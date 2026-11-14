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
    QDialog, QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect,
    QCheckBox, QDateEdit, QSpinBox)
from core import Activity, Accountant, CATEGORIES, IGNORE, LABELS, OWN_WINDOW_MODES, RULE_TARGETS, Store, SITES, classify, hostname, load_config, save_config, streaks
from core import SURFACES, SURFACE_TARGETS, SURFACE_PREFIX
from insights import insights, work_rhythm
from bridge import BrowserState, start_bridge
from theme import ACCENT, COLORS, INK, P, TEXTS, THEMES, TINTS, MUTED, SUBTLE, app_name, icon, key_name, label, site_name
from charts import focus_ratio, short_time, share
import subcats
from ui_qt import Shell, CategoryDelegate, ElidedLabel, IconLabel, Segmented, text, button, restyle, set_text, fade_in
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
from core import AWAY_LABEL, MANUAL_MARK  # stored app name of away time the user counted as work; mark of hand-entered time


def popup_menu(parent):
    """A context / drop-down menu that frees itself once closed."""
    menu = QMenu(parent); menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return menu


def tint(colour, alpha=34):
    """A see-through version of a colour, for soft pill backgrounds in any theme."""
    c = QColor(colour); return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


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
        target = (site_name(activity.host) if activity.host else activity.site) or app_name(activity.app)
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
        note = owner.sub_limit_note(activity)
        if note: stats += f"\n{note}"
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
    """Small card in the corner after a long time without input: was that work, distraction or away?

    Watching a film or a lecture, a meeting or reading on paper all look like "away" to the computer.
    `before` is what was in the foreground when the input stopped; its category is the suggested answer.
    """
    def __init__(self, owner, began, ended, before=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating); self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if owner.testing: self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.owner, self.began, self.ended = owner, began, ended
        outer = QVBoxLayout(self); outer.setContentsMargins(14, 10, 14, 18)
        body = QFrame(); body.setObjectName("card"); outer.addWidget(body)
        layout = QVBoxLayout(body); layout.setContentsMargins(18, 16, 18, 14); layout.setSpacing(6)
        minutes = round((ended-began).total_seconds()/60)
        layout.addWidget(text(f"刚才 {minutes} 分钟没有操作，这段在做什么？", "heading"))
        name = (site_name(before.host) if before.host else before.site) or app_name(before.app) if before and (before.app or before.host) else ""
        layout.addWidget(text(f"{began:%H:%M} – {ended:%H:%M}" + (f"，之前在用“{name}”" if name else "") +
                              "。看视频、开会、看纸质资料时，电脑会以为你离开了。", "muted", wrap=True))
        suggested = "distraction" if before and before.category == "distraction" else "work"
        row = QHBoxLayout(); row.setSpacing(8); row.addStretch(); self.choices = {}
        for key, label in (("idle", "保持离开"), ("distraction", "算作摸鱼"), ("work", "算作工作")):
            choice = button(label, lambda checked=False, k=key: self.choose(k), key == suggested)
            if key != "idle": choice.setIcon(icon(key, P["on_accent"] if key == suggested else COLORS[key], 16))
            self.choices[key] = choice; row.addWidget(choice)
        layout.addSpacing(6); layout.addLayout(row); self.setFixedWidth(420)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.close); self.timer.start(90_000)
        self.adjustSize()
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right()-self.width()-8, area.bottom()-self.height()-8)

    def choose(self, category):
        owner, began, ended = self.owner, self.began, self.ended
        self.close()
        if category != "idle": owner.count_away_as(category, began, ended)

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


