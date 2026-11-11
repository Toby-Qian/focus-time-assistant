"""Qt desktop controller: sampling, refresh scheduling, classification actions, tray and reminders.

Sampling runs once per second. The interface is refreshed in tiers so that a
visible window costs little and a window hidden in the tray costs almost nothing:

* hidden / minimised: sample and account only, no widget work at all;
* every second: header, current activity and the four totals;
* every 5 s: today's charts and ranking; every 3 s: the activity table;
* on demand only: analysis (plus once a minute), rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import gc
import os
import logging
from pathlib import Path
import dataclasses
import re
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QByteArray, QDate, QSize, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QMessageBox,
    QDialog, QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect)
from core import Activity, Accountant, CATEGORIES, IGNORE, LABELS, OWN_WINDOW_MODES, RULE_TARGETS, Store, SITES, classify, hostname, load_config, save_config, streaks
from core import SURFACES, SURFACE_TARGETS, SURFACE_PREFIX
from insights import insights
from bridge import BrowserState, start_bridge
from theme import ACCENT, COLORS, INK, P, TEXTS, THEMES, TINTS, MUTED, SUBTLE, app_name, icon
from charts import focus_ratio, short_time, share
from ui_qt import Shell, CategoryDelegate, IconLabel, Segmented, text, button, restyle, set_text, fade_in
import theme
import windows
import appicons

SAMPLE_MS = 1000
SAMPLE_SECONDS = SAMPLE_MS/1000
OVERVIEW_CHARTS_EVERY = 5
MANAGER_EVERY = 3
ANALYSIS_EVERY = 60
TRAY_TIP_EVERY = 30
COMMIT_EVERY = 10  # seconds of sampled time per database commit; quitting commits at once
TRIM_EVERY_MS = 10*60*1000
MESSAGES = {"work": "正在计入工作，保持节奏。", "distraction": "正在计入摸鱼，记得回到手上的事。",
            "system": "正在计入系统工具（资源管理器、任务管理器等），不影响专注率。",
            "idle": "暂时没有操作，正在记录离开时间。", "unknown": "还不知道这算什么，点右侧“调整分类”告诉我。",
            IGNORE: "设为不计入，这段时间不记录。"}
OWN_PID = os.getpid()
from core import AWAY_LABEL  # stored app name of away time the user counted as work


def popup_menu(parent):
    """A context / drop-down menu that frees itself once closed."""
    menu = QMenu(parent); menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return menu


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"


class Reminder(QWidget):
    SHADOW = 30  # margin around the card used for the hand-painted shadow

    """Non-modal reminder centred on the screen the user is working on; never takes keyboard focus."""
    def __init__(self, owner, activity):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self); outer.setContentsMargins(self.SHADOW, self.SHADOW-8, self.SHADOW, self.SHADOW+8)
        body = QFrame(); body.setObjectName("card"); outer.addWidget(body)
        body.setStyleSheet(f"QFrame#card{{background:{P['card']};border:1px solid {P['line']};border-top:5px solid {COLORS['distraction']};border-radius:16px;}}")
        layout = QVBoxLayout(body); layout.setContentsMargins(30, 26, 30, 24); layout.setSpacing(12)
        target = activity.site or activity.host or app_name(activity.app)
        top = QHBoxLayout(); top.setSpacing(14)
        badge = QFrame(); badge.setFixedSize(52, 52); badge.setStyleSheet(f"background:{TINTS['distraction']};border-radius:14px;")
        inner = QHBoxLayout(badge); inner.setContentsMargins(12, 12, 12, 12); inner.addWidget(IconLabel(icon("distraction", COLORS["distraction"], 28), 28))
        top.addWidget(badge)
        titles = QVBoxLayout(); titles.setSpacing(2)
        head = text(f"专注提醒 · {target}"); head.setStyleSheet(f"color:{TEXTS['distraction']};font-size:13px;font-weight:600;"); titles.addWidget(head)
        title = text("把注意力带回手上的事"); title.setStyleSheet("font-size:22px;font-weight:700;"); titles.addWidget(title)
        top.addLayout(titles, 1); layout.addLayout(top)
        minutes = owner.config["learning_minutes"]
        note = text(owner.distraction_note() or f"这段时间正计入摸鱼。如果还想继续，接下来 {minutes} 分钟不再提醒，时间照常算摸鱼。", wrap=True)
        note.setStyleSheet(f"font-size:15px;color:{P['body']};"); layout.addWidget(note)
        today = owner.store.totals(datetime.now().date().isoformat())
        limit = owner.config["distraction_limit_minutes"]*60
        stats = f"今天已工作 {short_time(today['work'])} · 摸鱼 {short_time(today['distraction'])}" + (f"（上限 {short_time(limit)}）" if limit else "")
        layout.addWidget(text(stats, "muted"))
        row = QHBoxLayout(); row.setSpacing(10); row.addStretch()
        if activity.site:
            row.addWidget(button(f"继续摸鱼 {minutes} 分钟", lambda: owner.allow_learning(activity.site or activity.app)))
        ok = button("知道了，回去工作", owner.dismiss_reminder, True); ok.setMinimumHeight(38); row.addWidget(ok)
        layout.addSpacing(4); layout.addLayout(row)
        self.setFixedWidth(560); self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x()-self.width()//2, area.center().y()-self.height()//2-40)
        self.timeout = QTimer(self); self.timeout.setSingleShot(True); self.timeout.timeout.connect(owner.dismiss_reminder); self.timeout.start(25000)


    def paintEvent(self, _):
        """Soft drop shadow painted as layered rounded rectangles. QGraphicsDropShadowEffect would
        render the whole card, text included, through a low-resolution pixmap and blur it."""
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.layout().itemAt(0).widget().geometry()).translated(0, 5)
        layers = 18
        for step in range(layers, 0, -1):
            alpha = (3.2 if P["dark"] else 1.6)*(1-step/(layers+1))  # fades out smoothly towards the edge
            p.setBrush(QColor(*P["shadow"], max(1, round(alpha))))
            p.drawRoundedRect(card.adjusted(-step, -step*.5, step, step), 16+step, 16+step)


class FocusDialog(QDialog):
    """Start a focus session: optional task name (recent ones offered) and a length."""
    PRESETS = (15, 25, 45, 60, 90)

    def __init__(self, parent, minutes, tasks, rest_minutes):
        super().__init__(parent)
        self.setWindowTitle("开始专注"); self.setMinimumWidth(460)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        layout.addWidget(text("这一段要做什么？", "heading"))
        layout.addWidget(text("任务名可以不填；填了会出现在专注记录、悬浮计时和导出的报告里。", "muted", wrap=True))
        form = QFormLayout(); form.setSpacing(12)
        self.task = QComboBox(); self.task.setEditable(True); self.task.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.task.addItems(tasks); self.task.setCurrentIndex(-1); self.task.setCurrentText("")
        self.task.lineEdit().setPlaceholderText("例如：论文第三节、复习高数"); self.task.lineEdit().setMaxLength(40)
        self.task.lineEdit().returnPressed.connect(self.accept)
        presets = sorted(set(self.PRESETS) | {minutes})
        self.length = Segmented([(f"{m} 分钟", m) for m in presets]); self.length.setCurrentIndex(self.length.findData(minutes))
        form.addRow("任务", self.task); form.addRow("时长", self.length); layout.addLayout(form)
        layout.addWidget(text(f"完成后休息 {rest_minutes} 分钟，再提醒你开始下一段。" if rest_minutes else
                              "完成后不安排休息（可在“规则与设置 → 目标、专注与启动”里设置）。", "small", wrap=True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        start = buttons.button(QDialogButtonBox.StandardButton.Ok); start.setText("开始专注"); start.setProperty("role", "primary"); start.setDefault(True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.task.setFocus()

    def values(self):
        return self.length.currentData(), self.task.currentText().strip()[:40]


class FocusFloat(QWidget):
    """Small always-on-top timer shown during focus and break: progress ring, countdown, task.

    Drag to move (the position is remembered), double-click to open the main window, right-click
    for actions. It never takes keyboard focus from the window being worked in.
    """
    W, H, M = 248, 74, 14

    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.owner = owner; self.state = None; self.drag = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground); self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if owner.testing: self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.setFixedSize(self.W+2*self.M, self.H+2*self.M); self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动可移动 · 双击打开主窗口 · 右键更多")

    def set_state(self, mode, left, planned, task, category):
        state = (mode, int(left), int(planned), task, category)
        if state != self.state: self.state = state; self.update()

    def background(self):
        """Shadow, card and ring track, painted once per pixel ratio."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen, QPixmap
        ratio = self.devicePixelRatioF()
        if getattr(self, "_base", None) is not None and self._base.devicePixelRatio() == ratio:
            return self._base
        pix = QPixmap(round(self.width()*ratio), round(self.height()*ratio)); pix.setDevicePixelRatio(ratio); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.M, self.M, self.W, self.H)
        for step in range(10, 0, -1):  # soft hand-painted shadow; a graphics effect would blur the text
            p.setBrush(QColor(*P["shadow"], max(1, round((5 if P["dark"] else 2.4)*(1-step/11)))))
            p.drawRoundedRect(card.adjusted(-step, -step*.4, step, step*1.2), 18+step, 18+step)
        p.setBrush(QColor(P["card"])); p.setPen(QPen(QColor(P["line"]), 1)); p.drawRoundedRect(card, 18, 18)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor(P["track"]), 5)); p.drawEllipse(self.ring())
        p.end(); self._base = pix
        return pix

    def ring(self):
        from PySide6.QtCore import QRectF
        return QRectF(self.M+16, self.M+(self.H-44)/2, 44, 44)

    def place(self):
        screens = QApplication.screens()
        try:
            x, y = (int(v) for v in self.owner.config.get("float_pos", "").split(","))
            if any(s.availableGeometry().contains(x+self.width()//2, y+self.height()//2) for s in screens):
                self.move(x, y); return
        except ValueError:
            pass
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right()-self.width()-12, area.bottom()-self.height()-12)

    def paintEvent(self, _):
        if not self.state: return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen, QFont
        from theme import font
        mode, left, planned, task, category = self.state
        p = QPainter(self); p.drawPixmap(0, 0, self.background())
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        card = QRectF(self.M, self.M, self.W, self.H)
        colour = QColor(ACCENT if mode == "focus" else COLORS["work"])
        ring = self.ring()
        done = 1-left/planned if planned else 0
        if done > 0:
            pen = QPen(colour, 5); pen.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(pen)
            p.drawArc(ring, 90*16, -round(360*16*min(1, done)))
        icon("focus" if mode == "focus" else "coffee", colour.name(), 18).paint(p, int(ring.center().x()-9), int(ring.center().y()-9), 18, 18)
        x = ring.right()+14; width = card.right()-x-14
        p.setPen(QColor(INK)); p.setFont(font(24, QFont.Weight.DemiBold, "num"))
        clock = f"{left//60:02d}:{left%60:02d}" if left < 6000 else f"{left//3600}:{left//60%60:02d}:{left%60:02d}"
        p.drawText(QRectF(x, card.y()+10, width, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clock)
        if mode == "focus" and category == "distraction":
            line, tone = "正在摸鱼，回到任务吧", TEXTS["distraction"]
        elif mode == "focus":
            line, tone = task or "专注中", MUTED
        else:
            line, tone = "休息一下 · 站起来走走", TEXTS["work"]
        p.setPen(QColor(tone)); p.setFont(font(12))
        p.drawText(QRectF(x, card.y()+42, width, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, int(width)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint()-self.pos(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            self.menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self.drag is not None: self.move(event.globalPosition().toPoint()-self.drag)

    def mouseReleaseEvent(self, event):
        if self.drag is not None:
            self.drag = None; self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.owner.config["float_pos"] = f"{self.x()},{self.y()}"; save_config(self.owner.config_path, self.owner.config)

    def mouseDoubleClickEvent(self, _):
        self.owner.bring_forward()

    def menu(self, where):
        owner = self.owner; menu = QMenu()
        menu.addAction("打开主窗口", owner.bring_forward)
        if owner.focus:
            menu.addAction("提前结束专注", lambda: owner.finish_focus(completed=False))
        elif owner.rest_break:
            menu.addAction("开始下一段专注", lambda: owner.start_focus(task=owner.rest_break["task"]))
            menu.addAction("结束休息", owner.end_break)
        menu.addSeparator(); menu.addAction("不再显示悬浮计时", owner.hide_float_for_good)
        menu.exec(where)


class ReturnPrompt(QWidget):
    """Small card in the corner after a long time away: count it as work, or keep it as away."""
    def __init__(self, owner, began, ended):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating); self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if owner.testing: self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.owner, self.began, self.ended = owner, began, ended
        outer = QVBoxLayout(self); outer.setContentsMargins(14, 10, 14, 18)
        body = QFrame(); body.setObjectName("card"); outer.addWidget(body)
        layout = QVBoxLayout(body); layout.setContentsMargins(18, 16, 18, 14); layout.setSpacing(6)
        minutes = round((ended-began).total_seconds()/60)
        layout.addWidget(text(f"欢迎回来，刚才离开了 {minutes} 分钟", "heading"))
        layout.addWidget(text(f"{began:%H:%M} – {ended:%H:%M} 如果是在开会、看纸质资料或打电话，可以算作工作。", "muted", wrap=True))
        row = QHBoxLayout(); row.setSpacing(8); row.addStretch()
        row.addWidget(button("保持离开", self.close)); row.addWidget(button("算作工作", self.count_as_work, True))
        layout.addLayout(row); self.setFixedWidth(380)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.close); self.timer.start(90_000)
        self.adjustSize()
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right()-self.width()-8, area.bottom()-self.height()-8)

    def count_as_work(self):
        owner, began, ended = self.owner, self.began, self.ended
        self.close(); owner.count_away_as_work(began, ended)

    def paintEvent(self, _):
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRectF
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.layout().itemAt(0).widget().geometry()).translated(0, 3)
        for step in range(10, 0, -1):  # hand-painted shadow, text stays crisp
            p.setBrush(QColor(*P["shadow"], max(1, round((3 if P["dark"] else 1.8)*(1-step/11)))))
            p.drawRoundedRect(card.adjusted(-step, -step*.5, step, step), 12+step, 12+step)


class RuleDialog(QDialog):
    def __init__(self, parent, key="", domain=False, category="work"):
        super().__init__(parent)
        self.setWindowTitle("调整分类规则"); self.setMinimumWidth(440)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        layout.addWidget(text("以后怎样记录它", "heading"))
        layout.addWidget(text("规则从保存起生效，网站规则包含子域名。已经记下的时间请在“活动管理”里修正。", "muted", wrap=True))
        form = QFormLayout(); form.setSpacing(12)
        self.kind = QComboBox(); self.kind.addItems(["应用进程名", "网站域名"]); self.kind.setCurrentIndex(1 if domain else 0)
        self.key = QLineEdit(key); self.key.setPlaceholderText("例如 code.exe 或 bilibili.com")
        self.category = QComboBox()
        for k in RULE_TARGETS: self.category.addItem(LABELS[k], k)
        self.category.setCurrentIndex(max(0, self.category.findData(category)))
        form.addRow("规则类型", self.kind); form.addRow("应用 / 域名", self.key); form.addRow("记录为", self.category); layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存规则"); buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def accept(self):
        key = self.key.text().strip().lower()
        if self.kind.currentIndex() == 1:
            key = hostname(key)
            valid = bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", key)) and "." in key and ".." not in key
        else:
            valid = key.endswith(".exe") and not any(c in key for c in "/\\:\n\r")
        if not valid:
            QMessageBox.information(self, "请检查格式", "网站请输入有效域名，应用请输入以 .exe 结尾的进程名。")
            return
        self.key.setText(key)
        super().accept()


class App(Shell):
    def __init__(self, data_dir: Path, project: Path, testing=False):
        super().__init__()
        self.project = project; self.data_dir = data_dir; self.testing = testing
        self.config_path = data_dir/"settings.json"; self.config = load_config(self.config_path)
        self.store = Store(data_dir/"activity.sqlite3"); self.accountant = Accountant(self.store)
        appicons.load(data_dir/"app_paths.json", data_dir/"site_icons")
        self.icon_version = 0; self.paths_seen = windows.paths_version
        self.browser = BrowserState(); self.server = None
        self.browser.icon_dir = data_dir/"site_icons"
        try:
            self.server = start_bridge(self.browser, self.config["token"], port=0 if testing else 17863)
        except OSError:
            logging.exception("Browser bridge unavailable")
        self.paused = False; self.stopping = False; self.current = Activity("unknown"); self.learning = {}; self.reminded = {}
        self.popup = None; self.popup_site = ""; self.manager_rows = []; self.manager_signature = None; self.rule_entries = []
        self.ticks = 0; self.own_window = False; self.skipped = False; self.background = ""; self.tray_hinted = False; self.last_error = ""
        self.streak_key = None; self.streak_start = time.monotonic(); self.compare = {}
        self.focus = None; self.goal_flags = {}; self.active_run = 0.0
        self.rest_break = None; self.floater = None; self.pause_until = None; self.streak_cache = {}
        self.tray_state = None
        self.away_since = None; self.return_prompt = None; self.idle_now = False  # start of the current away stretch (wall clock)
        # A summary time already passed when the app starts is not announced late.
        at = self.config["daily_summary"]
        self.summary_day = datetime.now().date().isoformat() if at != "off" and datetime.now().strftime("%H:%M") >= at else None
        startup = Path(os.environ.get("APPDATA", str(Path.home())))/"Microsoft"/"Windows"/"Start Menu"/"Programs"/"Startup"
        self.startup_link = (data_dir/"startup_test" if testing else startup)/"专注时间助手.lnk"
        self.build_tray(); self.build()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_menu)
        self.table.setIconSize(QSize(18, 18)); self.rules_table.setIconSize(QSize(18, 18))
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self.toggle_pause)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.focus_search)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.undo_shortcut)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(lambda: self.focus_clicked() if self.focus or self.rest_break else self.start_focus())
        for index in range(5):
            QShortcut(QKeySequence(f"Ctrl+{index+1}"), self).activated.connect(lambda i=index: self.navigate(i))
        self.restore_window()
        self.preferences_timer = QTimer(self); self.preferences_timer.setSingleShot(True); self.preferences_timer.setInterval(600); self.preferences_timer.timeout.connect(self.save_preferences)
        self.trim_timer = QTimer(self); self.trim_timer.setInterval(TRIM_EVERY_MS); self.trim_timer.timeout.connect(self.trim_if_hidden)
        self.timer = QTimer(self); self.timer.setInterval(SAMPLE_MS); self.timer.timeout.connect(self.tick)
        if not testing:
            self.timer.start(); self.trim_timer.start(); QTimer.singleShot(50, self.tick)

    # ── tray and window ───────────────────────────────────────────────
    def build_tray(self):
        self.tray = QSystemTrayIcon(icon("logo", ACCENT, 32), self)
        self.tray.setToolTip("专注时间助手 · 正在记录")
        menu = QMenu()
        menu.addAction("打开专注时间助手", self.bring_forward)
        self.tray_focus = menu.addAction("开始专注", self.tray_focus_action)
        self.tray_pause = menu.addAction("暂停记录", self.toggle_pause)
        later = menu.addMenu("暂停一段时间")
        for minutes in (15, 30, 60): later.addAction(f"{minutes} 分钟", lambda m=minutes: self.pause_for(m))
        menu.addSeparator(); menu.addAction("退出程序", self.quit_requested)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.bring_forward() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if not self.testing: self.tray.show()
        self.tray_menu = menu

    def bring_forward(self):
        if self.isMinimized(): self.showNormal()
        else: self.show()
        self.raise_(); self.activateWindow()

    def ui_live(self):
        return self.isVisible() and not self.isMinimized()

    def focus_of(self, totals):
        return focus_ratio(totals, self.config["focus_includes_unknown"])

    def choose_theme(self, key):
        if key == self.config["theme"]: return
        self.config["theme"] = key; save_config(self.config_path, self.config)
        self.notify(f"已选择“{THEMES[key]}”主题，重启后生效。", self.restart_app, "立即重启", seconds=12)
        self.theme_restart.setVisible(key != theme.THEME)

    def restart_app(self):
        """Start a fresh copy that waits for this process to exit, then quit."""
        args = [a for a in sys.argv[1:] if a not in ("--start-hidden",)]
        if "--wait-pid" in args:
            index = args.index("--wait-pid"); del args[index:index+2]
        args += ["--wait-pid", str(os.getpid())]
        if getattr(sys, "frozen", False):
            command = [sys.executable, *args]
        else:
            command = [str(self.project/".venv"/"Scripts"/"pythonw.exe"), str(self.project/"app"/"main.py"), *args]
        try:
            subprocess.Popen(command, cwd=str(self.project), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except OSError:
            logging.exception("Restart failed"); self.notify("未能自动重启，请从托盘退出后重新打开。"); return
        self.quit_requested()

    def showEvent(self, event):
        super().showEvent(event)
        if P.get("dark") and not getattr(self, "_dark_title", False):
            self._dark_title = True; windows.dark_title_bar(int(self.winId()))
        if hasattr(self, "store"): QTimer.singleShot(0, lambda: self.refresh(force=True))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and hasattr(self, "store"):
            if self.isMinimized(): QTimer.singleShot(2000, self.trim_if_hidden)
            else: QTimer.singleShot(0, lambda: self.refresh(force=True))

    def trim_if_hidden(self):
        if not self.ui_live():
            gc.collect(); windows.trim_memory()

    def notify(self, message, action=None, label="撤销", seconds=6):
        if hasattr(self, "toast"): self.toast.show_message(message, action, label, seconds)

    def tray_message(self, title, message, kind=QSystemTrayIcon.MessageIcon.Information, ms=5000):
        """System notification only while the window is not on screen; the toast covers the visible case."""
        if not self.ui_live() and not self.testing: self.tray.showMessage(title, message, kind, ms)

    def undo_shortcut(self):
        if self.pages.currentIndex() == 2 and self.store.can_undo(): self.undo_correction()

    def restore_window(self):
        if self.config.get("window"):
            self.restoreGeometry(QByteArray.fromHex(self.config["window"].encode()))
        if self.config.get("page"): self.navigate(self.config["page"])
        if self.config.get("rhythm_view"): self.rhythm_mode.setCurrentIndex(self.config["rhythm_view"])

    def remember_window(self):
        state = bytes(self.saveGeometry().toHex()).decode(); page = self.pages.currentIndex()
        if (state, page) != (self.config.get("window"), self.config.get("page")):
            self.config["window"] = state; self.config["page"] = page; save_config(self.config_path, self.config)

    def focus_search(self):
        self.navigate(2); self.search.setFocus(); self.search.selectAll()

    # ── sampling ──────────────────────────────────────────────────────
    def settle(self):
        self.accountant.tick(datetime.now(), time.monotonic(), None)
        self.accountant.previous = None

    def tick(self):
        if self.stopping: return
        try:
            mono = time.monotonic()
            if self.paused and self.pause_until and mono >= self.pause_until:
                self.toggle_pause(); self.notify("暂停时间到，已自动继续记录。")
                self.tray_message("已继续记录", "暂停时间到，已自动继续计时与提醒。")
            mode = self.config["own_window"]
            app, title = windows.foreground(OWN_PID if mode == "skip" else None); age = windows.idle_age(); locked = windows.locked()
            idle = locked or age >= self.config["idle_seconds"]
            host = self.browser.current(app, title)
            current = classify(app, title, host, self.config, idle=idle)
            self.own_window = not idle and windows.last_pid == OWN_PID
            self.skipped = not idle and windows.last_skipped
            self.background = "" if idle else windows.last_background
            if self.own_window:
                # This program's own window: recorded as the category chosen in the settings.
                category = mode if mode in LABELS else "idle"
                current = Activity(category, "专注时间助手.exe", source=f"本程序窗口，{OWN_WINDOW_MODES.get(category, '记为离开')}")
            elif self.background:
                # Desktop, taskbar, Start menu, hidden or minimised windows: not counted unless a rule says so.
                category = self.config["surface_rules"].get(self.background, IGNORE)
                current = Activity(category, SURFACE_PREFIX+self.background if category != IGNORE else app,
                                   source=f"系统界面，{'不计时' if category == IGNORE else '记为'+LABELS[category]}（可调整）")
            if self.skipped and not self.background:
                current = dataclasses.replace(current, source=current.source+" · 已略过本程序窗口")
            self.idle_now = idle and not self.paused
            self.track_away(current, age, locked)
            self.current = current
            key = None if self.paused else (current.category, current.app, current.host or current.site, self.background, self.own_window)
            if key != self.streak_key: self.streak_key = key; self.streak_start = mono
            self.expire_learning(mono)
            idle_part = SAMPLE_SECONDS if locked else max(0, age-self.config["idle_seconds"])
            self.accountant.tick(datetime.now(), mono, None if self.paused else current, idle_part)
            if not self.paused and current.category == "distraction": self.maybe_remind(mono)
            if self.popup and (self.paused or current.category != "distraction" or (current.site or current.app) != self.popup_site): self.dismiss_reminder()
            if self.last_error:
                self.last_error = ""; self.refresh_header()
            if self.focus and mono-self.focus["mono"] >= self.focus["planned"]: self.finish_focus(completed=True)
            if self.rest_break and mono-self.rest_break["mono"] >= self.rest_break["planned"]: self.end_break(done=True)
            self.update_float()
            self.track_rest(current)
            self.ticks += 1
            if self.ticks % 5 == 0: self.check_goals()
            if self.ticks % COMMIT_EVERY == 0: self.store.flush()
            if self.ticks % 30 == 0: self.check_summary()
            if self.ticks % 60 == 0: appicons.save()
            if windows.paths_version != self.paths_seen:  # a program path was learned: rebuild rows for its icon and name
                self.paths_seen = windows.paths_version; self.manager_signature = None
            if self.browser.icon_version != self.icon_version:  # a site icon arrived from the extension
                self.icon_version = self.browser.icon_version; appicons.clear(); self.manager_signature = None
            self.update_tray_icon()
            if self.ticks % TRAY_TIP_EVERY == 0: self.update_tray_tip()
            self.refresh()
        except Exception:
            logging.exception("Sampling failed"); self.accountant.previous = None
            if not self.last_error:
                self.last_error = "采样暂时失败，此段未计时。"; self.notify(self.last_error)
            self.sidebar_status.setText("●  记录异常")

    def expire_learning(self, mono):
        for site, until in list(self.learning.items()):
            if until <= mono:
                self.learning.pop(site); self.notify(f"继续摸鱼的 {self.config['learning_minutes']} 分钟到了，{app_name(site)} 会照常提醒。")

    def track_away(self, current, age, locked):
        """Remember when an away stretch began; when the user returns after a long one, ask once."""
        away = self.idle_now
        if away and self.away_since is None:
            self.away_since = datetime.now()-timedelta(seconds=0 if locked else max(0, age-self.config["idle_seconds"]))
        elif not away and self.away_since is not None:
            began, self.away_since = self.away_since, None
            threshold = self.config["return_prompt_minutes"]*60; ended = datetime.now()
            if threshold and not self.paused and (ended-began).total_seconds() >= threshold:
                self.close_return_prompt()
                self.return_prompt = ReturnPrompt(self, began, ended)
                # The card deletes itself when closed; drop the reference with it.
                self.return_prompt.destroyed.connect(lambda *_: setattr(self, "return_prompt", None))
                self.return_prompt.show()

    def close_return_prompt(self):
        import shiboken6
        if self.return_prompt is not None and shiboken6.isValid(self.return_prompt): self.return_prompt.close()
        self.return_prompt = None

    def count_away_as_work(self, began, ended):
        self.settle()
        moved = self.store.move_minutes(began, ended, ("idle", "", ""), ("work", AWAY_LABEL, ""))
        def undo():
            self.settle(); self.store.move_minutes(began, ended, ("work", AWAY_LABEL, ""), ("idle", "", ""))
            self.notify("已改回离开。"); self.manager_signature = None; self.refresh(force=True)
        self.notify(f"已把离开的 {short_time(moved)} 算作工作", undo, seconds=8)
        self.manager_signature = None; self.refresh(force=True)

    def check_app_limits(self, today):
        """One reminder per program / website per day when its own daily limit is reached."""
        for key, minutes in self.config["app_limits"].items():
            flag = "app:"+key
            if self.goal_flags.get(flag): continue
            used = self.store.used_today(today, key)
            if used >= minutes*60:
                self.goal_flags[flag] = True; name = app_name(key)
                self.notify(f"{name} 今天已用 {short_time(used)}，到了你设的 {minutes} 分钟上限。", label="", seconds=8)
                self.tray_message(f"{name} 到上限了", f"今天已用 {short_time(used)}，上限 {minutes} 分钟。", QSystemTrayIcon.MessageIcon.Warning, 6000)
                if self.config["reminder_sound"]: self.beep()

    def set_app_limit(self, key):
        """Ask for a daily limit in minutes for a program or website; 0 removes it."""
        from PySide6.QtWidgets import QInputDialog
        current = self.config["app_limits"].get(key, 0)
        minutes, ok = QInputDialog.getInt(self, "每日上限", f"“{app_name(key)}”每天最多用多少分钟？\n到了提醒一次，填 0 取消上限。", current, 0, 1440, 5)
        if not ok or minutes == current: return
        if minutes: self.config["app_limits"][key] = minutes
        else: self.config["app_limits"].pop(key, None)
        self.goal_flags.pop("app:"+key, None); save_config(self.config_path, self.config); self.refresh_rules()
        self.notify(f"{app_name(key)} 每日上限：{minutes} 分钟。" if minutes else f"已取消 {app_name(key)} 的每日上限。")

    def update_tray_icon(self):
        """The tray icon takes the colour of what is being recorded, so a glance at the taskbar is enough."""
        state = "paused" if self.paused else "focus" if self.focus else ("idle" if self.current.category == IGNORE else self.current.category)
        if state == self.tray_state: return
        self.tray_state = state
        colour = {"paused": P["subtle"], "focus": ACCENT}.get(state) or COLORS.get(state, ACCENT)
        self.tray.setIcon(icon("logo", colour, 32))

    def check_summary(self):
        at = self.config["daily_summary"]; now = datetime.now(); today = now.date().isoformat()
        if at == "off" or self.summary_day == today or now.strftime("%H:%M") < at: return
        self.summary_day = today
        totals = self.store.totals(today); goal = self.config["work_goal_minutes"]*60
        text = f"工作 {short_time(totals['work'])}，摸鱼 {short_time(totals['distraction'])}"
        if goal: text += "，已完成今天的目标。" if totals["work"] >= goal else f"，离 {short_time(goal)} 目标还差 {short_time(goal-totals['work'])}。"
        else: text += "。"
        used = [(seconds, app, host) for cat, app, host, seconds in self.store.details(today) if cat not in ("idle",) and (app or host)]
        if used:
            _, app, host = max(used); text += f"用得最多：{host or app_name(app)}。"
        self.notify("今日总结 · "+text, seconds=10)
        self.tray_message("今日总结", text, QSystemTrayIcon.MessageIcon.Information, 8000)

    def backup_data(self):
        """Save a complete, consistent copy of the record database (SQLite online backup)."""
        from PySide6.QtWidgets import QFileDialog
        import sqlite3
        base = self.project/"exports"; base.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "备份记录数据", str(base/f"专注时间备份_{datetime.now():%Y%m%d_%H%M}.sqlite3"), "记录数据库 (*.sqlite3)")
        if not path: return
        self.settle(); self.store.flush()
        try:
            target = sqlite3.connect(path); self.store.db.backup(target); target.close()
        except (OSError, sqlite3.Error):
            logging.exception("Backup failed"); QMessageBox.warning(self, "未能备份", "这个位置可能不能写入，请换一个文件夹再试。"); return
        folder = Path(path).parent
        self.notify(f"已备份到 {Path(path).name}", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))), "打开文件夹", seconds=8)

    def open_data_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir)))

    def update_tray_tip(self):
        totals = self.store.totals(datetime.now().date().isoformat())
        state = "已暂停" if self.paused else "正在记录"
        self.tray.setToolTip(f"专注时间助手 · {state}\n今日工作 {short_time(totals['work'])} · 摸鱼 {short_time(totals['distraction'])}")

    # ── refresh ───────────────────────────────────────────────────────
    def refresh(self, force=False):
        # Delayed refreshes (window shown / restored) can arrive after shutdown closed the database.
        if self.stopping or (not force and not self.ui_live()): return
        self.refresh_header()
        index = self.pages.currentIndex()
        if index == 0: self.refresh_overview(force or self.ticks % OVERVIEW_CHARTS_EVERY == 0)
        elif index == 1 and (force or self.ticks % ANALYSIS_EVERY == 0): self.refresh_analysis()
        elif index == 2 and (force or self.ticks % MANAGER_EVERY == 0): self.refresh_manager()
        elif index == 3 and force: self.refresh_rules()
        elif index == 4: self.refresh_connection()

    def refresh_header(self):
        today = datetime.now()
        set_text(self.date_label, f"{today.year}年{today.month}月{today.day}日 周{'一二三四五六日'[today.weekday()]}")
        if self.pause_button.property("paused") != self.paused:
            self.pause_button.setProperty("paused", self.paused)
            self.pause_button.setText("继续记录" if self.paused else "暂停记录")
            self.pause_button.setProperty("role", "primary" if self.paused else None)
            self.pause_button.setIcon(icon("play" if self.paused else "pause", P["on_accent"] if self.paused else ACCENT, 16))
            self.pause_button.style().unpolish(self.pause_button); self.pause_button.style().polish(self.pause_button)
            self.tray_pause.setText("继续记录" if self.paused else "暂停记录")
        left = self.focus_left(); mode = "focus" if self.focus else "break" if self.rest_break else ""
        clock = lambda s: duration(s)[3:] if s < 3600 else duration(s)
        label = f"专注中 {clock(left)}" if mode == "focus" else f"休息中 {clock(self.break_left())}" if mode == "break" else "开始专注"
        set_text(self.focus_button, label)
        if self.focus_button.property("active") != mode:
            self.focus_button.setProperty("active", mode)
            self.focus_button.setProperty("role", "primary" if mode == "focus" else None)
            self.focus_button.setIcon(icon("coffee" if mode == "break" else "focus", P["on_accent"] if mode == "focus" else ACCENT, 16))
            self.focus_button.setToolTip({"focus": "点击可提前结束本次专注", "break": "点击可跳过休息、开始下一段"}.get(mode, "选择任务和时长后开始专注（Ctrl+Shift+F 按默认时长直接开始）"))
            self.focus_button.style().unpolish(self.focus_button); self.focus_button.style().polish(self.focus_button)
            self.tray_focus.setText({"focus": "结束专注", "break": "开始下一段专注"}.get(mode, "开始专注"))
        if not self.last_error and self.focus and not self.paused:
            set_text(self.sidebar_status, f"●  专注中 · 剩余 {duration(left)[3:]}")
            restyle(self.sidebar_status, f"color:{ACCENT};font-size:12px;")
        elif not self.last_error:
            resume = max(0, (self.pause_until or 0)-time.monotonic())
            set_text(self.sidebar_status, (f"●  已暂停 · {max(1, round(resume/60))} 分钟后继续" if resume else "●  已暂停记录") if self.paused else "●  正在记录 · 每秒采样")
            restyle(self.sidebar_status, f"color:{MUTED if self.paused else TEXTS['work']};font-size:12px;")
        totals = self.store.totals(today.date().isoformat()); ratio = self.focus_of(totals)
        goal, limit = self.config["work_goal_minutes"]*60, self.config["distraction_limit_minutes"]*60
        set_text(self.side_work, short_time(totals["work"]))
        set_text(self.side_goal, f"目标 {short_time(goal)}" if goal else "")
        self.side_meter.set_ratio(totals["work"]/goal if goal else ratio or 0)
        current, best = self.goal_streak(goal)
        set_text(self.side_streak, (f"连续达标 {current} 天" + (f" · 最长 {best} 天" if best > current else "")) if best else "")
        sessions, _ = self.store.sessions(today.date().isoformat())
        focus_text = f"专注率 {ratio:.0%}" if ratio is not None else "专注率 —"
        set_text(self.side_focus, focus_text+(f" · 专注 {sessions} 次" if sessions else ""))
        if limit:
            over = totals["distraction"] > limit
            set_text(self.side_limit, f"摸鱼 {short_time(totals['distraction'])} / {short_time(limit)}")
            restyle(self.side_limit, f"color:{P['danger'] if over else SUBTLE};font-size:12px;")
        else:
            set_text(self.side_limit, "")

    def refresh_overview(self, charts):
        today = datetime.now().date().isoformat()
        totals = self.store.totals(today); total = sum(totals.values())
        for key, label in self.card_values.items():
            set_text(label, duration(totals[key])); set_text(self.card_shares[key], share(totals[key], total))
            self.stat_cards[key].bar.set_ratio(totals[key]/total if total else 0)
        current = self.current; category = "idle" if self.paused else current.category
        name = ("专注时间助手（本窗口）" if self.own_window else self.background if self.background else
                current.host or current.site or app_name(current.app) or "等待前台活动")
        set_text(self.now_label, "记录已暂停" if self.paused else name)
        self.now_label.setToolTip(current.app or "")
        set_text(self.running_badge, "已暂停" if self.paused else LABELS[current.category])
        restyle(self.running_badge, f"background:{TINTS[category]};color:{MUTED if self.paused else TEXTS[category]};")
        restyle(self.now_badge, f"background:{TINTS[category]};border-radius:12px;")
        subject = current.host or (current.app if current.app.endswith(".exe") else "")
        if self.paused or self.idle_now or category == IGNORE or self.background or self.own_window or not subject:
            self.now_icon.set_icon(icon("pause" if self.paused else category, MUTED if self.paused else COLORS[category], 26))
        else:
            self.now_icon.set_icon(appicons.icon_for(subject, category))
        set_text(self.source_label, "点击右上角“继续记录”恢复计时与提醒。" if self.paused else
                 f"正在查看本程序，这段时间{OWN_WINDOW_MODES.get(current.category, '记为离开')}。可点右侧调整。" if self.own_window else
                 "按规则把这个程序记为离开时间。" if current.category == "idle" and not self.idle_now else MESSAGES[current.category])
        snooze_key = current.site or current.app
        remaining = max(0, self.learning.get(snooze_key, 0)-time.monotonic()) if current.category == "distraction" else 0
        set_text(self.learning_label, f"继续摸鱼 · {duration(remaining)[3:]} 内不提醒" if remaining else current.source)
        streak = time.monotonic()-self.streak_start
        set_text(self.streak_label, "" if self.paused or streak < 5 else f"已持续 {short_time(streak) if streak >= 3600 else duration(streak)[3:]} ·")
        set_text(self.learning_button, f"继续摸鱼 {self.config['learning_minutes']} 分钟")
        self.learning_button.setVisible(current.category == "distraction" and not remaining and not self.paused and not self.own_window)
        self.cancel_learning_button.setVisible(bool(remaining) and not self.paused)
        self.classify_current.setEnabled(not self.paused and (self.own_window or bool(self.background) or (bool(current.app or current.host) and not self.idle_now)))
        if not charts: return
        self.refresh_deltas(totals)
        self.donut.set_data(totals)
        for key, label in self.legend_values.items(): set_text(label, share(totals[key], total))
        hourly = {str(i).zfill(2): dict.fromkeys(CATEGORIES, 0.0) for i in range(24)}
        for hour, category, seconds in self.store.hourly(today): hourly[hour][category] = seconds
        recorded = sum(sum(v.values()) for v in hourly.values())
        # Show only the part of the day that has happened: from an hour before the first record up to now.
        now_hour = datetime.now().hour
        used = [int(h) for h, v in hourly.items() if sum(v.values())]
        if self.config["hour_range"] == "full":
            first, last = 0, 23
        else:
            first = max(0, min(used+[now_hour])-1) if used else 0
            last = max(used+[now_hour])
        rows = [(f"{h:02d}", hourly[f"{h:02d}"]) for h in range(first, last+1)]
        self.hour_chart.set_data(rows if recorded else []); self.hour_chart.set_mark(now_hour-first if recorded else None)
        if self.rhythm_stack.currentIndex() == 1:
            minutes = [(m, cat, host or app_name(app) or "离开 / 锁屏", host or app) for m, cat, app, host in self.store.timeline(today)]
            self.timeline.set_data(minutes, (first, min(24, last+1)))
        line = self.rhythm_stack.currentIndex() == 1
        set_text(self.hour_note, "时段图只含升级后的分钟记录；旧版每日汇总仍完整保留在合计里。" if total-recorded > 3 else
                 "每一竖条是一分钟，颜色是那一分钟主要在做的事；悬停查看，点击查看明细。" if line else
                 "悬停柱子查看该小时各类时长；点击右侧环形图查看明细。")
        grouped = {}
        for cat, app, host, seconds in self.store.details(today):
            if cat == "idle": continue
            entry = grouped.setdefault((app, host), [0.0, cat, 0.0])
            entry[0] += seconds
            if seconds > entry[2]: entry[1], entry[2] = cat, seconds
        ranked = sorted(grouped.items(), key=lambda row: row[1][0], reverse=True)
        self.rank_list.set_data([(host or app_name(app), host or app, cat, seconds) for (app, host), (seconds, cat, _) in ranked])

    def switch_rhythm(self, index):
        """按小时 / 时间线; the choice is remembered."""
        self.rhythm_stack.setCurrentIndex(index); fade_in(self.rhythm_stack.currentWidget())
        if self.config.get("rhythm_view") != index:
            self.config["rhythm_view"] = index; save_config(self.config_path, self.config)
        self.refresh_overview(True)

    def refresh_deltas(self, totals):
        """Compare with yesterday up to the same clock time (minute records only)."""
        now = datetime.now(); stamp = now.strftime("%Y-%m-%d %H:%M")
        if self.compare.get("stamp") != stamp:
            yesterday = (now.date()-timedelta(days=1)).isoformat()
            self.compare = {"stamp": stamp, "totals": self.store.totals_until(yesterday, now.strftime("%H:%M"))}
        before = self.compare["totals"]
        for key, card in self.stat_cards.items():
            if before is None:
                set_text(card.delta, "昨天此时无记录"); restyle(card.delta, f"color:{SUBTLE};"); continue
            diff = totals[key]-before[key]
            if abs(diff) < 60:
                set_text(card.delta, "与昨天此时持平"); restyle(card.delta, f"color:{SUBTLE};"); continue
            good = {"work": diff > 0, "distraction": diff < 0}.get(key)
            colour = SUBTLE if good is None else TEXTS["work"] if good else P['danger']
            set_text(card.delta, f"比昨天此时{'多' if diff > 0 else '少'} {short_time(abs(diff))}")
            restyle(card.delta, f"color:{colour};")

    def refresh_analysis(self):
        count, back = self.range_combo.currentData()
        today = datetime.now().date(); last = today-timedelta(days=back); days = {}
        for i in range(count-1, -1, -1): days[(last-timedelta(days=i)).isoformat()] = dict.fromkeys(CATEGORIES, 0.0)
        for day, totals in self.store.day_totals(min(days), max(days)).items():
            if day in days: days[day].update(totals)
        self.trend_chart.set_data(list(days.items())); self.trend_chart.set_mark(count-1 if back == 0 else None)
        summed = {key: sum(x[key] for x in days.values()) for key in CATEGORIES}
        active = [d for d in days.values() if sum(d.values())]
        ratio = self.focus_of(summed)
        set_text(self.kpis["work"], short_time(summed["work"]))
        set_text(self.kpis["average"], short_time(summed["work"]/len(active)) if active else "—")
        set_text(self.kpis["distraction"], short_time(summed["distraction"]))
        set_text(self.kpis["focus"], f"{ratio:.0%}" if ratio is not None else "—")
        first_day, last_day = min(days), max(days)
        completed = sum(1 for row in self.store.session_list(first_day, last_day) if row[5])
        set_text(self.kpis["sessions"], f"{completed} 次")
        self.range_rank.set_data([(host or app_name(app), host or app, cat, seconds) for app, host, cat, seconds, _ in self.store.range_apps(first_day, last_day)])
        profile_days, profile = self.store.hour_profile(first_day, last_day)
        hours = {f"{h:02d}": dict.fromkeys(CATEGORIES, 0.0) for h in range(24)}
        for hour, key, seconds in profile: hours[hour][key] = seconds
        self.profile_chart.set_data(list(hours.items()) if profile else []); self.profile_chart.set_mark(None)
        set_text(self.profile_note, f"按有分钟记录的 {profile_days} 天平均；看看自己一般在什么时段专注、什么时段分心。" if profile_days else "这段时间还没有分钟记录。")
        self.refresh_focus_story(today, first_day, last_day)
        self.history_table.setRowCount(len(days))
        for row, (day, totals) in enumerate(reversed(list(days.items()))):
            date = datetime.fromisoformat(day)
            label = QTableWidgetItem(f"{date.month}月{date.day}日 " + ("今天" if day == today.isoformat() else "昨天" if (today-date.date()).days == 1 else "周"+"一二三四五六日"[date.weekday()]))
            label.setData(Qt.ItemDataRole.UserRole, day); self.history_table.setItem(row, 0, label)
            for col, key in enumerate(CATEGORIES, 1):
                item = QTableWidgetItem(duration(totals[key]) if totals[key] else "—")
                item.setForeground(QColor(TEXTS[key] if totals[key] else SUBTLE)); self.history_table.setItem(row, col, item)
            day_ratio = self.focus_of(totals)
            item = QTableWidgetItem(f"{day_ratio:.0%}" if day_ratio is not None else "—")
            item.setForeground(QColor(INK if day_ratio is not None else SUBTLE)); self.history_table.setItem(row, len(CATEGORIES)+1, item)
        # Show every day without an inner scrollbar; the page itself scrolls.
        self.history_table.setFixedHeight(self.history_table.horizontalHeader().height()+40*len(days)+4)

    def goal_streak(self, goal, force=False):
        """(current, best) goal streak; the sidebar recomputes it at most once a minute."""
        now = datetime.now(); stamp = (now.strftime("%Y-%m-%d %H:%M"), goal)
        if force or self.streak_cache.get("stamp") != stamp:
            today = now.date().isoformat(); first = (now.date()-timedelta(days=400)).isoformat()
            work = {day: totals["work"] for day, totals in self.store.day_totals(first, today).items()}
            self.streak_cache = {"stamp": stamp, "value": streaks(work, goal, today), "work": work}
        return self.streak_cache["value"]

    def refresh_focus_story(self, today, first_day, last_day):
        """Analysis extras: calendar heatmap, streak, plain-language findings and focus sessions."""
        goal = self.config["work_goal_minutes"]*60; current, best = self.goal_streak(goal, force=True)
        self.heatmap.set_data(self.streak_cache.get("work", {}), goal, today.isoformat())
        rule = f"达到每日目标 {short_time(goal)}" if goal else "工作满 30 分钟"
        set_text(self.streak_label, f"连续 {current} 天{rule} · 最长 {best} 天" if best else f"还没有{rule}的日子")
        findings = insights(self.store, first_day, last_day, self.config["focus_includes_unknown"])
        if findings:
            colours = {"distraction": TEXTS["distraction"], "rank": TEXTS["distraction"], "flame": TEXTS["work"]}
            html = "".join(f"<p style='margin:0 0 12px'><span style='color:{colours.get(kind, ACCENT)}'>●</span>&nbsp;&nbsp;{line}</p>" for kind, line in findings)
        else:
            html = f"<span style='color:{MUTED}'>记录满一两天后，这里会总结你最专注的时段、最大的分心来源，以及和上一段时间的对比。</span>"
        set_text(self.insight_label, html)
        rows = list(reversed(self.store.session_list(first_day, last_day)))[:30]
        self.session_table.setRowCount(len(rows))
        for row, (started, planned, seconds, work, play, done, task) in enumerate(rows):
            when = datetime.fromisoformat(started)
            values = [f"{when.month}月{when.day}日 {when:%H:%M}", task or "—", f"{round(seconds/60)} / {planned} 分钟",
                      f"工作 {short_time(work)} · 摸鱼 {short_time(play)}", "完成" if done else "提前结束"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 4: item.setForeground(QColor(TEXTS["work"] if done else SUBTLE))
                if col == 1 and not task: item.setForeground(QColor(SUBTLE))
                self.session_table.setItem(row, col, item)
        self.session_table.setVisible(bool(rows)); self.session_empty.setVisible(not rows)
        self.session_table.setFixedHeight(self.session_table.horizontalHeader().height()+40*len(rows)+4)

    # ── activity management ───────────────────────────────────────────
    def open_manager(self, category="", day=None, query=""):
        for widget, apply in [(self.manage_date, lambda: self.manage_date.setDate(QDate.fromString(day or datetime.now().date().isoformat(), "yyyy-MM-dd"))),
                              (self.category_filter, lambda: self.category_filter.setCurrentIndex(max(0, self.category_filter.findData(category)))),
                              (self.search, lambda: self.search.setText(query))]:
            widget.blockSignals(True); apply(); widget.blockSignals(False)
        self.manager_signature = None; self.navigate(2)

    def selected_keys(self):
        result = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item: result.append(tuple(item.data(Qt.ItemDataRole.UserRole)))
        return result

    def refresh_manager(self, force=False):
        day = self.manage_date.date().toString("yyyy-MM-dd"); query = self.search.text().strip().lower(); category = self.category_filter.currentData()
        rows = []
        for row in self.store.details(day):
            cat, app, host, seconds = row
            if category and cat != category: continue
            if query and query not in f"{app} {host} {app_name(app)}".lower(): continue
            rows.append(row)
        total = sum(self.store.totals(day).values())
        keys = tuple((c, a, h) for c, a, h, _ in rows)
        signature = (day, query, category, keys)
        if not force and signature == self.manager_signature:
            # Same rows in the same order: update only the changing time cells.
            for index, (_, _, _, seconds) in enumerate(rows):
                for column, value in ((3, duration(seconds)), (4, f"{seconds/total:.1%}" if total else "—")):
                    item = self.table.item(index, column)
                    if item.text() != value: item.setText(value)
            self.manager_rows = rows
            self.undo_button.setEnabled(self.store.can_undo())
            return
        self.manager_signature = signature
        selected = set(self.selected_keys()); scroll = self.table.verticalScrollBar().value()
        self.table.setUpdatesEnabled(False); self.table.blockSignals(True)
        self.table.clearSelection(); self.table.setRowCount(len(rows)); self.manager_rows = rows
        selection = self.table.selectionModel()
        for row, (cat, app, host, seconds) in enumerate(rows):
            values = [CATEGORIES[cat], app_name(app) or "离开 / 锁屏", host or "—", duration(seconds), f"{seconds/total:.1%}" if total else "—"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, (cat, app, host)); item.setData(CategoryDelegate.ROLE, cat)
                elif column == 1:
                    item.setToolTip(app or "离开 / 锁屏")
                    if app: item.setIcon(appicons.icon_for(app, cat))
                elif column == 2 and host: item.setToolTip(host); item.setIcon(appicons.icon_for(host, cat))
                self.table.setItem(row, column, item)
            if (cat, app, host) in selected:
                selection.select(self.table.model().index(row, 0), selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
        self.table.blockSignals(False); self.table.setUpdatesEnabled(True); self.table.verticalScrollBar().setValue(scroll)
        self.table.setVisible(bool(rows)); self.empty_label.setVisible(not rows); self.selection_changed()
        shown = sum(r[3] for r in rows)
        summary = f"共 {len(rows)} 项 · 合计 {short_time(shown)}" + (f"（当天全部 {short_time(total)}）" if shown < total-1 else "")
        set_text(self.manager_total, summary if rows else "")
        is_today = self.manage_date.date() >= QDate.currentDate()
        self.next_day.setEnabled(not is_today); self.today_button.setEnabled(not is_today)

    def shift_day(self, days):
        self.manage_date.setDate(self.manage_date.date().addDays(days))

    def selection_changed(self):
        keys = self.selected_keys()
        set_text(self.selection_label, f"已选 {len(keys)} 项" if keys else "未选择 · 点选下方记录（可多选）")
        for b in self.classify_buttons: b.setEnabled(bool(keys))
        self.undo_button.setEnabled(self.store.can_undo())

    def remember_choice(self, checked):
        self.config["remember_rule"] = bool(checked); save_config(self.config_path, self.config)

    def apply_classification(self, target):
        keys = self.selected_keys()
        if not keys: return
        self.settle()
        count = self.store.reclassify(self.manage_date.date().toString("yyyy-MM-dd"), keys, target)
        remember = self.change_future.isChecked()
        if remember:
            for _, app, host in keys:
                if host: self.config["host_rules"][host] = target
                elif app.startswith(SURFACE_PREFIX): self.config["surface_rules"][app[len(SURFACE_PREFIX):]] = target
                elif app == "专注时间助手.exe": self.config["own_window"] = target; self.own_combo.setCurrentIndex(max(0, self.own_combo.findData(target)))
                elif app: self.config["app_rules"][app] = target
            save_config(self.config_path, self.config)
        self.manager_signature = None; self.refresh_manager(force=True)
        undo = self.undo_correction if count else None
        if target == IGNORE:
            self.notify(f"已移除 {count} 条记录的时间" + ("，以后也不再计时" if remember else ""), undo)
        else:
            self.notify(f"已设为{LABELS[target]}：修正 {count} 条记录" + ("，以后也这样记录" if remember else ""), undo)
        self.resample_without_charge()

    def undo_correction(self):
        self.settle()
        if self.store.undo(): self.notify("已撤销上次记录修正；分类规则保持不变。")
        self.manager_signature = None; self.refresh_manager(force=True)

    def row_menu(self, position):
        if not self.selected_keys(): return
        menu = popup_menu(self)
        for key in RULE_TARGETS:
            title = "不计入" if key == IGNORE else "设为"+LABELS[key]
            menu.addAction(icon(key, COLORS[key], 16), title, lambda checked=False, k=key: self.apply_classification(k))
        menu.addSeparator(); menu.addAction("调整以后的分类规则…", lambda: self.rule_for_row(self.table.currentRow()))
        row = self.table.currentRow()
        if 0 <= row < len(self.manager_rows) and (self.manager_rows[row][2] or self.manager_rows[row][1].endswith(".exe")):
            _, app, host, _ = self.manager_rows[row]
            menu.addAction(icon("target", ACCENT, 16), f"设置“{app_name(host or app)}”每日上限…", lambda k=host or app: self.set_app_limit(k))
        menu.exec(self.table.viewport().mapToGlobal(position))

    # ── rules and settings ────────────────────────────────────────────
    def current_rule(self):
        current = self.current
        if self.own_window: return self.own_window_menu()
        if self.background: return self.surface_menu(self.background)
        if current.app or current.host: self.show_rule(current.host or current.app, bool(current.host), current.category)

    def own_window_menu(self):
        menu = popup_menu(self)
        for mode, label in OWN_WINDOW_MODES.items():
            action = menu.addAction(label, lambda m=mode: self.set_own_window(m))
            action.setCheckable(True); action.setChecked(self.config["own_window"] == mode)
        menu.exec(self.classify_current.mapToGlobal(self.classify_current.rect().bottomLeft()))

    def surface_menu(self, surface, where=None):
        """How time on a system surface (desktop, taskbar …) is recorded from now on."""
        menu = popup_menu(self); current = self.config["surface_rules"].get(surface, IGNORE)
        menu.addSection(f"“{surface}”以后")
        for target in SURFACE_TARGETS:
            action = menu.addAction(icon(target, COLORS[target], 16), "不计入（默认）" if target == IGNORE else "记为"+LABELS[target],
                                    lambda t=target: self.set_rule_category("surface", surface, t))
            action.setCheckable(True); action.setChecked(target == current)
        menu.exec(where or self.classify_current.mapToGlobal(self.classify_current.rect().bottomLeft()))

    def set_own_window(self, mode):
        self.own_combo.setCurrentIndex(max(0, self.own_combo.findData(mode)))
        self.save_preferences()

    def rule_for_row(self, row):
        if not 0 <= row < len(self.manager_rows): return
        cat, app, host, _ = self.manager_rows[row]
        if not (app or host) or app == AWAY_LABEL:
            self.notify("离开时间没有对应的程序，用上方的“设为”按钮或右键就能改分类。"); return
        self.show_rule(host or app, bool(host), cat)

    def show_rule(self, key="", domain=False, category="work"):
        dialog = RuleDialog(self, key, domain, category if category in RULE_TARGETS else "work")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settle(); self.config["host_rules" if dialog.kind.currentIndex() == 1 else "app_rules"][dialog.key.text()] = dialog.category.currentData()
            save_config(self.config_path, self.config); self.notify("分类规则已保存，从现在起生效。")
            self.refresh_rules(); self.resample_without_charge()

    def add_rule(self): self.show_rule()

    def default_rules(self):
        entries = {("surface", name): IGNORE for name in SURFACES}
        for key in ("work", "distraction", "system"):
            for app in self.config[f"{key}_apps"]: entries[("app", app)] = key
        for host in SITES: entries[("host", host)] = "distraction"
        return entries

    def refresh_rules(self):
        entries = {key: (category, "默认") for key, category in self.default_rules().items()}
        for key, category in self.config["app_rules"].items(): entries[("app", key)] = (category, "自定义")
        for key, category in self.config["host_rules"].items(): entries[("host", key)] = (category, "自定义")
        for key, category in self.config["surface_rules"].items(): entries[("surface", key)] = (category, "自定义")
        row = self.rules_table.currentRow()
        selected = self.rule_entries[row][0] if 0 <= row < len(getattr(self, "rule_entries", [])) else None
        everything = sorted(entries.items(), key=lambda x: (x[1][1] != "自定义", x[0][0], x[0][1]))
        query = self.rule_search.text().strip().lower(); custom_only = self.custom_only.isChecked(); wanted = self.rule_filter.currentData()
        self.rule_entries = [e for e in everything if (not custom_only or e[1][1] == "自定义") and (not query or query in e[0][1])
                             and (not wanted or e[1][0] == wanted)]
        self.rules_table.setRowCount(len(self.rule_entries))
        for row, ((kind, key), (category, source)) in enumerate(self.rule_entries):
            limit = self.config["app_limits"].get(key)
            for col, value in enumerate([LABELS[category], {"host": "网站", "surface": "系统界面"}.get(kind, "应用"), key,
                                         f"{limit} 分钟" if limit else "—", source]):
                item = QTableWidgetItem(value)
                if col == 0: item.setData(CategoryDelegate.ROLE, category)
                if col == 2 and kind == "app": item.setIcon(appicons.icon_for(key, category))
                if col == 4 and source == "默认": item.setForeground(QColor(SUBTLE))
                if col == 3 and not limit: item.setForeground(QColor(SUBTLE))
                if col == 2 and kind == "host": item.setIcon(appicons.icon_for(key, category))
                self.rules_table.setItem(row, col, item)
        keys = [key for key, _ in self.rule_entries]
        if selected in keys: self.rules_table.selectRow(keys.index(selected))
        else: self.rules_table.clearSelection(); self.rules_table.setCurrentCell(-1, -1)
        custom = sum(1 for _, (_, source) in everything if source == "自定义")
        shown = f" · 显示 {len(self.rule_entries)} 条" if len(self.rule_entries) != len(everything) else ""
        self.rules_count.setText(f"共 {len(everything)} 条 · 自定义 {custom} 条{shown}")

    def rule_menu(self, row, where=None):
        """Change a rule's category in place, from a click on its category cell or a right click."""
        if not 0 <= row < len(self.rule_entries): return
        self.rules_table.selectRow(row)
        (kind, key), (category, source) = self.rule_entries[row]
        if where is None:
            cell = self.rules_table.visualRect(self.rules_table.model().index(row, 0))
            where = self.rules_table.viewport().mapToGlobal(cell.bottomLeft())
        if kind == "surface": return self.surface_menu(key, where)
        menu = popup_menu(self)
        for target in RULE_TARGETS:
            action = menu.addAction(icon(target, COLORS[target], 16), LABELS[target], lambda t=target: self.set_rule_category(kind, key, t))
            action.setCheckable(True); action.setChecked(target == category)
        menu.addSeparator(); menu.addAction("编辑…", self.edit_rule)
        limit = self.config["app_limits"].get(key)
        menu.addAction(icon("target", ACCENT, 16), f"每日上限…（现在 {limit} 分钟）" if limit else "每日上限…", lambda: self.set_app_limit(key))
        reset = menu.addAction("恢复默认", self.remove_rule); reset.setEnabled(source == "自定义")
        if where is None:
            cell = self.rules_table.visualRect(self.rules_table.model().index(row, 0))
            where = self.rules_table.viewport().mapToGlobal(cell.bottomLeft())
        menu.exec(where)

    def rule_store(self, kind):
        return self.config[{"host": "host_rules", "surface": "surface_rules"}.get(kind, "app_rules")]

    def set_rule_category(self, kind, key, category):
        rules = self.rule_store(kind)
        default = self.default_rules().get((kind, key))
        if rules.get(key, default) == category: return
        self.settle(); undo = self.rule_undo(kind, key)
        if category == default: rules.pop(key, None)  # same as the built-in rule: no custom entry needed
        else: rules[key] = category
        save_config(self.config_path, self.config); self.refresh_rules()
        self.notify(f"{key} 以后记为{LABELS[category]}", undo, seconds=6); self.resample_without_charge()

    def rule_undo(self, kind, key):
        """A callable that puts one rule back exactly as it is now."""
        rules = self.rule_store(kind); before = rules.get(key)
        def restore():
            self.settle()
            if before is None: rules.pop(key, None)
            else: rules[key] = before
            save_config(self.config_path, self.config); self.refresh_rules(); self.notify(f"已撤销 {key} 的规则修改。")
            self.resample_without_charge()
        return restore

    def edit_rule(self):
        row = self.rules_table.currentRow()
        if 0 <= row < len(self.rule_entries):
            (kind, key), (category, _) = self.rule_entries[row]
            if kind == "surface": return self.rule_menu(row)
            self.show_rule(key, kind == "host", category)
        else: self.notify("请先在表格里选择一条规则。")

    def remove_rule(self):
        row = self.rules_table.currentRow()
        if not 0 <= row < len(self.rule_entries): self.notify("请先在表格里选择一条规则。"); return
        (kind, key), (_, source) = self.rule_entries[row]
        if source != "自定义": self.notify("这条已经是默认规则。"); return
        self.settle(); undo = self.rule_undo(kind, key)
        self.rule_store(kind).pop(key, None); save_config(self.config_path, self.config)
        self.refresh_rules(); self.notify(f"已恢复 {key} 的默认分类", undo, seconds=6); self.resample_without_charge()

    def schedule_preferences(self, *_):
        self.preferences_timer.start()

    def save_preferences(self):
        values = {"idle_seconds": self.idle_spin.value()*60, "reminder_seconds": self.reminder_spin.value(),
                  "remind_distraction": self.remind_check.isChecked(), "reminder_sound": self.sound_check.isChecked(),
                  "tray_hint": self.tray_hint_check.isChecked(), "daily_summary": self.summary_combo.currentData(),
                  "return_prompt_minutes": self.return_combo.currentData(),
                  "learning_minutes": self.learning_spin.value(), "rest_after_minutes": self.rest_spin.value(),
                  "work_goal_minutes": self.goal_spin.value(), "distraction_limit_minutes": self.limit_spin.value(),
                  "focus_minutes": self.focus_spin.value(), "own_window": self.own_combo.currentData(),
                  "break_minutes": self.break_spin.value(), "focus_float": self.float_check.isChecked(),
                  "focus_includes_unknown": self.focus_unknown_check.isChecked(), "hour_range": self.hour_range_combo.currentData(),
                  "export_format": self.export_format_combo.currentData(),
                  "export_parts": [key for key, box in self.export_checks.items() if box.isChecked()]}
        if all(self.config[k] == v for k, v in values.items()): return
        if (values["work_goal_minutes"], values["distraction_limit_minutes"]) != (self.config["work_goal_minutes"], self.config["distraction_limit_minutes"]):
            self.goal_flags = {}  # a changed goal can be announced again today
        self.config.update(values); save_config(self.config_path, self.config)
        self.reminder_spin.setEnabled(values["remind_distraction"]); self.sound_check.setEnabled(values["remind_distraction"])
        self.notify("设置已自动保存。"); self.update_float(); self.resample_without_charge()

    def resample_without_charge(self):
        if not self.testing: self.tick()
        self.refresh(force=True)

    # ── pause, learning allowance, reminders ──────────────────────────
    def pause_clicked(self):
        """Resume at once; when recording, offer how long to pause."""
        if self.paused: return self.toggle_pause()
        menu = popup_menu(self)
        for minutes in (15, 30, 60): menu.addAction(f"暂停 {minutes} 分钟", lambda m=minutes: self.pause_for(m))
        menu.addSeparator(); menu.addAction("一直暂停，直到我点继续", self.toggle_pause)
        menu.exec(self.pause_button.mapToGlobal(self.pause_button.rect().bottomLeft()))

    def pause_for(self, minutes):
        if not self.paused: self.toggle_pause()
        self.pause_until = time.monotonic()+minutes*60; self.refresh(force=True)
        self.notify(f"已暂停 {minutes} 分钟，到时自动继续记录。", self.toggle_pause, "现在继续")

    def toggle_pause(self):
        self.settle(); self.paused = not self.paused; self.pause_until = None
        if self.paused: self.dismiss_reminder()
        self.update_tray_tip(); self.refresh(force=True)
        self.notify("已暂停计时与提醒。" if self.paused else "已继续记录。")

    def allow_learning(self, site=None):
        if isinstance(site, bool): site = None
        site = site or self.current.site or self.current.app
        if not site: return
        minutes = self.config["learning_minutes"]
        self.learning[site] = time.monotonic()+minutes*60; self.dismiss_reminder()
        self.notify(f"接下来 {minutes} 分钟不再提醒 {app_name(site)}，时间照常算摸鱼。", self.cancel_learning, "现在就停"); self.refresh(force=True)

    def cancel_learning(self):
        self.learning.pop(self.current.site or self.current.app, None); self.learning.pop(self.current.site, None)
        self.notify("已恢复摸鱼提醒。"); self.refresh(force=True)

    def maybe_remind(self, now):
        key = self.current.site or self.current.app
        if self.learning.get(key, 0) > now: return  # the user chose to keep going for a while
        if not self.config["remind_distraction"] and not self.focus: return
        interval = min(self.config["reminder_seconds"], 30) if self.focus else self.config["reminder_seconds"]
        if self.popup or now-self.reminded.get(key, -1e9) < interval: return
        self.reminded[key] = now; self.popup_site = key; self.popup = Reminder(self, self.current); self.popup.show()
        if self.config["reminder_sound"]: self.beep()

    @staticmethod
    def beep():
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception: pass

    def distraction_note(self):
        """Extra line for the reminder while focusing or once over today's limit."""
        if self.focus:
            return f"正在专注，还剩 {duration(self.focus_left())[3:]}。先把手上的事做完吧。"
        limit = self.config["distraction_limit_minutes"]*60
        spent = self.store.totals(datetime.now().date().isoformat())["distraction"]
        if limit and spent > limit:
            return f"今天已摸鱼 {short_time(spent)}，超过了 {short_time(limit)} 的上限。"
        return ""

    # ── focus sessions, goals, rest ───────────────────────────────────
    def focus_left(self):
        return max(0.0, self.focus["planned"]-(time.monotonic()-self.focus["mono"])) if self.focus else 0.0

    def break_left(self):
        return max(0.0, self.rest_break["planned"]-(time.monotonic()-self.rest_break["mono"])) if self.rest_break else 0.0

    def focus_clicked(self):
        where = self.focus_button.mapToGlobal(self.focus_button.rect().bottomLeft())
        if self.focus:
            menu = popup_menu(self)
            menu.addAction(f"提前结束（已专注 {short_time(time.monotonic()-self.focus['mono'])}）", lambda: self.finish_focus(completed=False))
            menu.addAction("继续专注"); menu.exec(where); return
        if self.rest_break:
            menu = popup_menu(self)
            menu.addAction("跳过休息，开始下一段", lambda: self.start_focus(task=self.rest_break["task"]))
            menu.addAction("结束休息", self.end_break); menu.exec(where); return
        dialog = FocusDialog(self, self.config["focus_minutes"], self.config["recent_tasks"], self.config["break_minutes"])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            minutes, task = dialog.values(); self.start_focus(minutes, task)

    def tray_focus_action(self):
        if self.focus: self.finish_focus(completed=False)
        elif self.rest_break: self.start_focus(task=self.rest_break["task"])
        else: self.start_focus()

    def start_focus(self, minutes=None, task=""):
        self.settle()
        if self.rest_break: self.rest_break = None
        now = datetime.now(); minutes = minutes or self.config["focus_minutes"]; task = (task or "").strip()[:40]
        self.focus = {"mono": time.monotonic(), "wall": now, "planned": minutes*60, "day": now.date().isoformat(),
                      "base": self.store.totals(now.date().isoformat()), "task": task}
        if task:
            self.config["recent_tasks"] = [task]+[t for t in self.config["recent_tasks"] if t != task][:7]
            save_config(self.config_path, self.config)
        if self.paused: self.toggle_pause()
        self.notify(f"开始专注 {minutes} 分钟" + (f"：{task}" if task else "") + "。期间停留在摸鱼网站会立即提醒。")
        self.update_float(); self.resample_without_charge()

    def start_break(self, task):
        minutes = self.config["break_minutes"]
        self.rest_break = {"mono": time.monotonic(), "planned": minutes*60, "task": task}; self.update_float()

    def end_break(self, done=False):
        if not self.rest_break: return
        task = self.rest_break["task"]; self.rest_break = None; self.update_float(); self.refresh(force=True)
        if done:
            self.notify("休息结束，准备好开始下一段了吗？", lambda: self.start_focus(task=task), "开始下一段", seconds=12)
            self.tray_message("休息结束", "准备好就开始下一段专注吧。")
            if self.config["reminder_sound"]: self.beep()

    def update_float(self):
        """Show, update or hide the floating timer to match the focus / break state and the setting."""
        mode = "focus" if self.focus else "break" if self.rest_break else None
        if not mode or not self.config["focus_float"] or self.stopping:
            if self.floater: self.floater.hide()
            return
        if not self.floater: self.floater = FocusFloat(self); self.floater.place()
        if mode == "focus":
            self.floater.set_state(mode, self.focus_left(), self.focus["planned"], self.focus["task"], "idle" if self.paused else self.current.category)
        else:
            self.floater.set_state(mode, self.break_left(), self.rest_break["planned"], self.rest_break["task"], "")
        if not self.floater.isVisible(): self.floater.show()

    def hide_float_for_good(self):
        self.float_check.setChecked(False); self.config["focus_float"] = False; save_config(self.config_path, self.config)
        self.update_float(); self.notify("已关闭悬浮计时，可在“规则与设置 → 目标、专注与启动”里重新打开。")

    def finish_focus(self, completed):
        if not self.focus: return
        self.settle(); focus, self.focus = self.focus, None
        elapsed = min(focus["planned"], time.monotonic()-focus["mono"])
        today = datetime.now().date().isoformat()
        after = self.store.totals(focus["day"])
        work = after["work"]-focus["base"]["work"]; play = after["distraction"]-focus["base"]["distraction"]
        if today != focus["day"]:  # crossed midnight
            later = self.store.totals(today); work += later["work"]; play += later["distraction"]
        work, play = max(0.0, work), max(0.0, play)
        if elapsed >= 60:
            self.store.add_session(focus["wall"], focus["planned"]//60, elapsed, work, play, completed, focus.get("task", ""))
        summary = f"工作 {short_time(work)} · 摸鱼 {short_time(play)}"; task = focus.get("task", "")
        if completed:
            rest = self.config["break_minutes"]
            if rest:
                self.start_break(task)
                self.notify(f"专注完成：{summary}。休息 {rest} 分钟吧", lambda: self.start_focus(task=task), "跳过休息", seconds=10)
            else:
                self.notify(f"专注完成 {focus['planned']//60} 分钟：{summary}", lambda: self.start_focus(task=task), "再来一次", seconds=10)
            self.tray_message("专注完成", summary+(f"。休息 {rest} 分钟吧。" if rest else ""), QSystemTrayIcon.MessageIcon.Information, 5000)
            if self.config["reminder_sound"]: self.beep()
        else:
            self.notify(f"已结束专注（{short_time(elapsed)}）：{summary}")
        self.dismiss_reminder(); self.update_float(); self.refresh(force=True)

    def track_rest(self, current):
        """Count continuous computer use; idle time (away / locked) resets it."""
        limit = self.config["rest_after_minutes"]*60
        if self.paused or self.idle_now or not limit:
            self.active_run = 0.0; return
        self.active_run += SAMPLE_SECONDS
        if self.active_run >= limit:
            self.active_run = 0.0
            message = f"已连续使用电脑 {short_time(limit)}，起身活动一下、看看远处吧。"
            self.notify(message, label="")
            self.tray_message("休息一下", message, QSystemTrayIcon.MessageIcon.Information, 6000)
            if self.config["reminder_sound"]: self.beep()

    def check_goals(self):
        today = datetime.now().date().isoformat()
        if self.goal_flags.get("day") != today: self.goal_flags = {"day": today}
        totals = self.store.totals(today)
        goal, limit = self.config["work_goal_minutes"]*60, self.config["distraction_limit_minutes"]*60
        if goal and totals["work"] >= goal and not self.goal_flags.get("work"):
            self.goal_flags["work"] = True
            self.notify(f"今日工作目标 {short_time(goal)} 已完成。", label="")
            self.tray_message("工作目标已完成", f"今天已工作 {short_time(totals['work'])}。", QSystemTrayIcon.MessageIcon.Information, 5000)
        self.check_app_limits(today)
        if limit and totals["distraction"] > limit and not self.goal_flags.get("limit"):
            self.goal_flags["limit"] = True
            self.notify(f"今天摸鱼已超过 {short_time(limit)} 的上限。", label="")
            self.tray_message("摸鱼超过上限", f"今天已摸鱼 {short_time(totals['distraction'])}。", QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ── start with Windows ────────────────────────────────────────────
    def launch_command(self):
        """What the startup shortcut runs: the EXE, or the project's pythonw with main.py."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable), "--start-hidden"
        pythonw = self.project/".venv"/"Scripts"/"pythonw.exe"
        return pythonw, f'"{self.project/"app"/"main.py"}" --start-hidden'

    def autostart_enabled(self):
        return self.startup_link.exists()

    def set_autostart(self, enabled):
        try:
            if enabled:
                target, arguments = self.launch_command()
                self.startup_link.parent.mkdir(parents=True, exist_ok=True)
                script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:FOCUS_LINK);"
                          "$s.TargetPath=$env:FOCUS_TARGET;$s.Arguments=$env:FOCUS_ARGS;$s.WorkingDirectory=$env:FOCUS_DIR;"
                          "$s.IconLocation=$env:FOCUS_TARGET+',0';$s.Description='专注时间助手';$s.Save()")
                env = dict(os.environ, FOCUS_LINK=str(self.startup_link), FOCUS_TARGET=str(target), FOCUS_ARGS=arguments,
                           FOCUS_DIR=str(target.parent))
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                               timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.notify("已设置开机启动：登录 Windows 后在托盘自动运行。")
            else:
                self.startup_link.unlink(missing_ok=True)
                self.notify("已取消开机启动。")
        except (OSError, subprocess.SubprocessError):
            logging.exception("Autostart change failed")
            self.notify("开机启动设置失败，详情见日志。")
        self.autostart_check.blockSignals(True); self.autostart_check.setChecked(self.autostart_enabled()); self.autostart_check.blockSignals(False)

    def dismiss_reminder(self):
        if self.popup: self.popup.close(); self.popup.deleteLater(); self.popup = None

    # ── browser connection, export ────────────────────────────────────
    def refresh_connection(self):
        connected = self.browser.connected()
        set_text(self.connection_title, "已连接浏览器" if connected else "等待浏览器连接" if self.server else "本机接口暂不可用")
        restyle(self.connection_dot, f"background:{COLORS['work'] if connected else P['offline']};border-radius:6px;")
        set_text(self.connection_detail, "正在按当前活动标签页的域名识别；后台标签页不会单独计时。" if connected else
                 "目前按应用与窗口标题识别。按下方步骤连接扩展后，即可按域名准确分类。" if self.server else
                 "接口可能被另一个程序占用；应用与标题计时仍然可用。")

    def copy_token(self):
        QApplication.clipboard().setText(self.config["token"]); self.notify("本机连接码已复制，请粘贴到扩展设置页。")

    def open_extension(self): QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project/"browser_extension")))

    def copy_extension_path(self):
        QApplication.clipboard().setText(self.extension_path.text()); self.notify("扩展文件夹路径已复制。")

    def export_day(self): self.export_days([self.manage_date.date().toString("yyyy-MM-dd")])

    def export_menu(self):
        menu = popup_menu(self)
        for days, back, label in ((1, 0, "导出今天"), (1, 1, "导出昨天"), (3, 0, "导出最近 3 天"), (7, 0, "导出最近 7 天"), (30, 0, "导出最近 30 天")):
            menu.addAction(icon("report", ACCENT, 16), label, lambda d=days, b=back: self.export_range(d, b))
        fmt = {"html": "HTML 报告", "csv": "CSV 表格", "both": "HTML 报告 + CSV 表格"}[self.config["export_format"]]
        menu.addSeparator(); hint = menu.addAction(f"格式：{fmt}（在设置里修改）"); hint.setEnabled(False)
        menu.exec(self.export_button.mapToGlobal(self.export_button.rect().bottomLeft()))

    def export_range(self, count=None, back=0):
        """Full analysis as an HTML report and / or CSV tables (format and contents from the settings)."""
        from PySide6.QtWidgets import QFileDialog
        import report
        parts = self.config["export_parts"]
        if not parts:
            self.notify("请先在“规则与设置 → 分析与导出”里至少勾选一项导出内容。"); return
        if not count: count, back = self.range_combo.currentData()
        last = datetime.now().date()-timedelta(days=back); first = last-timedelta(days=count-1)
        span = last.isoformat() if count == 1 else f"{first.isoformat()}_{last.isoformat()}"
        base = self.project/"exports"; base.mkdir(exist_ok=True)
        parent = QFileDialog.getExistingDirectory(self, "选择报告保存位置", str(base))
        if not parent: return
        fmt = self.config["export_format"]; stamp = f"{datetime.now():%H%M%S}"
        # A lone HTML report is saved as one file; CSV tables get their own folder.
        folder = Path(parent) if fmt == "html" else Path(parent)/f"专注时间报告_{span}_{stamp}"
        name = f"专注时间报告_{span}_{stamp}" if fmt == "html" else "专注时间报告"
        try:
            written = report.export(self.store, folder, first.isoformat(), last.isoformat(), parts, fmt,
                                    P["accent"] if not P["dark"] else "#4B5FE0", self.config["focus_includes_unknown"], name)
        except OSError:
            logging.exception("Report export failed"); QMessageBox.warning(self, "未能导出", "文件夹可能没有写入权限，请换一个位置再试。"); return
        html = next((path for path in written if path.suffix == ".html"), None)
        open_it = (lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html)))) if html else (lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        where = html.name if fmt == "html" and html else f"{len(written)} 个文件到“{folder.name}”"
        self.notify(f"已导出 {where}", open_it, "打开报告" if html else "打开文件夹", seconds=10)

    def export_days(self, days):
        import csv
        from PySide6.QtWidgets import QFileDialog
        directory = self.project/"exports"; directory.mkdir(exist_ok=True)
        default = directory/f"{days[0]}_{days[-1]}_专注时间_{datetime.now():%H%M%S}.csv"
        name, _ = QFileDialog.getSaveFileName(self, "导出时间记录", str(default), "CSV 文件 (*.csv)")
        if not name: return
        try:
            with open(name, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream); writer.writerow(["日期", "分类", "应用", "域名", "秒数", "时长"])
                for day in days:
                    for category, app, host, seconds in self.store.details(day): writer.writerow([day, CATEGORIES[category], app, host, round(seconds, 2), duration(seconds)])
            self.notify("已导出到所选文件。")
        except OSError:
            logging.exception("Export failed"); QMessageBox.warning(self, "未能导出", "文件可能正被占用，请换一个文件名再试。")

    # ── lifecycle ─────────────────────────────────────────────────────
    def hide_to_tray(self):
        if not self.testing: self.remember_window()
        self.hide()
        if not self.tray_hinted and self.config["tray_hint"]:
            self.tray_hinted = True
            self.tray.showMessage("专注时间助手仍在记录", "点击托盘图标打开；右键可暂停或退出。", QSystemTrayIcon.MessageIcon.Information, 3000)
        QTimer.singleShot(2000, self.trim_if_hidden)

    def closeEvent(self, event):
        if self.stopping: event.accept(); return
        if self.testing: self.shutdown(); event.accept(); return
        event.ignore()
        if QSystemTrayIcon.isSystemTrayAvailable(): self.hide_to_tray()
        else: self.showMinimized()

    def quit_requested(self):
        self.shutdown(); QApplication.instance().quit()

    def shutdown(self):
        if self.stopping: return
        if not self.testing and self.isVisible():
            try: self.remember_window()
            except OSError: logging.exception("Could not save window state")
        self.stopping = True; self.timer.stop(); self.trim_timer.stop(); self.settle(); self.dismiss_reminder(); self.store.close()
        if self.floater: self.floater.close()
        self.close_return_prompt()
        appicons.save()
        if self.server: self.server.shutdown(); self.server.server_close()
        self.tray.hide()