class ManualDialog(QDialog):
    """Enter a stretch the computer could not see (a meeting, a class, reading on paper)."""
    PRESETS = ("开会", "上课", "看纸质资料", "讨论", "打电话")

    def __init__(self, parent, day, begin, end, latest):
        super().__init__(parent); self.latest = latest  # last minute of the day that may be entered (now, for today)
        self.setWindowTitle("补记时间"); self.setMinimumWidth(480)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        layout.addWidget(text(f"补记 {parent.day_name(day)} 的一段时间", "heading"))
        layout.addWidget(text("用来补上电脑没记到的事。这段时间里已经记下的程序保持不变，只把离开和没有记录的部分补上。", "muted", wrap=True))
        form = QFormLayout(); form.setSpacing(12)
        self.clocks = []
        for minute in (begin, end):
            row = QHBoxLayout(); row.setSpacing(8); pair = []
            for value, high, suffix, step in ((minute//60, 23, " 时", 1), (minute % 60, 59, " 分", 5)):
                box = QSpinBox(); box.setRange(0, high); box.setValue(value); box.setSuffix(suffix); box.setSingleStep(step)
                box.setFixedWidth(104); box.setWrapping(True); box.valueChanged.connect(self.update_note); row.addWidget(box); pair.append(box)
            row.addStretch(); holder = QWidget(); holder.setLayout(row); row.setContentsMargins(0, 0, 0, 0); self.clocks.append((pair, holder))
        form.addRow("开始", self.clocks[0][1]); form.addRow("结束", self.clocks[1][1])
        self.name = QComboBox(); self.name.setEditable(True); self.name.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.name.addItems(self.PRESETS); self.name.setCurrentIndex(-1); self.name.setCurrentText("")
        self.name.lineEdit().setPlaceholderText("例如：开会、上课、看纸质资料"); self.name.lineEdit().setMaxLength(20)
        form.addRow("做了什么", self.name)
        self.category = Segmented([(LABELS[key], key) for key in ("work", "distraction")])
        holder = QWidget(); row = QHBoxLayout(holder); row.setContentsMargins(0, 0, 0, 0); row.addWidget(self.category); row.addStretch()
        form.addRow("记为", holder); layout.addLayout(form)
        self.note = text("", "small", wrap=True); layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("补记"); self.ok.setProperty("role", "primary"); self.ok.setDefault(True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.update_note(); self.name.setFocus()

    def span(self):
        return tuple(pair[0].value()*60+pair[1].value() for pair, _ in self.clocks)

    def update_note(self, *_):
        begin, end = self.span(); problem = ""
        if end <= begin: problem = "结束时间要晚于开始时间。"
        elif end > self.latest: problem = "不能补记还没到的时间。"
        restyle(self.note, f"color:{P['danger']};" if problem else "")
        self.note.setText(problem or f"共 {short_time((end-begin)*60)}。补记的时间会标上“（补记）”，之后可以撤销，也能再改分类。")
        self.ok.setEnabled(not problem)

    def values(self):
        begin, end = self.span()
        return begin, end, self.category.currentData(), self.name.currentText().strip()[:20]


class RangeDialog(QDialog):
    """Pick the first and last day of a custom analysis range."""
    LONGEST = 92

    def __init__(self, parent, first, last, earliest):
        super().__init__(parent)
        self.setWindowTitle("自定义范围"); self.setMinimumWidth(420)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        layout.addWidget(text("分析哪一段时间？", "heading"))
        form = QFormLayout(); form.setSpacing(12); self.edits = []
        for value in (first, last):
            edit = QDateEdit(QDate.fromString(value, "yyyy-MM-dd")); edit.setCalendarPopup(True); edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMaximumDate(QDate.currentDate()); edit.setFixedWidth(170); edit.dateChanged.connect(self.update_note); self.edits.append(edit)
            if earliest: edit.setMinimumDate(QDate.fromString(earliest, "yyyy-MM-dd"))
        form.addRow("从", self.edits[0]); form.addRow("到", self.edits[1]); layout.addLayout(form)
        self.note = text("", "small", wrap=True); layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("开始分析"); self.ok.setProperty("role", "primary"); self.ok.setDefault(True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.update_note()

    def update_note(self, *_):
        days = self.edits[0].date().daysTo(self.edits[1].date())+1
        problem = "结束日期不能早于开始日期。" if days < 1 else f"最长 {self.LONGEST} 天，请缩短一些。" if days > self.LONGEST else ""
        restyle(self.note, f"color:{P['danger']};" if problem else "")
        self.note.setText(problem or f"共 {days} 天；会和它前面的 {days} 天比较。")
        self.ok.setEnabled(not problem)

    def values(self):
        return tuple(edit.date().toString("yyyy-MM-dd") for edit in self.edits)


class WelcomeDialog(QDialog):
    """Shown once on the very first start: what the program does, a daily goal, start with Windows."""
    POINTS = (("overview", "自动记录，不用打卡", "只算你正在操作的那个窗口，分成工作、摸鱼、系统工具、离开和待分类。分错了随时能改。"),
              ("bell", "刷太久会提醒一下", "在抖音、B站、小红书这类网站停太久，屏幕中间弹个提醒；不锁屏，也不抢键盘。"),
              ("shield", "数据只在这台电脑上", "没有账号，不联网上传；不保存网址、页面标题、截图和按键。"))

    def __init__(self, parent, goal, autostart):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用专注时间助手"); self.setMinimumWidth(520)
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 26, 28, 22); layout.setSpacing(16)
        title = text("看清时间都花在了哪"); title.setStyleSheet("font-size:20px;font-weight:700;"); layout.addWidget(title)
        for kind, head, detail in self.POINTS:
            row = QHBoxLayout(); row.setSpacing(14)
            chip = QFrame(); chip.setFixedSize(34, 34); chip.setStyleSheet(f"background:{P['soft']};border-radius:9px;")
            inner = QHBoxLayout(chip); inner.setContentsMargins(8, 8, 8, 8); inner.addWidget(IconLabel(icon(kind, ACCENT, 18), 18))
            row.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout(); column.setSpacing(2); column.addWidget(text(head, "heading")); column.addWidget(text(detail, "muted", wrap=True))
            row.addLayout(column, 1); layout.addLayout(row)
        box = QFrame(); box.setObjectName("note"); form = QFormLayout(box); form.setContentsMargins(16, 14, 16, 14); form.setSpacing(10)
        self.goal = QSpinBox(); self.goal.setRange(0, 16); self.goal.setValue(round(goal/60)); self.goal.setSuffix(" 小时"); self.goal.setSpecialValueText("不设目标")
        self.goal.setFixedWidth(124); self.goal.setToolTip("每天想工作 / 学习多久；侧栏会显示进度，以后可以在设置里改")
        self.autostart = QCheckBox("登录 Windows 后自动在托盘运行"); self.autostart.setChecked(autostart)
        form.addRow("每日工作目标", self.goal); form.addRow("开机启动", self.autostart); layout.addWidget(box)
        layout.addWidget(text("关闭窗口后，它会留在右下角的托盘里继续记录；要彻底退出，右键托盘图标选“退出程序”。", "small", wrap=True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        start = buttons.button(QDialogButtonBox.StandardButton.Ok); start.setText("开始使用"); start.setProperty("role", "primary"); start.setDefault(True)
        start.setMinimumHeight(36); buttons.accepted.connect(self.accept); layout.addWidget(buttons)

    def values(self):
        return self.goal.value()*60, self.autostart.isChecked()


class App(Shell):
    manager_sub = None  # (category, subcategory id) the activity list is narrowed to
    analysis_day = None  # one day picked from the calendar / a bar, instead of a range
    analysis_custom = None  # ("week" | "lastweek" | "month" | "lastmonth" | "custom", first day, last day) from the 更多 menu
    analysis_last = None  # last day of what the analysis page shows
    def __init__(self, data_dir: Path, project: Path, testing=False):
        super().__init__()
        self.project = project; self.data_dir = data_dir; self.testing = testing
        self.config_path = data_dir/"settings.json"; self.first_run = not self.config_path.exists()
        self.config = load_config(self.config_path)
        theme.SITE_ALIASES = self.config["site_names"]; theme.APP_ALIASES = self.config["app_names"]
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
        QShortcut(QKeySequence("F5"), self).activated.connect(lambda: self.refresh(force=True))
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(lambda: self.focus_clicked() if self.focus or self.rest_break else self.start_focus())
        for index in range(5):
            QShortcut(QKeySequence(f"Ctrl+{index+1}"), self).activated.connect(lambda i=index: self.navigate(i))
        self.restore_window()
        self.preferences_timer = QTimer(self); self.preferences_timer.setSingleShot(True); self.preferences_timer.setInterval(600); self.preferences_timer.timeout.connect(self.save_preferences)
        self.sub_limit_timer = QTimer(self); self.sub_limit_timer.setSingleShot(True); self.sub_limit_timer.setInterval(600)
        self.sub_limit_timer.timeout.connect(lambda: (save_config(self.config_path, self.config), self.refresh(force=True)))
        self.trim_timer = QTimer(self); self.trim_timer.setInterval(TRIM_EVERY_MS); self.trim_timer.timeout.connect(self.trim_if_hidden)
        self.timer = QTimer(self); self.timer.setInterval(SAMPLE_MS); self.timer.timeout.connect(self.tick)
        if not testing:
            self.timer.start(); self.trim_timer.start(); QTimer.singleShot(50, self.tick)


    # ── 小类 (subcategories of 工作 and 摸鱼) ─────────────────────────────
    def sub_name(self, category, sid):
        return subcats.name(category, sid, self.config)

    def sub_palette(self, category):
        """{id: colour} in list order; 其他 is the neutral grey."""
        ids = subcats.order(category, self.config)
        colours = theme.sub_colors(category, len(ids)-1)
        return {**dict(zip(ids[:-1], colours)), subcats.OTHER: COLORS["idle"]}

    def sub_members_text(self, category, sid, most=4):
        """"抖音、快手、视频号 等 7 个": the user's own choices first, then names people know before bare
        program files (pycharm64.exe), so the list reads like the apps and sites it means."""
        mine = set(self.config["sub_rules"]); named, plain = [], []
        for key in subcats.members(category, sid, self.config):
            shown = key_name(key); friendly = shown != key or key in mine
            target = named if friendly else plain
            shown = shown if friendly else shown.removesuffix(".exe")
            if shown not in named and shown not in plain: target.append(shown)
        names = named+plain
        if not names: return ""
        return "、".join(names[:most])+(f" 等 {len(names)} 个" if len(names) > most else "")

    def sub_rows(self, category, seconds_by_id, total, before=None, most=None, span="", one_day=True):
        """Rows for a SubBars widget: biggest first, 其他 last; time (and the daily limit, for one day only),
        share, and change."""
        colours = self.sub_palette(category); limits = self.config["sub_limits"] if category == "distraction" and one_day else {}
        named = sorted(((sid, sec) for sid, sec in seconds_by_id.items() if sid and sec >= 30), key=lambda r: r[1], reverse=True)
        if most and len(named) > most: named = named[:most]
        rest = sum(seconds_by_id.values())-sum(sec for _, sec in named)
        items = named+([(subcats.OTHER, rest)] if rest >= 30 else [])
        rows = []
        for sid, seconds in items:
            name = self.sub_name(category, sid); limit = limits.get(sid, 0)*60
            time_text = short_time(seconds)+(f" / {short_time(limit)}" if limit else "")
            cells = [(time_text, P["danger"] if limit and seconds > limit else None), (share(seconds, total), SUBTLE)]
            tip = f"{name} · {short_time(seconds)} · 占{LABELS[category]} {share(seconds, total)}"
            if limit: tip += f"\n每日上限 {short_time(limit)}" + ("，已超出" if seconds > limit else "")
            if before is not None:
                diff = seconds-before.get(sid, 0.0)
                if not sum(before.values()): cells.append(("—", SUBTLE))
                elif abs(diff) < 60: cells.append(("持平", SUBTLE))
                else:
                    good = diff > 0 if category == "work" else diff < 0
                    cells.append((f"{'↑' if diff > 0 else '↓'} {short_time(abs(diff))}", TEXTS["work"] if good else P["danger"]))
                    tip += f"\n比{span}{'多' if diff > 0 else '少'} {short_time(abs(diff))}"
            members = self.sub_members_text(category, sid) if sid else ""
            tip += f"\n包括 {members}" if members else "\n没有归到任何小类的应用和网站" if not sid else ""
            rows.append((sid, name, seconds, colours[sid], cells, tip+"\n点击查看这些记录"))
        return rows

    def open_subs(self):
        self.navigate(3); self.switch_settings(2)

    def sub_key(self, app, host):
        return subcats.rule_key(app, host, self.config)

    def sub_snapshot(self):
        import copy
        saved = copy.deepcopy({key: self.config[key] for key in ("subcategories", "sub_rules", "sub_limits")})
        def restore():
            self.config.update(copy.deepcopy(saved)); self.subs_changed("已撤销。")
        return restore

    def subs_changed(self, message=None, undo=None):
        save_config(self.config_path, self.config)
        self.manager_signature = None  # limit reminders already shown today stay shown; set_sub_limit resets its own
        self.refresh(force=True)
        if self.pages.currentIndex() == 3:
            if self.settings_stack.currentIndex() == 1: self.refresh_rules()
            if self.settings_stack.currentIndex() == 2: self.refresh_subs()
        if message: self.notify(message, undo, seconds=6 if undo else 4)

    def set_sub(self, category, keys, sid):
        """Put programs / websites into a subcategory of `category`; it applies to every recorded day."""
        undo = self.sub_snapshot(); rules = self.config["sub_rules"]
        for key in keys:
            choice = rules.setdefault(key, {})
            choice.pop(category, None)
            if subcats.lookup(category, key if key.endswith(".exe") or "." not in key else "", key if "." in key and not key.endswith(".exe") else "", self.config) != sid:
                choice[category] = sid
            if not choice: rules.pop(key, None)
        names = "、".join(key_name(key) for key in keys[:2])+(f" 等 {len(keys)} 项" if len(keys) > 2 else "")
        self.subs_changed(f"{names} 归到“{self.sub_name(category, sid)}”，以前的记录也按这个统计", undo)

    def sub_menu(self, menu, category, keys, current):
        """The 小类 submenu for records of one category; `current` is the id they share, or None."""
        sub = menu.addMenu(icon("tags", ACCENT, 16), "小类")
        if category not in subcats.SUB_CATEGORIES:
            sub.setEnabled(False); return
        colours = self.sub_palette(category)
        for sid in subcats.order(category, self.config):
            action = sub.addAction(self.sub_swatch(colours[sid]), "不归类（其他）" if not sid else self.sub_name(category, sid),
                                   lambda s=sid: self.set_sub(category, keys, s))
            action.setCheckable(True); action.setChecked(sid == current)
        sub.addSeparator(); sub.addAction(icon("plus", ACCENT, 16), "新建小类…", lambda: self.add_sub(category, keys))

    @staticmethod
    def sub_swatch(colour):
        from PySide6.QtGui import QIcon, QPixmap, QPainter
        result = QIcon()
        for scale in (1.0, 1.5, 2.0):
            size = round(16*scale); pix = QPixmap(size, size); pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(colour))
            p.drawRoundedRect(4*scale, 4*scale, 8*scale, 8*scale, 2*scale, 2*scale); p.end(); pix.setDevicePixelRatio(scale); result.addPixmap(pix)
        return result

    def ask_sub_name(self, title, prompt, current=""):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, prompt, text=current)
        name = name.strip()[:subcats.NAME_LIMIT] if ok else ""
        if not name: return ""
        taken = {item["name"] for cat in subcats.SUB_CATEGORIES for item in self.config["subcategories"][cat]} - {current}
        if name in taken:
            QMessageBox.information(self, "名字重复", f"已经有一个叫“{name}”的小类了，换个名字吧。"); return ""
        return name

    def add_sub(self, category, keys=None):
        items = self.config["subcategories"][category]
        if len(items) >= subcats.MOST:
            self.notify(f"{LABELS[category]}最多 {subcats.MOST} 个小类。"); return
        name = self.ask_sub_name("新建小类", f"新的{LABELS[category]}小类叫什么？（最多 {subcats.NAME_LIMIT} 个字）")
        if not name: return
        undo = self.sub_snapshot(); sid = subcats.new_id(self.config); items.append({"id": sid, "name": name})
        if keys: return self.set_sub(category, keys, sid)
        self.subs_changed(f"已新建小类“{name}”。在活动管理或分类规则里右键，把应用归进来。", undo)

    def rename_sub(self, category, sid):
        item = next(i for i in self.config["subcategories"][category] if i["id"] == sid)
        name = self.ask_sub_name("小类改名", "改成：", item["name"])
        if not name or name == item["name"]: return
        undo = self.sub_snapshot(); old = item["name"]; item["name"] = name
        self.subs_changed(f"“{old}”改名为“{name}”", undo)

    def delete_sub(self, category, sid):
        undo = self.sub_snapshot(); items = self.config["subcategories"][category]
        name = self.sub_name(category, sid)
        items[:] = [i for i in items if i["id"] != sid]
        for key in list(self.config["sub_rules"]):
            choice = self.config["sub_rules"][key]
            if choice.get(category) == sid: choice.pop(category)
            if not choice: self.config["sub_rules"].pop(key)
        self.config["sub_limits"].pop(sid, None)
        self.subs_changed(f"已删除“{name}”，原来的应用归到其他{LABELS[category]}", undo)

    def reset_subs(self):
        """Bring back the built-in subcategories and their names; the user's own ones and choices stay."""
        undo = self.sub_snapshot(); defaults = subcats.default_list()
        for category in subcats.SUB_CATEGORIES:
            mine = [i for i in self.config["subcategories"][category] if i["id"] not in {d["id"] for d in defaults[category]}]
            self.config["subcategories"][category] = (defaults[category]+mine)[:subcats.MOST]
        self.subs_changed("已恢复默认小类（你新建的小类和设置保持不变）", undo)

    def set_sub_limit(self, sid, minutes):
        shown = getattr(self, "subs_signature", None) == self.subs_state()  # the list on screen is up to date
        if minutes: self.config["sub_limits"][sid] = minutes
        else: self.config["sub_limits"].pop(sid, None)
        # Its spin box already shows the new value: keep the list instead of rebuilding it under the cursor.
        if shown: self.subs_signature = self.subs_state()
        self.goal_flags.pop("sub:"+sid, None); self.sub_limit_timer.start()

    def subs_state(self):
        import json
        return json.dumps([self.config[k] for k in ("subcategories", "sub_rules", "sub_limits")], sort_keys=True, ensure_ascii=False)

    def refresh_subs(self):
        """设置 → 小类与上限: one row per subcategory; rebuilt only when the subcategories changed."""
        from PySide6.QtWidgets import QSpinBox, QSizePolicy
        state = self.subs_state()
        if state == getattr(self, "subs_signature", None): return
        self.subs_signature = state
        for category, rows in self.sub_lists.items():
            while rows.count():
                item = rows.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            items = self.config["subcategories"][category]; colours = self.sub_palette(category)
            set_text(self.sub_counts[category], f"{len(items)} 个")
            for index, item in enumerate(items):
                sid = item["id"]; line = QFrame(); line.setObjectName("subrow")
                line.setStyleSheet(f"QFrame#subrow {{ border-top:1px solid {P['row_line']}; }}" if index else "")
                row = QHBoxLayout(line); row.setContentsMargins(0, 8, 0, 8); row.setSpacing(10)
                swatch = QFrame(); swatch.setFixedSize(12, 12); swatch.setStyleSheet(f"background:{colours[sid]};border-radius:3px;border:0;")
                row.addWidget(swatch)
                title = text(item["name"]); title.setStyleSheet("font-size:14px;font-weight:600;"); title.setMinimumWidth(72); row.addWidget(title)
                members = self.sub_members_text(category, sid, 3)
                detail = ElidedLabel(members or "还没有应用或网站")
                detail.setToolTip(self.sub_members_text(category, sid, 40) or "在活动管理或分类规则里右键，选“小类”把应用归进来")
                row.addWidget(detail, 1)
                if category == "distraction":
                    limit = QSpinBox(); limit.setRange(0, 600); limit.setSingleStep(5); limit.setSuffix(" 分钟"); limit.setSpecialValueText("不设上限")
                    limit.setValue(self.config["sub_limits"].get(sid, 0)); limit.setFixedWidth(124)
                    limit.setToolTip(f"“{item['name']}”每天最多多少分钟；到了提醒一次，总览里也会标出来")
                    limit.valueChanged.connect(lambda value, s=sid: self.set_sub_limit(s, value)); row.addWidget(limit)
                for kind, tip, command in (("pen", "改名", lambda checked=False, s=sid, c=category: self.rename_sub(c, s)),
                                           ("close", "删除这个小类（可撤销）", lambda checked=False, s=sid, c=category: self.delete_sub(c, s))):
                    b = button("", command, kind=kind); b.setProperty("role", "icon"); b.setToolTip(tip); b.setFocusPolicy(Qt.FocusPolicy.TabFocus); row.addWidget(b)
                rows.addWidget(line)

    def check_sub_limits(self, today):
        """One reminder per subcategory per day once its own daily limit is reached."""
        limits = self.config["sub_limits"]
        if not limits: return
        spent = subcats.totals(self.store.details(today), self.config)["distraction"]
        for sid, minutes in limits.items():
            flag = "sub:"+sid; used = spent.get(sid, 0.0)
            if self.goal_flags.get(flag) or used < minutes*60: continue
            self.goal_flags[flag] = True; name = self.sub_name("distraction", sid)
            self.notify(f"{name}今天已经 {short_time(used)}，到了你设的 {minutes} 分钟上限。", label="", seconds=8)
            self.tray_message(f"{name}到上限了", f"今天已 {short_time(used)}，上限 {minutes} 分钟。", QSystemTrayIcon.MessageIcon.Warning, 6000)
            if self.config["reminder_sound"]: self.beep()

    def sub_limit_note(self, activity):
        """For the reminder: how much of this subcategory's daily limit is used."""
        sid = subcats.lookup(activity.category, activity.app, activity.host, self.config)
        minutes = self.config["sub_limits"].get(sid) if sid else None
        if not minutes: return ""
        used = subcats.totals(self.store.details(datetime.now().date().isoformat()), self.config)["distraction"].get(sid, 0.0)
        return f"{self.sub_name('distraction', sid)}今天 {short_time(used)} / 上限 {minutes} 分钟"

    def category_filtered(self):
        if self.manager_sub and self.manager_sub[0] != self.category_filter.currentData(): self.manager_sub = None
        self.refresh_manager(force=True)

    def clear_sub_filter(self):
        self.manager_sub = None; self.refresh_manager(force=True)

    # ── tray and window ───────────────────────────────────────────────
    def build_tray(self):
        self.tray = QSystemTrayIcon(icon("logo", ACCENT, 32), self)
        self.tray.setToolTip("专注时间助手 · 正在记录")
        menu = QMenu()
        self.tray_today = menu.addAction(""); self.tray_today.setEnabled(False); menu.addSeparator()
        menu.aboutToShow.connect(self.update_tray_menu)
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

    def update_tray_menu(self):
        totals = self.store.totals(datetime.now().date().isoformat()); goal = self.config["work_goal_minutes"]*60
        self.tray_today.setText(f"今天工作 {short_time(totals['work'])}" + (f" / {short_time(goal)}" if goal else "") + f" · 摸鱼 {short_time(totals['distraction'])}")

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
        if getattr(self, "first_run", False) and not self.testing:
            self.first_run = False; QTimer.singleShot(350, self.show_welcome)

    def show_welcome(self):
        """First start only: say what the program does and take a daily goal."""
        dialog = WelcomeDialog(self, self.config["work_goal_minutes"], self.autostart_enabled())
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        goal, autostart = dialog.values()
        self.goal_spin.setValue(goal)
        if autostart != self.autostart_check.isChecked(): self.autostart_check.setChecked(autostart)
        self.preferences_timer.stop(); self.save_preferences()

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
        # Never resized by the user (still the old built-in size, or nothing saved): the new default.
        if (not self.config.get("window") or self.size() == self.OLD_DEFAULT) and not self.isMaximized(): self.place_default()
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
        except Exception:
            logging.exception("Sampling failed"); self.accountant.previous = None
            if not self.last_error:
                self.last_error = "采样暂时失败，此段未计时。"; self.notify(self.last_error)
            self.sidebar_status.setText("●  记录异常")
        # Redrawing the window is separate from recording: a display bug is logged but never costs recorded time.
        try:
            self.refresh()
        except Exception:
            logging.exception("Refresh failed")

    def expire_learning(self, mono):
        for site, until in list(self.learning.items()):
            if until <= mono:
                self.learning.pop(site); self.notify(f"继续摸鱼的 {self.config['learning_minutes']} 分钟到了，{app_name(site)} 会照常提醒。")

    def track_away(self, current, age, locked):
        """Remember when an away stretch began; when the user returns after a long one, ask once."""
        away = self.idle_now
        if not away: self.last_active = current  # what the user was on when the input stopped, for the question on return
        if away and self.away_since is None:
            self.away_since = datetime.now()-timedelta(seconds=0 if locked else max(0, age-self.config["idle_seconds"]))
            self.away_from = getattr(self, "last_active", None)
        elif not away and self.away_since is not None:
            began, self.away_since = self.away_since, None
            threshold = self.config["return_prompt_minutes"]*60; ended = datetime.now()
            if threshold and not self.paused and (ended-began).total_seconds() >= threshold:
                self.close_return_prompt()
                self.return_prompt = ReturnPrompt(self, began, ended, getattr(self, "away_from", None))
                # The card deletes itself when closed; drop the reference with it.
                self.return_prompt.destroyed.connect(lambda *_: setattr(self, "return_prompt", None))
                self.return_prompt.show()

    def close_return_prompt(self):
        import shiboken6
        if self.return_prompt is not None and shiboken6.isValid(self.return_prompt): self.return_prompt.close()
        self.return_prompt = None

    def count_away_as(self, category, began, ended):
        """The away stretch [began, ended] becomes work or distraction, under "离开期间（补记）"; undoable."""
        self.settle()
        moved = self.store.move_minutes(began, ended, ("idle", "", ""), (category, AWAY_LABEL, ""))
        def undo():
            self.settle(); self.store.move_minutes(began, ended, (category, AWAY_LABEL, ""), ("idle", "", ""))
            self.notify("已改回离开。"); self.records_changed(); self.refresh(force=True)
        self.notify(f"已把这 {short_time(moved)} 算作{LABELS[category]}", undo, seconds=8)
        self.records_changed(); self.refresh(force=True)

    def count_away_as_work(self, began, ended):
        self.count_away_as("work", began, ended)

    def check_app_limits(self, today):
        """One reminder per program / website per day when its own daily limit is reached."""
        for key, minutes in self.config["app_limits"].items():
            flag = "app:"+key
            if self.goal_flags.get(flag): continue
            used = self.store.used_today(today, key)
            if used >= minutes*60:
                self.goal_flags[flag] = True; name = key_name(key)
                self.notify(f"{name} 今天已用 {short_time(used)}，到了你设的 {minutes} 分钟上限。", label="", seconds=8)
                self.tray_message(f"{name} 到上限了", f"今天已用 {short_time(used)}，上限 {minutes} 分钟。", QSystemTrayIcon.MessageIcon.Warning, 6000)
                if self.config["reminder_sound"]: self.beep()

    def set_app_limit(self, key):
        """Ask for a daily limit in minutes for a program or website; 0 removes it."""
        from PySide6.QtWidgets import QInputDialog
        current = self.config["app_limits"].get(key, 0)
        minutes, ok = QInputDialog.getInt(self, "每日上限", f"“{key_name(key)}”每天最多用多少分钟？\n到了提醒一次，填 0 取消上限。", current, 0, 1440, 5)
        if not ok or minutes == current: return
        if minutes: self.config["app_limits"][key] = minutes
        else: self.config["app_limits"].pop(key, None)
        self.goal_flags.pop("app:"+key, None); save_config(self.config_path, self.config); self.refresh_rules()
        self.notify(f"{key_name(key)} 每日上限：{minutes} 分钟。" if minutes else f"已取消 {key_name(key)} 的每日上限。")

    def rename_site(self, host):
        """The user's own name for a website; it covers subdomains (saved without "www.")."""
        domain = host[4:] if host.startswith("www.") else host
        self.rename("site_names", domain, theme.site_default(domain) or domain, "给网站改名",
                    f"{domain} 显示为（包括它的子域名）")

    def rename_app(self, app):
        """The user's own name for a program, e.g. weixin.exe → 微信."""
        self.rename("app_names", app, theme.app_default(app) or app, "给应用改名", f"{app} 显示为")

    def rename(self, store, key, default, title, prompt):
        """Ask for a display name; an empty name (or the default) restores the default. Undoable."""
        from PySide6.QtWidgets import QInputDialog
        names = self.config[store]
        name, ok = QInputDialog.getText(self, title, f"{prompt}；留空恢复为“{default}”：", text=names.get(key) or default)
        if not ok: return
        name = name.strip()[:30]; before = names.get(key)
        if not name or name in (default, key): names.pop(key, None)
        else: names[key] = name
        if names.get(key) == before: return
        def apply():
            theme.SITE_ALIASES = self.config["site_names"]; theme.APP_ALIASES = self.config["app_names"]
            save_config(self.config_path, self.config); self.redraw_names()
        def undo():
            if before is None: names.pop(key, None)
            else: names[key] = before
            apply()
        apply()
        self.notify(f"{key} 以后显示为“{names.get(key, default)}”。", undo, seconds=6)

    def redraw_names(self):
        self.manager_signature = None
        self.refresh(force=True)
        if self.pages.currentIndex() != 3: self.refresh_rules()

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
            _, app, host = max(used); text += f"用得最多：{label(app, host)}。"
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

    def restore_data(self):
        """Replace every record with a backup file; the present records are saved aside first."""
        from PySide6.QtWidgets import QFileDialog
        import sqlite3
        path, _ = QFileDialog.getOpenFileName(self, "选择要恢复的备份", str(self.project/"exports"), "记录数据库 (*.sqlite3)")
        if not path: return
        live = self.data_dir/"activity.sqlite3"
        if Path(path).resolve() == live.resolve():
            QMessageBox.information(self, "这是正在使用的记录", "选中的就是程序现在用的记录文件，不需要恢复。"); return
        try:
            days, first, last = self.inspect_backup(path)
        except (OSError, ValueError):
            QMessageBox.warning(self, "不能用这个文件恢复", "它不是本程序的备份，或者文件已经损坏。"); return
        box = QMessageBox(self); box.setIcon(QMessageBox.Icon.Question); box.setWindowTitle("恢复备份")
        box.setText(f"这份备份里有 {days} 天的记录（{first} 到 {last}）。" if days else "这份备份里没有记录。")
        box.setInformativeText("恢复后，现在的全部记录会换成备份里的内容。\n替换前会先把现在的记录另存到数据文件夹；分类规则和设置不受影响。")
        go = box.addButton("恢复", QMessageBox.ButtonRole.AcceptRole); box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not go: return
        try:
            aside = self.restore_from(path)
        except (OSError, ValueError):
            logging.exception("Restore failed"); QMessageBox.warning(self, "未能恢复", "恢复没有完成，现在的记录没有变化。"); return
        self.notify(f"已恢复 {days} 天的记录；之前的记录另存为 {aside.name}", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir))), "打开文件夹", seconds=10)

    @staticmethod
    def inspect_backup(path):
        """(days, first day, last day) of a backup file; ValueError when it is not one of ours."""
        import sqlite3
        try:
            source = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
            try:
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"usage", "minute_usage"} <= tables: raise ValueError("not a record database")
                return source.execute("SELECT COUNT(DISTINCT day), MIN(day), MAX(day) FROM usage").fetchone()
            finally:
                source.close()
        except sqlite3.Error as error:
            raise ValueError(str(error)) from error

    def restore_from(self, path):
        """Swap in the records of a backup; returns the file the present records were saved to."""
        import sqlite3
        self.inspect_backup(path); self.settle(); self.store.flush()
        aside = self.data_dir/f"恢复前的记录_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"; live = self.data_dir/"activity.sqlite3"
        try:
            keep = sqlite3.connect(aside); self.store.db.backup(keep); keep.close()
            source = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
            try: source.backup(self.store.db)
            finally: source.close()
        except sqlite3.Error as error:
            raise ValueError(str(error)) from error
        self.store.close(); self.store = Store(live); self.accountant = Accountant(self.store)  # reopen: adds what an old backup lacks
        self.compare = {}; self.streak_cache = {}; self.rhythm_cache = (None, None); self.goal_flags = {}; self.manager_signature = None
        self.refresh(force=True)
        return aside

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
        elif index == 3 and force and self.settings_stack.currentIndex() == 1: self.refresh_rules()
        elif index == 3 and force and self.settings_stack.currentIndex() == 2: self.refresh_subs()
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
        totals = self.store.totals(today); away = self.config["overview_away"]
        # Without away time the shares, the ring and the hourly bars describe only the time at the computer.
        counted = totals if away else {**totals, "idle": 0.0}; total = sum(counted.values())
        for key, value in self.card_values.items():
            set_text(value, duration(totals[key]))
            left_out = not away and key == "idle"
            set_text(self.card_shares[key], "不计入" if left_out else share(counted[key], total))
            restyle(self.card_shares[key], f"background:{P['track'] if left_out else TINTS[key]};color:{SUBTLE if left_out else TEXTS[key]};")
            restyle(value, f"color:{SUBTLE};" if left_out else "")
            self.stat_cards[key].bar.set_ratio(counted[key]/total if total else 0)
        current = self.current; category = "idle" if self.paused else current.category
        name = ("专注时间助手（本窗口）" if self.own_window else self.background if self.background else
                (site_name(current.host) if current.host else current.site) or app_name(current.app) or "等待前台活动")
        set_text(self.now_label, "记录已暂停" if self.paused else name)
        self.now_label.setToolTip(current.host or current.app or "")
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
        self.donut.set_data(counted, "今日已记录" if away else "不含离开")
        set_text(self.away_toggle, "含离开" if away else "不含离开")
        for key, value in self.legend_values.items():
            left_out = not away and key == "idle"
            set_text(value, "不计入" if left_out else share(counted[key], total))
            restyle(value, f"color:{SUBTLE};font-size:12px;" if left_out else "")
            set_text(self.legend_times[key], short_time(totals[key]) if totals[key] >= 60 else "<1分钟" if totals[key] else "—")
        hourly = {str(i).zfill(2): dict.fromkeys(CATEGORIES, 0.0) for i in range(24)}
        for hour, category, seconds in self.store.hourly(today):
            if away or category != "idle": hourly[hour][category] = seconds
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
            minutes = [(m, cat, label(app, host) or "离开 / 锁屏", host or app) for m, cat, app, host in self.store.timeline(today) if away or cat != "idle"]
            self.timeline.set_data(minutes, (first, min(24, last+1)))
        line = self.rhythm_stack.currentIndex() == 1
        set_text(self.hour_note, "时段图只含分钟记录，旧版汇总算在合计里" if total-recorded > 3 else
                 "一竖条一分钟 · 悬停查看，点击看明细" if line else "悬停查看各类时长 · 点环形图看明细")
        grouped = {}
        for cat, app, host, seconds in self.store.details(today):
            if cat == "idle": continue
            entry = grouped.setdefault((app, host), [0.0, cat, 0.0])
            entry[0] += seconds
            if seconds > entry[2]: entry[1], entry[2] = cat, seconds
        ranked = sorted(grouped.items(), key=lambda row: row[1][0], reverse=True)
        self.rank_list.set_data([(label(app, host), host or app, cat, seconds) for (app, host), (seconds, cat, _) in ranked], total)

    def toggle_away(self):
        """The quiet 含离开 / 不含离开 switch on the overview; mirrors 设置 → 总览占比."""
        away = not self.config["overview_away"]
        self.config["overview_away"] = away; save_config(self.config_path, self.config)
        self.away_combo.blockSignals(True); self.away_combo.setCurrentIndex(0 if away else 1); self.away_combo.blockSignals(False)
        self.refresh_overview(True)

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
            if before is None:  # nothing to compare with: say where the day stands against the goal / limit instead
                note, colour = "", SUBTLE
                goal, limit = self.config["work_goal_minutes"]*60, self.config["distraction_limit_minutes"]*60
                if key == "work" and goal:
                    done = totals["work"] >= goal
                    note = "已达成今天的目标" if done else f"离目标还差 {short_time(goal-totals['work'])}"; colour = TEXTS["work"] if done else SUBTLE
                elif key == "distraction" and limit:
                    over = totals["distraction"] > limit
                    note = f"超出上限 {short_time(totals['distraction']-limit)}" if over else f"离上限还有 {short_time(limit-totals['distraction'])}"
                    colour = P["danger"] if over else SUBTLE
                set_text(card.delta, note); restyle(card.delta, f"color:{colour};"); continue
            diff = totals[key]-before[key]
            if abs(diff) < 60:
                set_text(card.delta, "与昨天此时持平"); restyle(card.delta, f"color:{SUBTLE};"); continue
            good = {"work": diff > 0, "distraction": diff < 0}.get(key)
            colour = SUBTLE if good is None else TEXTS["work"] if good else P['danger']
            set_text(card.delta, f"{'↑' if diff > 0 else '↓'} 比昨天此时{'多' if diff > 0 else '少'} {short_time(abs(diff))}")
            card.delta.setToolTip("" if good is None else "颜色看好坏，箭头看多少：绿色是比昨天好（工作多了 / 摸鱼少了），红色是比昨天差（工作少了 / 摸鱼多了）")
            restyle(card.delta, f"color:{colour};font-weight:600;" if good is not None else f"color:{colour};")

    # ── time analysis ─────────────────────────────────────────────────
    def analysis_span(self):
        """(number of days, days back from today to the last one) of what the analysis page shows."""
        today = datetime.now().date()
        if self.analysis_day:
            return 1, (today-datetime.fromisoformat(self.analysis_day).date()).days
        if self.analysis_custom:
            first, last = self.custom_dates()
            return (last-first).days+1, (today-last).days
        return self.range_combo.currentData()

    RANGE_NAMES = {"week": "本周", "lastweek": "上周", "month": "本月", "lastmonth": "上月"}

    def custom_dates(self):
        """First and last day of the range chosen in the 更多 menu; 本周 / 本月 move with the calendar."""
        kind, first, last = self.analysis_custom; today = datetime.now().date()
        monday = today-timedelta(days=today.weekday()); month = today.replace(day=1)
        if kind == "week": return monday, today
        if kind == "lastweek": return monday-timedelta(days=7), monday-timedelta(days=1)
        if kind == "month": return month, today
        if kind == "lastmonth": return (month-timedelta(days=1)).replace(day=1), month-timedelta(days=1)
        return datetime.fromisoformat(first).date(), datetime.fromisoformat(last).date()

    def range_menu(self):
        self.more_range.setChecked(bool(self.analysis_custom))  # the click toggled it; the menu decides
        menu = popup_menu(self); chosen = (self.analysis_custom or ("",))[0]
        for kind, title in self.RANGE_NAMES.items():
            action = menu.addAction(title, lambda k=kind: self.choose_custom(k)); action.setCheckable(True); action.setChecked(kind == chosen)
        menu.addSeparator()
        action = menu.addAction("自定义范围…", self.pick_custom); action.setCheckable(True); action.setChecked(chosen == "custom")
        menu.exec(self.more_range.mapToGlobal(self.more_range.rect().bottomLeft()))

    def pick_custom(self):
        first, last = self.analysis_dates()
        recorded = sorted(self.streak_cache.get("totals", {}))
        dialog = RangeDialog(self, first, last, recorded[0] if recorded else None)
        if dialog.exec() == QDialog.DialogCode.Accepted: self.choose_custom("custom", *dialog.values())

    def choose_custom(self, kind, first="", last=""):
        self.analysis_day = None; self.analysis_custom = (kind, first, last)
        self.range_combo.clearSelection(); self.refresh_analysis()
        self.analysis_view.verticalScrollBar().setValue(0); fade_in(self.analysis_view.widget())

    def analysis_dates(self):
        count, back = self.analysis_span()
        last = datetime.now().date()-timedelta(days=back)
        return (last-timedelta(days=count-1)).isoformat(), last.isoformat()

    @staticmethod
    def day_name(day):
        date = datetime.fromisoformat(day).date(); today = datetime.now().date()
        tag = "今天" if date == today else "昨天" if (today-date).days == 1 else "周"+"一二三四五六日"[date.weekday()]
        return f"{date.month}月{date.day}日 {tag}"

    def range_name(self, first, last):
        if first == last: return self.day_name(first)
        a, b = datetime.fromisoformat(first), datetime.fromisoformat(last)
        return f"{a.month}月{a.day}日 – {b.month}月{b.day}日"

    def choose_range(self):
        """A range segment was clicked: leave the single-day view and any range from the 更多 menu."""
        self.analysis_day = None; self.analysis_custom = None; self.refresh_analysis()

    def show_analysis_day(self, day, keep_scroll=False):
        """Analyse one day (from the calendar, a bar or the daily table)."""
        if not day or day > datetime.now().date().isoformat(): return
        presets = {datetime.now().date().isoformat(): 0, (datetime.now().date()-timedelta(days=1)).isoformat(): 1}
        self.analysis_custom = None
        if day in presets:  # 今天 / 昨天 have their own segment
            self.analysis_day = None; self.range_combo.blockSignals(True); self.range_combo.setCurrentIndex(presets[day]); self.range_combo.blockSignals(False)
        else:
            self.analysis_day = day; self.range_combo.clearSelection()
        if self.pages.currentIndex() != 1: self.navigate(1)
        else: self.refresh_analysis()
        if not keep_scroll or self.pages.currentIndex() != 1: self.analysis_view.verticalScrollBar().setValue(0)
        self.analysis_view.horizontalScrollBar().setValue(0)

    def shift_analysis_day(self, step):
        day = datetime.fromisoformat(self.analysis_day or self.analysis_dates()[1]).date()+timedelta(days=step)
        self.show_analysis_day(day.isoformat())

    def mix_chosen(self, category):
        first, last = self.analysis_dates()
        if first == last: self.open_manager(category=category, day=last)

    def refresh_analysis(self):
        first_day, last_day = self.analysis_dates(); self.analysis_last = last_day
        today = datetime.now().date(); single = first_day == last_day
        first, last = datetime.fromisoformat(first_day).date(), datetime.fromisoformat(last_day).date()
        count = (last-first).days+1
        days = {(first+timedelta(days=i)).isoformat(): dict.fromkeys(CATEGORIES, 0.0) for i in range(count)}
        for day, totals in self.store.day_totals(first_day, last_day).items():
            if day in days: days[day].update(totals)
        summed = {key: sum(x[key] for x in days.values()) for key in CATEGORIES}
        active = [d for d in days.values() if sum(d.values())]
        # The previous stretch of the same length, for the comparison card.
        before_last = first-timedelta(days=1); before_first = before_last-timedelta(days=count-1)
        before = dict.fromkeys(CATEGORIES, 0.0)
        for totals in self.store.day_totals(before_first.isoformat(), before_last.isoformat()).values():
            for key in CATEGORIES: before[key] += totals[key]

        # Controls: which day / range, and how much of it has records.
        self.day_box.setVisible(bool(self.analysis_day)); self.day_detail.setVisible(single)
        if self.analysis_day:
            self.day_label.setText(self.day_name(self.analysis_day)); self.day_next.setEnabled(last < today)
        custom = self.analysis_custom
        self.more_range.setChecked(bool(custom)); set_text(self.more_range, self.RANGE_NAMES.get(custom[0], "自定义") if custom else "更多")
        parts = [] if self.analysis_day else [self.range_name(first_day, last_day)]
        if single and not active: parts.append("当天没有记录")
        elif not single: parts.append(f"{count} 天中 {len(active)} 天有记录")
        set_text(self.range_note, " · ".join(parts))
        self.range_note.setToolTip(self.range_note.text()); self.fit_analysis_controls()

        # KPIs
        ratio = self.focus_of(summed); present = sum(v for k, v in summed.items() if k != "idle")
        goal, limit = self.config["work_goal_minutes"]*60, self.config["distraction_limit_minutes"]*60
        set_text(self.kpis["work"], short_time(summed["work"]))
        set_text(self.kpis["work"].sub, (f"目标 {short_time(goal)} · 完成 {summed['work']/goal:.0%}" if goal else "当天工作合计") if single else
                 f"日均 {short_time(summed['work']/len(active))}" if active else "没有记录")
        set_text(self.kpis["distraction"], short_time(summed["distraction"]))
        set_text(self.kpis["distraction"].sub, (f"上限 {short_time(limit)}" + (" · 已超出" if summed["distraction"] > limit else "") if limit else "当天摸鱼合计") if single else
                 f"日均 {short_time(summed['distraction']/len(active))}" if active else "没有记录")
        set_text(self.kpis["focus"], f"{ratio:.0%}" if ratio is not None else "—")
        set_text(self.kpis["focus"].sub, "离开和系统工具不算在内")
        set_text(self.kpis["present"], short_time(present))
        set_text(self.kpis["present"].sub, f"另有离开 {short_time(summed['idle'])}" if summed["idle"] >= 60 else "除离开以外的记录")
        sessions = self.store.session_list(first_day, last_day); completed = sum(1 for row in sessions if row[5])
        set_text(self.kpis["sessions"], f"{completed} 次")
        set_text(self.kpis["sessions"].sub, f"开始 {len(sessions)} 次 · 共 {short_time(sum(row[2] for row in sessions))}" if sessions else "这段时间没有专注")

        # Composition ring: the same 含离开 / 不含离开 choice as the overview.
        away = self.config["overview_away"]; counted = summed if away else {**summed, "idle": 0.0}; total = sum(counted.values())
        self.mix_donut.hint = "点击查看当天明细" if single else ""
        self.mix_donut.set_data(counted, "当天" if single else f"{count} 天合计")
        set_text(self.mix_note, "含离开" if away else "不含离开")
        self.mix_note.setToolTip("占比是否计入离开时间；在“规则与设置 → 分析与导出 → 总览占比”里修改")
        for key, value in self.mix_values.items():
            left_out = not away and key == "idle"
            set_text(value, "不计入" if left_out else share(counted[key], total))
            restyle(value, f"color:{SUBTLE};font-size:12px;" if left_out else "")
            set_text(self.mix_times[key], short_time(summed[key]) if summed[key] >= 60 else "<1分钟" if summed[key] else "—")
            self.mix_bars[key].set_ratio(0 if left_out or not total else counted[key]/total)

        # Daily bars only make sense for more than one day; one day shows its own hours below.
        self.trend_card.setVisible(not single); self.history_card.setVisible(not single)
        if not single:
            self.trend_chart.set_data(list(days.items())); self.trend_chart.set_mark(count-1 if last == today else None)
        self.range_rank.set_data([(label(app, host), host or app, cat, seconds) for app, host, cat, seconds, _ in self.store.range_apps(first_day, last_day)],
                                 present)
        kind = self.sub_kind.currentData(); span = "前一天" if single else f"前 {count} 天"
        now_subs = subcats.totals(self.store.range_rows(first_day, last_day), self.config)[kind]
        then_subs = subcats.totals(self.store.range_rows(before_first.isoformat(), before_last.isoformat()), self.config)[kind]
        self.sub_analysis.set_data(kind, f"{LABELS[kind]} {short_time(summed[kind])}" if summed[kind] >= 60 else LABELS[kind],
                                   f"占{'当天' if single else '这段时间'}记录 {share(summed[kind], sum(counted.values()))}" if sum(counted.values()) else "",
                                   self.sub_rows(kind, now_subs, summed[kind], then_subs, span=span, one_day=single),
                                   ("这一天" if single else "这段时间")+("没有工作记录" if kind == "work" else "没有摸鱼，很专注"), 2,
                                   heads=("合计" + (" / 上限" if single and kind == "distraction" and self.config["sub_limits"] else ""), "占比", "比"+span))
        profile_days, profile = self.store.hour_profile(first_day, last_day)
        hours = {f"{h:02d}": dict.fromkeys(CATEGORIES, 0.0) for h in range(24)}
        for hour, key, seconds in profile:
            if away or key != "idle": hours[hour][key] = seconds
        self.profile_chart.set_data(list(hours.items()) if profile else []); self.profile_chart.set_mark(datetime.now().hour if single and last == today else None)
        set_text(self.profile_title, "当天每小时" if single else "一天中的时段分布")
        set_text(self.profile_note, ("这一天每个小时在做什么。" if single else f"按有分钟记录的 {profile_days} 天平均；看看自己一般在什么时段专注、什么时段分心。")
                 if profile_days else "这段时间还没有分钟记录。")

        self.refresh_rhythm(first_day, last_day)
        self.refresh_compare(summed, before, single, count, before_first, before_last)
        self.refresh_focus_story(today, first_day, last_day)
        if single: return
        self.history_table.setRowCount(len(days))
        for row, (day, totals) in enumerate(reversed(list(days.items()))):
            day_item = QTableWidgetItem(self.day_name(day))
            day_item.setData(Qt.ItemDataRole.UserRole, day); self.history_table.setItem(row, 0, day_item)
            for col, key in enumerate(CATEGORIES, 1):
                item = QTableWidgetItem(duration(totals[key]) if totals[key] else "—")
                item.setForeground(QColor(TEXTS[key] if totals[key] else SUBTLE)); self.history_table.setItem(row, col, item)
            day_ratio = self.focus_of(totals)
            item = QTableWidgetItem(f"{day_ratio:.0%}" if day_ratio is not None else "—")
            item.setForeground(QColor(INK if day_ratio is not None else SUBTLE)); self.history_table.setItem(row, len(CATEGORIES)+1, item)
        # Show every day without an inner scrollbar; the page itself scrolls.
        self.history_table.setFixedHeight(self.history_table.horizontalHeader().height()+40*len(days)+4)

    def refresh_rhythm(self, first_day, last_day):
        """工作节奏: longest stretch, deep work, switching and the usual start / end of the day."""
        now = datetime.now()  # days that are over do not change: recompute only while the range includes today
        stamp = (first_day, last_day, now.strftime("%H:%M") if last_day >= now.date().isoformat() else "")
        if getattr(self, "rhythm_cache", (None,))[0] != stamp:
            self.rhythm_cache = (stamp, work_rhythm(self.store, first_day, last_day))
        r = self.rhythm_cache[1]; rows = self.rhythm_rows; single = first_day == last_day
        clock = lambda minute: f"{int(minute)//60:02d}:{int(minute) % 60:02d}"
        def put(key, value, note=""):
            set_text(rows[key][0], value); set_text(rows[key][1], note)
        if r["longest"]:
            minutes, day, begin = r["longest"]
            put("longest", short_time(minutes*60), ("" if single else self.day_name(day.isoformat())+" ")+f"{clock(begin)} 开始")
        else: put("longest", "—", "这段时间没有工作记录")
        count, minutes = r["deep"]
        put("deep", f"{count} 段" if count else "0 段", f"连续 25 分钟以上，共 {short_time(minutes*60)}，占工作的 {minutes/max(1, r['work']):.0%}" if count else "连续 25 分钟以上的工作；试试“开始专注”")
        put("average", short_time(r["average"]*60) if r["average"] else "—", f"共 {r['runs']} 段工作" if r["runs"] else "")
        put("switches", f"{r['switches']:.0f} 次 / 小时" if r["switches"] is not None else "—",
            "每小时换几次程序或网站，越少越专注" if r["switches"] is not None else "记录满半小时后计算")
        if r["start"] is not None:
            put("hours", f"{clock(r['start'])} – {clock(r['end'])}", "第一条到最后一条记录" + ("" if single else f"（{r['days']} 天平均）"))
        else: put("hours", "—", "")

    def refresh_compare(self, now, before, single, count, before_first, before_last):
        """和上一段比: each category this time against the same length just before."""
        set_text(self.compare_title, "和前一天比" if single else f"和前 {count} 天比")
        set_text(self.compare_heads[0], "当天" if single else "这段时间"); set_text(self.compare_heads[1], "前一天" if single else "上一段")
        has_before = sum(before.values()) > 0
        for key, (this, then, change) in self.compare_cells.items():
            if key == "focus":
                a, b = self.focus_of(now), self.focus_of(before)
                set_text(this, f"{a:.0%}" if a is not None else "—"); set_text(then, f"{b:.0%}" if b is not None and has_before else "—")
                diff = (a-b)*100 if a is not None and b is not None and has_before else None
                good = None if diff is None or abs(diff) < 1 else diff > 0
                set_text(change, "—" if diff is None else "持平" if abs(diff) < 1 else f"{'↑' if diff > 0 else '↓'} {abs(diff):.0f} 个点")
            else:
                set_text(this, short_time(now[key]) if now[key] else "—"); set_text(then, short_time(before[key]) if before[key] else "—")
                diff = now[key]-before[key] if has_before and (now[key] or before[key]) else None
                good = None if diff is None or abs(diff) < 60 else {"work": diff > 0, "distraction": diff < 0}.get(key)
                set_text(change, "—" if diff is None else "持平" if abs(diff) < 60 else f"{'↑' if diff > 0 else '↓'} {short_time(abs(diff))}")
            colour = SUBTLE if good is None else TEXTS["work"] if good else P["danger"]
            restyle(change, f"color:{colour};padding:2px 0;" if good is None else
                    f"color:{colour};background:{tint(colour)};border-radius:9px;padding:2px 9px;")
        a, b = before_first, before_last
        set_text(self.compare_note, (f"上一段：{self.range_name(a.isoformat(), b.isoformat())}。绿色是变好，红色是变差。") if has_before else
                 "上一段时间没有记录，暂时无法比较。")

    def goal_streak(self, goal, force=False):
        """(current, best) goal streak; the sidebar recomputes it at most once a minute."""
        now = datetime.now(); stamp = (now.strftime("%Y-%m-%d %H:%M"), goal)
        if force or self.streak_cache.get("stamp") != stamp:
            today = now.date().isoformat(); first = (now.date()-timedelta(days=400)).isoformat()
            totals = self.store.day_totals(first, today)
            work = {day: t["work"] for day, t in totals.items()}
            self.streak_cache = {"stamp": stamp, "value": streaks(work, goal, today), "work": work, "totals": totals}
        return self.streak_cache["value"]

    def refresh_focus_story(self, today, first_day, last_day):
        """Analysis extras: calendar heatmap, streak, plain-language findings and focus sessions."""
        goal = self.config["work_goal_minutes"]*60; current, best = self.goal_streak(goal, force=True)
        self.heatmap.include_unknown = self.config["focus_includes_unknown"]
        self.heatmap.set_data(self.streak_cache.get("totals", {}), goal, today.isoformat(), first_day if first_day == last_day else None)
        rule = f"达到每日目标 {short_time(goal)}" if goal else "工作满 30 分钟"
        set_text(self.streak_label, f"连续 {current} 天{rule} · 最长 {best} 天" if best else f"还没有{rule}的日子")
        findings = insights(self.store, first_day, last_day, self.config["focus_includes_unknown"])
        if findings:
            colours = {"distraction": TEXTS["distraction"], "rank": TEXTS["distraction"], "flame": TEXTS["work"]}
            html = "".join(f"<p style='margin:0 0 12px'><span style='color:{colours.get(kind, ACCENT)}'>●</span>&nbsp;&nbsp;{line}</p>" for kind, line in findings)
        else:
            empty = not self.store.day_totals(first_day, last_day)
            html = f"<span style='color:{MUTED}'>" + (("这一天没有记录。" if first_day == last_day else "这段时间没有记录。") if empty else
                     "记录满一两天后，这里会总结你最专注的时段、最大的分心来源，以及和上一段时间的对比。") + "</span>"
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
    def open_manager(self, category="", day=None, query="", timed=None, sub=None):
        self.manager_sub = sub
        if timed is not None and timed != self.manager_timed():  # e.g. from the minute timeline: show when it happened
            self.manager_view.blockSignals(True); self.manager_view.setCurrentIndex(1 if timed else 0); self.manager_view.blockSignals(False)
            self.switch_manager_view()
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

    def manager_timed(self):
        return self.manager_view.currentData() == "time"

    def switch_manager_view(self, _=None):
        """按应用 / 按时间: the same table with other columns."""
        timed = self.manager_timed(); header = self.table.horizontalHeader()
        self.table.clearSelection(); self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(["分类", "时间", "应用", "网站", "时长"] if timed else ["分类", "应用", "网站", "累计时长", "占当日"])
        for column, width in enumerate([160, 150, 0, 0, 110] if timed else [160, 0, 0, 110, 90]):
            header.setSectionResizeMode(column, header.ResizeMode.Fixed if width else header.ResizeMode.Stretch)
            if width: self.table.setColumnWidth(column, width)
        self.change_future.setVisible(not timed)  # a stretch is a one-off fix, never a rule
        set_text(self.manager_hint, "每分钟归给占时最多的那一项 · 选中后“设为”只改这一段" if timed else "Ctrl / Shift 多选 · 右键更多操作（改小类也在这里） · 双击调整以后的规则")
        self.manager_signature = None; self.manager_shown = None; self.refresh_manager(force=True)

    def records_changed(self):
        """Recorded time was edited: drop everything cached from it."""
        self.manager_signature = None; self.compare = {}; self.streak_cache = {}; self.rhythm_cache = (None, None)

    def manager_values(self, row, total):
        cat, app, host, seconds, first, last = row
        name = app_name(app) or "离开 / 锁屏"; site = site_name(host) or "—"
        if first is None:
            return [CATEGORIES[cat], name, site, duration(seconds), f"{seconds/total:.1%}" if total else "—"]
        clock = lambda minute: f"{minute//60:02d}:{minute % 60:02d}"
        return [CATEGORIES[cat], f"{clock(first)} – {clock(last+1)}", name, site, duration(seconds)]

    def refresh_manager(self, force=False):
        day = self.manage_date.date().toString("yyyy-MM-dd"); query = self.search.text().strip().lower(); category = self.category_filter.currentData()
        timed = self.manager_timed()
        # One shape for both views: (category, app, host, seconds, first minute, last minute); the last two are None per app.
        found = [(c, a, h, s, f, l) for f, l, c, a, h, s in self.store.segments(day)] if timed else [(*row, None, None) for row in self.store.details(day)]
        subs = {(c, a, h): subcats.lookup(c, a, h, self.config) for c, a, h, *_ in found}
        wanted = self.manager_sub
        rows = [row for row in found if (not category or row[0] == category)
                and (not wanted or (row[0] == wanted[0] and subs[row[:3]] == wanted[1]))
                and (not query or query in f"{row[1]} {row[2]} {app_name(row[1])} {site_name(row[2])} {self.sub_name(row[0], subs[row[:3]]) if row[0] in subcats.SUB_CATEGORIES else ''}".lower())]
        self.sub_chip.setVisible(bool(wanted))
        if wanted: set_text(self.sub_chip, f"小类：{self.sub_name(*wanted)}")
        total = sum(self.store.totals(day).values())
        keys = tuple((c, a, h, f, l) for c, a, h, _, f, l in rows)
        signature = (day, query, category, timed, keys, wanted, tuple(subs[k[:3]] for k in keys))
        if not force and signature == self.manager_signature:
            # Same rows in the same order: update only the cells whose text changed.
            for index, row in enumerate(rows):
                for column, value in enumerate(self.manager_values(row, total)):
                    item = self.table.item(index, column)
                    if column and item.text() != value: item.setText(value)
            self.manager_rows = rows
            self.undo_button.setEnabled(self.store.can_undo())
            return
        self.manager_signature = signature
        selected = set(self.selected_keys()); scroll = self.table.verticalScrollBar().value()
        self.table.setUpdatesEnabled(False); self.table.blockSignals(True)
        self.table.clearSelection(); self.table.setRowCount(len(rows)); self.manager_rows = rows
        selection = self.table.selectionModel(); app_column = 2 if timed else 1
        for index, row in enumerate(rows):
            cat, app, host, _, first, last = row
            key = (cat, app, host, first, last) if timed else (cat, app, host)
            for column, value in enumerate(self.manager_values(row, total)):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key); item.setData(CategoryDelegate.ROLE, cat)
                    if subs[(cat, app, host)]: item.setData(CategoryDelegate.SUB, self.sub_name(cat, subs[(cat, app, host)]))
                elif column == app_column:
                    item.setToolTip(app or "离开 / 锁屏")
                    if app: item.setIcon(appicons.icon_for(app, cat))
                elif column == app_column+1 and host: item.setToolTip(host); item.setIcon(appicons.icon_for(host, cat))
                self.table.setItem(index, column, item)
            if key in selected:
                selection.select(self.table.model().index(index, 0), selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
        self.table.blockSignals(False); self.table.setUpdatesEnabled(True); self.table.verticalScrollBar().setValue(scroll)
        self.table.setVisible(bool(rows)); self.empty_label.setVisible(not rows); self.selection_changed()
        shown = sum(r[3] for r in rows)
        summary = f"共 {len(rows)} {'段' if timed else '项'} · 合计 {short_time(shown)}" + (f"（当天全部 {short_time(total)}）" if not timed and shown < total-1 else "")
        set_text(self.manager_total, summary if rows else "")
        is_today = self.manage_date.date() >= QDate.currentDate()
        self.next_day.setEnabled(not is_today); self.today_button.setEnabled(not is_today)
        self.manual_button.setEnabled(self.manage_date.date() <= QDate.currentDate())
        # The day in order ends with what just happened: open today's list at its end.
        if timed and getattr(self, "manager_shown", None) != (day, timed) and is_today: QTimer.singleShot(0, self.table.scrollToBottom)
        self.manager_shown = (day, timed)

    def add_manual(self):
        """补记时间: enter a stretch of the shown day by hand."""
        day = self.manage_date.date().toString("yyyy-MM-dd"); now = datetime.now()
        today = day == now.date().isoformat(); latest = now.hour*60+now.minute if today else 1439
        # Start from an away stretch selected in 按时间, else from the hour that just passed (noon on other days).
        chosen = next((key for key in self.selected_keys() if len(key) == 5 and key[0] == "idle" and not key[1]), None)
        if chosen: begin, end = chosen[3], min(1439, chosen[4]+1)
        else:
            end = latest if today else 12*60; end -= end % 5; begin = max(0, end-60)
        dialog = ManualDialog(self, day, begin, end, latest)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        begin, end, category, name = dialog.values(); base = datetime.fromisoformat(day)
        self.settle()
        seconds = self.store.add_manual(base+timedelta(minutes=begin), base+timedelta(minutes=end), category, name)
        self.records_changed(); self.refresh(force=True)
        if seconds < 1:
            self.notify("这段时间已经记满了，没有可补的空白。想改其中一段，切到“按时间”选中它再设分类。", seconds=8)
        else:
            self.notify(f"已补记 {short_time(seconds)}，记为{LABELS[category]}", self.undo_correction, seconds=8)

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
        self.settle(); day = self.manage_date.date().toString("yyyy-MM-dd"); timed = self.manager_timed()
        if timed:  # only the selected stretches; never a rule
            count = self.store.reclassify_span(day, [(first, last, cat, app, host) for cat, app, host, first, last in keys], target)
        else:
            count = self.store.reclassify(day, keys, target)
        remember = self.change_future.isChecked() and not timed
        if remember:
            for _, app, host in keys:
                if host: self.config["host_rules"][host] = target
                elif app.startswith(SURFACE_PREFIX): self.config["surface_rules"][app[len(SURFACE_PREFIX):]] = target
                elif app == "专注时间助手.exe": self.config["own_window"] = target; self.own_combo.setCurrentIndex(max(0, self.own_combo.findData(target)))
                elif app: self.config["app_rules"][app] = target
            save_config(self.config_path, self.config)
        self.records_changed(); self.refresh_manager(force=True)
        undo = self.undo_correction if count else None; unit = "段" if timed else "条记录"
        if target == IGNORE:
            self.notify(f"已移除 {count} {unit}的时间" + ("，以后也不再计时" if remember else ""), undo)
        else:
            self.notify(f"已设为{LABELS[target]}：修正 {count} {unit}" + ("，以后也这样记录" if remember else "（只改这几段，规则不变）" if timed else ""), undo)
        self.resample_without_charge()

    def undo_correction(self):
        self.settle()
        if self.store.undo(): self.notify("已撤销上次记录修正；分类规则保持不变。")
        self.records_changed(); self.refresh(force=True)

    def row_menu(self, position):
        if not self.selected_keys(): return
        menu = popup_menu(self)
        for key in RULE_TARGETS:
            title = "不计入" if key == IGNORE else "设为"+LABELS[key]
            menu.addAction(icon(key, COLORS[key], 16), title, lambda checked=False, k=key: self.apply_classification(k))
        menu.addSeparator(); menu.addAction("调整以后的分类规则…", lambda: self.rule_for_row(self.table.currentRow()))
        row = self.table.currentRow()
        if 0 <= row < len(self.manager_rows) and (self.manager_rows[row][2] or self.manager_rows[row][1].endswith(".exe")):
            app, host = self.manager_rows[row][1:3]
            menu.addAction(icon("target", ACCENT, 16), f"设置“{label(app, host)}”每日上限…", lambda k=host or app: self.set_app_limit(k))
            if app.endswith(".exe"): menu.addAction("给应用改名…", lambda a=app: self.rename_app(a))
            if host: menu.addAction("给网站改名…", lambda h=host: self.rename_site(h))
        # 小类 for the selected records, when they are all 工作 or all 摸鱼
        chosen = [k[:3] for k in self.selected_keys() if k[1] or k[2]]
        cats = {c for c, _, _ in chosen}
        if len(cats) == 1 and next(iter(cats)) in subcats.SUB_CATEGORIES:
            category = next(iter(cats)); keys = list(dict.fromkeys(self.sub_key(a, h) for _, a, h in chosen))
            current = {subcats.lookup(c, a, h, self.config) for c, a, h in chosen}
            menu.addSeparator(); self.sub_menu(menu, category, keys, next(iter(current)) if len(current) == 1 else None)
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
        cat, app, host = self.manager_rows[row][:3]
        if not (app or host) or app.endswith(MANUAL_MARK):
            self.notify("离开和补记的时间没有对应的程序，用上方的“设为”按钮或右键就能改分类。"); return
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
        def sub_of(entry):
            (kind, key), (category, _) = entry
            if kind == "surface" or category not in subcats.SUB_CATEGORIES: return ""
            sid = subcats.lookup(category, key if kind == "app" else "", key if kind == "host" else "", self.config)
            return self.sub_name(category, sid) if sid else ""
        self.rule_entries = [e for e in everything if (not custom_only or e[1][1] == "自定义")
                             and (not query or query in e[0][1] or query in key_name(e[0][1]).lower() or query in sub_of(e).lower())
                             and (not wanted or e[1][0] == wanted)]
        self.rules_table.setRowCount(len(self.rule_entries)); self.icon_queue = []
        for row, ((kind, key), (category, source)) in enumerate(self.rule_entries):
            limit = self.config["app_limits"].get(key)
            shown = site_name(key) if kind == "host" else app_name(key) if kind == "app" else key
            for col, value in enumerate([LABELS[category], {"host": "网站", "surface": "系统界面"}.get(kind, "应用"),
                                         f"{shown}（{key}）" if shown != key else key,
                                         f"{limit} 分钟" if limit else "—", source]):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(CategoryDelegate.ROLE, category); sub = sub_of(((kind, key), (category, source)))
                    if sub: item.setData(CategoryDelegate.SUB, sub)
                if col == 2 and kind in ("app", "host"):
                    # Icons not loaded yet are filled in a few at a time, so the page opens at once.
                    if appicons.ready(key, category): item.setIcon(appicons.icon_for(key, category))
                    else: self.icon_queue.append((item, key, category))
                if col == 4 and source == "默认": item.setForeground(QColor(SUBTLE))
                if col == 3 and not limit: item.setForeground(QColor(SUBTLE))
                self.rules_table.setItem(row, col, item)
        if self.icon_queue: QTimer.singleShot(0, self.fill_icons)
        keys = [key for key, _ in self.rule_entries]
        if selected in keys: self.rules_table.selectRow(keys.index(selected))
        else: self.rules_table.clearSelection(); self.rules_table.setCurrentCell(-1, -1)
        custom = sum(1 for _, (_, source) in everything if source == "自定义")
        shown = f" · 显示 {len(self.rule_entries)} 条" if len(self.rule_entries) != len(everything) else ""
        self.rules_count.setText(f"共 {len(everything)} 条 · 自定义 {custom} 条{shown}")

    def fill_icons(self):
        """Load one queued program / site icon per event-loop pass, keeping the window responsive."""
        import shiboken6
        for _ in range(1):
            if not self.icon_queue: return
            item, key, category = self.icon_queue.pop(0)
            if shiboken6.isValid(item) and item.tableWidget() is not None: item.setIcon(appicons.icon_for(key, category))
        QTimer.singleShot(15, self.fill_icons)

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
        if kind == "host": menu.addAction("给网站改名…", lambda: self.rename_site(key))
        elif kind == "app": menu.addAction("给应用改名…", lambda: self.rename_app(key))
        if category in subcats.SUB_CATEGORIES and kind in ("app", "host"):
            self.sub_menu(menu, category, [key], subcats.lookup(category, key if kind == "app" else "", key if kind == "host" else "", self.config))
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
        self.notify(f"{key_name(key)} 以后记为{LABELS[category]}", undo, seconds=6); self.resample_without_charge()

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
                  "export_format": self.export_format_combo.currentData(), "overview_away": self.away_combo.currentData(),
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
        self.check_app_limits(today); self.check_sub_limits(today)
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
        count, back = self.analysis_span(); first, last = self.analysis_dates()
        current = f"导出当前：{self.range_name(first, last)}"
        menu.addAction(icon("report", ACCENT, 16), current, lambda: self.export_range(count, back)); menu.addSeparator()
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
        if not count: count, back = self.analysis_span()
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
