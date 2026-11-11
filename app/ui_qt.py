"""Window layout: sidebar, five pages and reusable widgets. Behaviour lives in desktop.App."""
from PySide6.QtCore import Qt, QDate, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QStackedWidget, QScrollArea, QLineEdit, QComboBox, QDateEdit, QSpinBox, QTableWidget, QHeaderView,
    QAbstractItemView, QCheckBox, QButtonGroup, QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication, QSizePolicy, QGraphicsOpacityEffect)


def fade_in(widget, ms=170):
    """Fade a widget in, then drop the effect so normal painting stays cheap."""
    if not windows.animations_enabled(): return
    effect = QGraphicsOpacityEffect(widget); effect.setOpacity(0.0); widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget); anim.setDuration(ms); anim.setStartValue(0.0); anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
from core import CATEGORIES, EXPORT_PARTS, LABELS, OWN_WINDOW_MODES, RULE_TARGETS
from theme import ACCENT, COLORS, MUTED, INK, P, TEXTS, THEME, THEMES, TINTS, icon, font
import windows
import theme
from charts import Donut, Heatmap, StackedBars, RankList, Timeline

PAGES = [("今日总览", "overview", "今天的专注", "实时记录今天的投入、分心与离开。"),
         ("时间分析", "chart", "时间分析", "按天查看趋势；悬停读数，点击某天查看明细。"),
         ("活动管理", "list", "活动管理", "筛选记录、批量改分类，让统计符合实际。"),
         ("规则与设置", "settings", "规则与设置", "决定以后怎样记录，以及多久提醒一次。"),
         ("浏览器连接", "browser", "浏览器连接", "连接扩展后按网站域名识别，提醒更准确。")]


def theme_swatch(key):
    """Round preview of a theme: background half, accent half."""
    from theme import palette
    from PySide6.QtGui import QIcon, QPixmap
    tokens, _ = palette(key); result = QIcon()
    for scale in (1.0, 1.5, 2.0, 3.0):
        pixels = round(22*scale); pix = QPixmap(pixels, pixels); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.scale(pixels/22, pixels/22)
        p.setPen(QColor(tokens["line"])); p.setBrush(QColor(tokens["bg"])); p.drawEllipse(QRectF(1, 1, 20, 20))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(tokens["accent"])); p.drawPie(QRectF(1, 1, 20, 20), -45*16, 180*16)
        p.end(); pix.setDevicePixelRatio(scale); result.addPixmap(pix)
    return result


class KeyboardFocusRing(QObject):
    """Marks a button kbfocus=true only while it holds focus that came from Tab / Shift+Tab.

    Qt's :focus also matches the focus a window hands its first button on start-up, which
    drew a ring nobody asked for; a mouse press anywhere clears the mark as well.
    """
    KEYS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)

    @staticmethod
    def mark(widget, on):
        if isinstance(widget, QPushButton) and bool(widget.property("kbfocus")) != on:
            widget.setProperty("kbfocus", on); widget.style().unpolish(widget); widget.style().polish(widget)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.FocusIn:
            self.mark(obj, event.reason() in self.KEYS)
        elif kind == QEvent.Type.FocusOut:
            self.mark(obj, False)
        elif kind == QEvent.Type.MouseButtonPress:
            self.mark(QApplication.focusWidget(), False)
        return False


def text(value, role=None, wrap=False):
    label = QLabel(value)
    if role:
        label.setProperty("role", role)
    if wrap:
        label.setWordWrap(True)
    return label


def button(value, command, primary=False, kind=None, role=None):
    b = QPushButton(value)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary or role:
        b.setProperty("role", role or "primary")
    if kind:
        b.setIcon(icon(kind, P["on_accent"] if primary else ACCENT, 16)); b.setIconSize(QSize(16, 16))
    b.clicked.connect(command)
    return b


def restyle(widget, css):
    """Apply a style sheet only when it changes; re-polishing every second is costly."""
    if widget.property("_css") != css:
        widget.setProperty("_css", css); widget.setStyleSheet(css)


def set_text(label, value):
    if label.text() != value:
        label.setText(value)


def heading(title, kind=None):
    """Card title with a small accent icon in a soft rounded square."""
    row = QHBoxLayout(); row.setSpacing(9)
    if kind:
        chip = QFrame(); chip.setFixedSize(26, 26); chip.setStyleSheet(f"background:{P['soft']};border-radius:7px;")
        inner = QHBoxLayout(chip); inner.setContentsMargins(5, 5, 5, 5); inner.addWidget(IconLabel(icon(kind, ACCENT, 16), 16))
        row.addWidget(chip)
    row.addWidget(text(title, "heading"))
    return row


def card(title=None, name="card", margins=(20, 18, 20, 18), spacing=12, kind=None):
    frame = QFrame(); frame.setObjectName(name)
    layout = QVBoxLayout(frame); layout.setContentsMargins(*margins); layout.setSpacing(spacing)
    if title:
        row = heading(title, kind); row.addStretch(); layout.addLayout(row)
    return frame, layout


def page(scroll=True):
    content = QWidget(); content.setObjectName("canvas")
    layout = QVBoxLayout(content); layout.setContentsMargins(0, 0, 10 if scroll else 0, 4); layout.setSpacing(14)
    if not scroll:
        return content, layout
    area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(content)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area, layout


class IconLabel(QWidget):
    """Paints a QIcon at the current screen's pixel ratio (a QLabel pixmap would not follow)."""
    def __init__(self, qicon, size=20):
        super().__init__(); self.qicon = qicon; self.setFixedSize(size, size)

    def set_icon(self, qicon):
        if qicon is not self.qicon:
            self.qicon = qicon; self.update()

    def paintEvent(self, _):
        self.qicon.paint(QPainter(self), self.rect())


class EmptyState(QWidget):
    """A soft illustration for empty lists: tinted disc, icon, title and hint."""
    def __init__(self, kind, title, hint=""):
        super().__init__(); self.kind, self.title, self.hint = kind, title, hint; self.setMinimumHeight(200)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        cx, cy = self.width()/2, self.height()/2-26
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(P["soft"])); p.drawEllipse(QRectF(cx-42, cy-42, 84, 84))
        p.setBrush(QColor(P["mark"])); p.drawEllipse(QRectF(cx+24, cy-46, 18, 18)); p.drawEllipse(QRectF(cx-54, cy+18, 12, 12))
        icon(self.kind, ACCENT, 40).paint(p, int(cx-20), int(cy-20), 40, 40)
        p.setPen(QColor(P["ink"])); p.setFont(font(15, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, cy+52, self.width(), 24), Qt.AlignmentFlag.AlignHCenter, self.title)
        if self.hint:
            p.setPen(QColor(P["muted"])); p.setFont(font(13))
            p.drawText(QRectF(0, cy+78, self.width(), 22), Qt.AlignmentFlag.AlignHCenter, self.hint)


class Meter(QWidget):
    """A thin rounded progress bar painted directly (no style sheet, cheap to update)."""
    def __init__(self, color=COLORS["work"], height=6):
        super().__init__(); self.ratio = 0.0; self.color = color; self.setFixedHeight(height)

    def set_ratio(self, ratio):
        ratio = max(0.0, min(1.0, ratio or 0.0))
        if abs(ratio-self.ratio) > .002:
            self.ratio = ratio; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
        r = self.height()/2
        p.setBrush(QColor(P["track"])); p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        if self.ratio:
            p.setBrush(QColor(self.color)); p.drawRoundedRect(QRectF(0, 0, max(self.height(), self.width()*self.ratio), self.height()), r, r)


class Segmented(QFrame):
    """Pill-style segmented control; mirrors the small QComboBox API the controller uses."""
    currentIndexChanged = Signal(int)

    def __init__(self, items):
        super().__init__(); self.setObjectName("segmented"); self.items = items
        row = QHBoxLayout(self); row.setContentsMargins(3, 3, 3, 3); row.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        for index, (label, data) in enumerate(items):
            b = QPushButton(label); b.setCheckable(True); b.setCursor(Qt.CursorShape.PointingHandCursor); b.setProperty("role", "segment")
            if data in COLORS:
                b.setIcon(icon(data, COLORS[data], 14)); b.setIconSize(QSize(14, 14))
            self.group.addButton(b, index); row.addWidget(b)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.currentIndexChanged.emit)

    def currentIndex(self): return self.group.checkedId()
    def currentData(self): return self.items[max(0, self.currentIndex())][1]
    def findData(self, data): return next((i for i, (_, d) in enumerate(self.items) if d == data), -1)

    def setCurrentIndex(self, index):
        if 0 <= index < len(self.items) and index != self.currentIndex():
            self.group.button(index).setChecked(True); self.currentIndexChanged.emit(index)


class Toast(QFrame):
    """Floating message at the bottom of the window, optionally with one action (e.g. 撤销)."""
    def __init__(self, parent):
        super().__init__(parent); self.setObjectName("toast"); self.action = None
        row = QHBoxLayout(self); row.setContentsMargins(16, 10, 10, 10); row.setSpacing(14)
        self.label = QLabel(); self.label.setObjectName("toastText"); row.addWidget(self.label)
        self.button = QPushButton(); self.button.setObjectName("toastAction"); self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.run_action); row.addWidget(self.button)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.hide)
        self.slide = QPropertyAnimation(self, b"pos", self); self.slide.setDuration(200); self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    def show_message(self, message, action=None, label="撤销", seconds=4):
        self.label.setText(message); self.action = action
        self.button.setText(label); self.button.setVisible(action is not None)
        appearing = not self.isVisible()
        self.adjustSize(); self.place(); self.show(); self.raise_()
        if appearing and windows.animations_enabled():  # rise and fade in; an already visible toast just changes its text
            target = self.pos(); self.move(target+QPoint(0, 14))
            self.slide.stop(); self.slide.setEndValue(target); self.slide.start()
            fade_in(self, 200)
        self.timer.start(int(seconds*1000))

    def place(self):
        parent = self.parentWidget()
        self.move((parent.width()-self.width())//2+98, parent.height()-self.height()-46)

    def run_action(self):
        action, self.action = self.action, None
        self.hide()
        if action: action()


class CategoryDelegate(QStyledItemDelegate):
    """Keeps the category colour (dot + text) visible on selected rows."""
    ROLE = Qt.ItemDataRole.UserRole+1

    def __init__(self, parent=None, chevron=False):
        super().__init__(parent); self.chevron = chevron  # chevron: the cell opens a category menu when clicked

    def paint(self, painter, option, index):
        key = index.data(self.ROLE)
        if key not in COLORS:
            return super().paint(painter, option, index)
        painter.save()
        # Background, hover, selection and the row divider come from the stylesheet, exactly like the
        # other columns; only the text is drawn here, in the category colour.
        background = QStyleOptionViewItem(option); self.initStyleOption(background, index); background.text = ""
        widget = option.widget
        (widget.style() if widget else QApplication.style()).drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(12, 0, -4, 0)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(COLORS[key]))
        painter.drawEllipse(QRectF(rect.x(), rect.center().y()-3.5, 7, 7))
        painter.setPen(QColor(TEXTS[key])); painter.setFont(option.font)
        painter.drawText(rect.adjusted(15, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, index.data())
        if self.chevron:
            x = rect.x()+15+painter.fontMetrics().horizontalAdvance(index.data())+7; y = rect.center().y()
            pen = QPen(QColor(COLORS[key] if option.state & QStyle.StateFlag.State_MouseOver else P["subtle"]), 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap); painter.setPen(pen)
            painter.drawPolyline([QPointF(x, y-1.5), QPointF(x+3.5, y+2), QPointF(x+7, y-1.5)])
        painter.restore()


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, key, title):
        super().__init__(); self.setObjectName("stat"); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"查看今天的{title}明细")
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 12, 18, 12); layout.setSpacing(2)
        top = QHBoxLayout(); top.setSpacing(8)
        chip = QFrame(); chip.setFixedSize(26, 26); chip.setStyleSheet(f"background:{TINTS[key]};border-radius:8px;")
        inner = QHBoxLayout(chip); inner.setContentsMargins(5, 5, 5, 5); inner.addWidget(IconLabel(icon(key, COLORS[key], 16), 16))
        top.addWidget(chip); top.addWidget(text(title, "muted")); top.addStretch()
        self.share = text("—", "small"); top.addWidget(self.share); layout.addLayout(top)
        self.value = text("00:00:00", "value"); layout.addWidget(self.value)
        self.delta = text("", "small"); layout.addWidget(self.delta)
        self.bar = Meter(COLORS[key], 4); layout.addSpacing(4); layout.addWidget(self.bar)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


def kpi(title, parent_layout, kind="chart"):
    frame, inside = card(name="kpi", margins=(16, 12, 16, 12), spacing=2)
    top = QHBoxLayout(); top.setSpacing(8)
    chip = QFrame(); chip.setFixedSize(22, 22); chip.setStyleSheet(f"background:{P['soft']};border-radius:6px;")
    inner = QHBoxLayout(chip); inner.setContentsMargins(4, 4, 4, 4); inner.addWidget(IconLabel(icon(kind, ACCENT, 14), 14))
    top.addWidget(chip); top.addWidget(text(title, "small")); top.addStretch()
    inside.addLayout(top); value = text("—", "kpi"); inside.addWidget(value)
    parent_layout.addWidget(frame, 1)
    return value


def table(headers, widths=None):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide(); t.verticalHeader().setDefaultSectionSize(40)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setShowGrid(False); t.setWordWrap(False); t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    header = t.horizontalHeader(); header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False); header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    for column, width in enumerate(widths or []):
        if width:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed); t.setColumnWidth(column, width)
    t.setItemDelegateForColumn(0, CategoryDelegate(t))
    t.setMinimumHeight(160)
    return t


def legend():
    row = QHBoxLayout(); row.setSpacing(14)
    for key, value in CATEGORIES.items():
        label = text("●  "+value); label.setStyleSheet(f"color:{TEXTS[key]};font-size:12px;")
        row.addWidget(label)
    return row


class Shell(QMainWindow):
    def build(self):
        self.setWindowTitle("专注时间助手")
        self.resize(1240, 820); self.setMinimumSize(980, 660)
        canvas = QWidget(); canvas.setObjectName("canvas"); self.setCentralWidget(canvas)
        columns = QHBoxLayout(canvas); columns.setContentsMargins(0, 0, 0, 0); columns.setSpacing(0)
        columns.addWidget(self.build_sidebar())
        right = QWidget(); region = QVBoxLayout(right); region.setContentsMargins(28, 22, 24, 12); region.setSpacing(16)
        header = QHBoxLayout(); titles = QVBoxLayout(); titles.setSpacing(3)
        self.page_title = text("", "title"); self.page_subtitle = text("", "muted")
        titles.addWidget(self.page_title); titles.addWidget(self.page_subtitle); header.addLayout(titles); header.addStretch()
        self.date_label = text("", "muted"); header.addWidget(self.date_label); header.addSpacing(12)
        self.focus_button = button("开始专注", self.focus_clicked, kind="focus"); self.focus_button.setToolTip("开始一段专注计时（Ctrl+Shift+F）")
        header.addWidget(self.focus_button); header.addSpacing(6)
        self.pause_button = button("暂停记录", self.pause_clicked, kind="pause"); self.pause_button.setToolTip("暂停计时与提醒，可选暂停多久（Ctrl+P 直接暂停）")
        header.addWidget(self.pause_button); region.addLayout(header)
        self.pages = QStackedWidget(); region.addWidget(self.pages, 1)
        self.build_overview(); self.build_analysis(); self.build_manager(); self.build_settings(); self.build_browser()
        foot = QHBoxLayout()
        self.status = text("", "small"); foot.addWidget(self.status, 1)
        self.footer = text("关闭窗口后在托盘继续记录 · Ctrl+1–5 切换页面 · Ctrl+Shift+F 专注 · Ctrl+P 暂停 · Ctrl+F 搜索", "small"); foot.addWidget(self.footer)
        region.addLayout(foot)
        columns.addWidget(right, 1)
        self.toast = Toast(canvas)
        self.navigate(0)
        # Buttons take focus from Tab only: a mouse click leaves no ring, keyboard users always see one.
        for b in self.findChildren(QPushButton): b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.focus_ring = KeyboardFocusRing(self); QApplication.instance().installEventFilter(self.focus_ring)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast") and self.toast.isVisible(): self.toast.place()

    def build_sidebar(self):
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(196)
        nav = QVBoxLayout(sidebar); nav.setContentsMargins(14, 24, 14, 18); nav.setSpacing(4)
        brand = QHBoxLayout(); brand.setContentsMargins(8, 0, 0, 0); brand.setSpacing(10)
        brand.addWidget(IconLabel(icon("logo", ACCENT, 28), 28))
        name = text("专注时间"); name.setStyleSheet("font-size:18px;font-weight:700;"); brand.addWidget(name); brand.addStretch()
        nav.addLayout(brand); nav.addSpacing(26)
        self.nav_buttons = []; self.nav_group = QButtonGroup(self)
        for index, (title, kind, _, _) in enumerate(PAGES):
            b = QPushButton(title); b.setCursor(Qt.CursorShape.PointingHandCursor); b.setCheckable(True); b.setProperty("role", "nav")
            b.setToolTip(f"Ctrl+{index+1}")
            b.setIcon(icon(kind, P["nav"], 18)); b.setIconSize(QSize(18, 18)); b.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav_group.addButton(b, index); self.nav_buttons.append(b); nav.addWidget(b)
        nav.addStretch()
        # Today at a glance, visible from every page.
        today, inside = card(name="note", margins=(14, 12, 14, 12), spacing=4)
        today.setCursor(Qt.CursorShape.PointingHandCursor); today.setToolTip("回到今日总览")
        today.mousePressEvent = lambda _event: self.navigate(0)
        head = QHBoxLayout(); head.addWidget(text("今日工作", "small")); head.addStretch()
        self.side_goal = text("", "small"); head.addWidget(self.side_goal); inside.addLayout(head)
        self.side_work = text("0 分钟"); self.side_work.setStyleSheet(f"font-family:'{theme.NUM_FONT}','{theme.UI_FONT}';font-size:20px;font-weight:600;color:{COLORS['work']};")
        inside.addWidget(self.side_work)
        self.side_meter = Meter(COLORS["work"], 5); inside.addSpacing(4); inside.addWidget(self.side_meter)
        self.side_focus = text("专注率 —", "small"); inside.addWidget(self.side_focus)
        self.side_focus.setToolTip("专注率 = 工作 ÷（工作 + 摸鱼 + 待分类），不含离开和不计入的时间")
        self.side_limit = text("", "small"); inside.addWidget(self.side_limit)
        self.side_streak = text("", "small"); inside.addWidget(self.side_streak)
        self.side_streak.setToolTip("连续达到每日工作目标的天数（没有目标时按每天工作满 30 分钟算）；今天还没达成不会中断")
        nav.addWidget(today); nav.addSpacing(10)
        self.sidebar_status = text("●  正在记录"); self.sidebar_status.setContentsMargins(8, 0, 0, 0); nav.addWidget(self.sidebar_status)
        return sidebar

    def navigate(self, index):
        _, _, title, subtitle = PAGES[index]
        changed = index != self.pages.currentIndex()
        self.pages.setCurrentIndex(index)
        if changed and self.isVisible(): fade_in(self.pages.currentWidget())
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == index); b.setIcon(icon(PAGES[i][1], ACCENT if i == index else P["nav"], 18))
        self.page_title.setText(title); self.page_subtitle.setText(subtitle)
        if hasattr(self, "store"):
            self.refresh(force=True)

    def build_overview(self):
        view, layout = page()
        # Current activity: one compact strip instead of a tall, mostly empty card.
        strip = QFrame(); strip.setObjectName("card")
        row = QHBoxLayout(strip); row.setContentsMargins(18, 14, 18, 14); row.setSpacing(14)
        self.now_icon = IconLabel(icon("unknown", COLORS["unknown"], 26), 26)
        self.now_badge = QFrame(); self.now_badge.setFixedSize(44, 44)
        badge_layout = QHBoxLayout(self.now_badge); badge_layout.setContentsMargins(9, 9, 9, 9); badge_layout.addWidget(self.now_icon)
        row.addWidget(self.now_badge)
        info = QVBoxLayout(); info.setSpacing(2)
        top = QHBoxLayout(); top.setSpacing(8)
        top.addWidget(text("此刻", "small")); self.running_badge = text("识别中", "pill"); top.addWidget(self.running_badge)
        self.streak_label = text("", "small"); self.streak_label.setToolTip("当前这项活动连续进行的时间")
        top.addWidget(self.streak_label)
        self.learning_label = text("", "small"); top.addWidget(self.learning_label); top.addStretch(); info.addLayout(top)
        self.now_label = text("正在识别前台应用", "now"); self.now_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.now_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred); info.addWidget(self.now_label)
        self.source_label = text("", "muted"); info.addWidget(self.source_label)
        row.addLayout(info, 1)
        self.learning_button = button("继续摸鱼 30 分钟", self.allow_learning); row.addWidget(self.learning_button)
        self.cancel_learning_button = button("恢复提醒", self.cancel_learning); self.cancel_learning_button.hide(); row.addWidget(self.cancel_learning_button)
        self.classify_current = button("调整分类", self.current_rule, primary=True); row.addWidget(self.classify_current)
        layout.addWidget(strip)

        stats = QHBoxLayout(); stats.setSpacing(12)
        self.card_values = {}; self.card_shares = {}; self.stat_cards = {}
        for key, title in CATEGORIES.items():
            stat = StatCard(key, title+"时间" if key in ("work", "distraction", "idle") else title)
            stat.clicked.connect(lambda k=key: self.open_manager(category=k))
            self.card_values[key] = stat.value; self.card_shares[key] = stat.share; self.stat_cards[key] = stat
            stats.addWidget(stat, 1)
        layout.addLayout(stats)

        bottom = QHBoxLayout(); bottom.setSpacing(12)
        rhythm, rl = card()
        head = heading("今天的时间节奏", "timeline"); head.addSpacing(10)
        self.rhythm_mode = Segmented([("按小时", "hour"), ("时间线", "line")]); head.addWidget(self.rhythm_mode)
        head.addStretch(); head.addLayout(legend()); rl.addLayout(head)
        self.rhythm_stack = QStackedWidget()
        self.hour_chart = StackedBars(hourly=True); self.rhythm_stack.addWidget(self.hour_chart)
        self.timeline = Timeline(); self.timeline.chosen.connect(lambda query: self.open_manager(query=query)); self.rhythm_stack.addWidget(self.timeline)
        self.rhythm_mode.currentIndexChanged.connect(self.switch_rhythm)
        rl.addWidget(self.rhythm_stack, 1)
        self.hour_note = text("", "small"); rl.addWidget(self.hour_note)
        bottom.addWidget(rhythm, 7)
        side = QVBoxLayout(); side.setSpacing(12)
        spread, sl = card("时间分布", margins=(20, 16, 20, 16), spacing=8, kind="pie")
        ring = QHBoxLayout(); ring.setSpacing(16)
        self.donut = Donut(); self.donut.chosen.connect(lambda key: self.open_manager(category=key)); ring.addWidget(self.donut)
        rows = QVBoxLayout(); rows.setSpacing(6); rows.addStretch(); self.legend_values = {}
        for key, title in CATEGORIES.items():
            line = QHBoxLayout()
            dot = text("●  "+title); dot.setStyleSheet(f"color:{TEXTS[key]};font-size:13px;"); line.addWidget(dot); line.addStretch()
            value = text("—", "muted"); self.legend_values[key] = value; line.addWidget(value); rows.addLayout(line)
        rows.addStretch(); ring.addLayout(rows, 1); sl.addLayout(ring)
        side.addWidget(spread)
        ranking, kl = card()
        head = heading("主要活动", "rank"); head.addStretch()
        head.addWidget(button("全部 →", lambda: self.open_manager(), role="link")); kl.addLayout(head)
        self.rank_list = RankList(6); self.rank_list.chosen.connect(lambda query: self.open_manager(query=query)); kl.addWidget(self.rank_list, 1)
        side.addWidget(ranking, 1)
        bottom.addLayout(side, 4)
        layout.addLayout(bottom, 1)
        self.pages.addWidget(view)

    def build_analysis(self):
        view, layout = page()
        controls = QHBoxLayout(); controls.setSpacing(10)
        # (number of days, days back from today to the last one): 昨天 ends yesterday, the rest end today.
        self.range_combo = Segmented([("昨天", (1, 1)), ("最近 3 天", (3, 0)), ("最近 7 天", (7, 0)), ("最近 30 天", (30, 0))])
        self.range_combo.setCurrentIndex(2)
        self.range_combo.currentIndexChanged.connect(lambda _: self.refresh_analysis()); controls.addWidget(self.range_combo)
        controls.addStretch()
        self.export_button = button("导出分析报告", self.export_menu, True, kind="report")
        self.export_button.setToolTip("选择导出今天、最近 7 天或最近 30 天；格式和内容在“规则与设置 → 分析与导出”里设置")
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        tiles = QHBoxLayout(); tiles.setSpacing(12)
        self.kpis = {"work": kpi("工作合计", tiles, "work"), "average": kpi("有记录日平均工作", tiles, "calendar"),
                     "distraction": kpi("摸鱼合计", tiles, "distraction"), "focus": kpi("专注率", tiles, "target"),
                     "sessions": kpi("完成专注", tiles, "flame")}
        self.kpis["focus"].parentWidget().setToolTip("工作 ÷（工作 + 摸鱼 + 待分类），可在设置里改为不含待分类")
        layout.addLayout(tiles)
        pair = QHBoxLayout(); pair.setSpacing(12)
        panel, inside = card()
        head = heading("专注日历", "calendar"); head.addStretch(); self.streak_label = text("", "muted"); head.addWidget(self.streak_label)
        inside.addLayout(head)
        self.heatmap = Heatmap(); self.heatmap.chosen.connect(lambda day: self.open_manager(day=day)); inside.addWidget(self.heatmap)
        pair.addWidget(panel, 3)
        panel, inside = card("要点", kind="bulb")
        self.insight_label = QLabel(); self.insight_label.setWordWrap(True); self.insight_label.setTextFormat(Qt.TextFormat.RichText)
        self.insight_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft); self.insight_label.setObjectName("insights")
        inside.addWidget(self.insight_label, 1)
        pair.addWidget(panel, 2); layout.addLayout(pair)
        panel, inside = card()
        head = heading("每日时间", "chart"); head.addStretch(); head.addLayout(legend()); inside.addLayout(head)
        self.trend_chart = StackedBars(); self.trend_chart.setMinimumHeight(260); self.trend_chart.chosen.connect(lambda day: self.open_manager(day=day)); inside.addWidget(self.trend_chart)
        inside.addWidget(text("柱高是已记录的时长，不是开机时长；空白表示当天没有记录。点击柱子查看当天明细。", "small")); layout.addWidget(panel)
        pair = QHBoxLayout(); pair.setSpacing(12)
        panel, inside = card("应用与网站排行", kind="rank")
        self.range_rank = RankList(8); self.range_rank.setMinimumHeight(RankList.ROW*8)
        self.range_rank.chosen.connect(lambda query: self.open_manager(query=query)); inside.addWidget(self.range_rank, 1)
        pair.addWidget(panel, 1)
        panel, inside = card("一天中的时段分布", kind="clock")
        self.profile_chart = StackedBars(hourly=True); self.profile_chart.setMinimumHeight(250); inside.addWidget(self.profile_chart, 1)
        self.profile_note = text("", "small"); inside.addWidget(self.profile_note)
        pair.addWidget(panel, 1); layout.addLayout(pair)
        panel, inside = card("专注记录", kind="flame")
        self.session_table = table(["开始", "任务", "实际 / 计划", "工作 / 摸鱼", "结果"], [140, 0, 130, 220, 90])
        self.session_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.session_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); inside.addWidget(self.session_table)
        self.session_empty = text("这段时间还没有专注记录。点右上角“开始专注”，可以给这一段写上任务名。", "muted", wrap=True)
        inside.addWidget(self.session_empty); layout.addWidget(panel)
        panel, inside = card("每日汇总", kind="calendar")
        self.history_table = table(["日期", *CATEGORIES.values(), "专注率"])
        self.history_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_table.setToolTip("双击某天查看活动明细")
        self.history_table.cellDoubleClicked.connect(lambda row, _: self.open_manager(day=self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole)))
        inside.addWidget(self.history_table); layout.addWidget(panel)
        self.pages.addWidget(view)

    def build_manager(self):
        view, layout = page(scroll=False)
        controls = QHBoxLayout(); controls.setSpacing(8)
        self.prev_day = button("", lambda: self.shift_day(-1), kind="left"); self.prev_day.setToolTip("前一天")
        self.prev_day.setProperty("role", "icon"); controls.addWidget(self.prev_day)
        self.manage_date = QDateEdit(QDate.currentDate()); self.manage_date.setCalendarPopup(True); self.manage_date.setDisplayFormat("yyyy-MM-dd")
        self.manage_date.setMaximumDate(QDate.currentDate().addDays(1)); self.manage_date.setFixedWidth(158)
        self.manage_date.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # no focus ring just because the page opened
        self.manage_date.dateChanged.connect(lambda _: self.refresh_manager(force=True)); controls.addWidget(self.manage_date)
        self.next_day = button("", lambda: self.shift_day(1), kind="right"); self.next_day.setToolTip("后一天")
        self.next_day.setProperty("role", "icon"); controls.addWidget(self.next_day)
        self.today_button = button("今天", lambda: self.manage_date.setDate(QDate.currentDate())); controls.addWidget(self.today_button)
        controls.addSpacing(6)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索应用或网站（Ctrl+F）"); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh_manager(force=True)); controls.addWidget(self.search, 1)
        controls.addWidget(button("导出当天", self.export_day, kind="export")); layout.addLayout(controls)

        panel, inside = card(spacing=10)
        filters = QHBoxLayout(); filters.setSpacing(10)
        self.category_filter = Segmented([("全部", "")]+[(title, key) for key, title in CATEGORIES.items()])
        self.category_filter.currentIndexChanged.connect(lambda _: self.refresh_manager(force=True)); filters.addWidget(self.category_filter)
        filters.addStretch(); self.manager_total = text("", "small"); filters.addWidget(self.manager_total)
        inside.addLayout(filters)
        actions = QHBoxLayout(); actions.setSpacing(6)
        self.selection_label = text("", "muted"); self.selection_label.setMinimumWidth(118); actions.addWidget(self.selection_label)
        actions.addWidget(text("设为", "small"))
        self.classify_buttons = []
        for key in RULE_TARGETS:
            value = LABELS[key]; b = button(value, lambda checked=False, k=key: self.apply_classification(k)); b.setIcon(icon(key, COLORS[key], 16))
            b.setToolTip(f"把所选记录设为{value}"); self.classify_buttons.append(b); actions.addWidget(b)
        self.classify_buttons[-1].setToolTip("删除所选记录的时间（可撤销）；勾选“记住为规则”后，以后这个应用 / 网站也不再计时。")
        self.change_future = QCheckBox("记住为规则"); self.change_future.setChecked(bool(self.config.get("remember_rule", True)))
        self.change_future.setToolTip("勾选：以后同一应用 / 网站也按此分类记录。\n不勾选：只修正所选日期的已有记录。")
        self.change_future.toggled.connect(self.remember_choice); actions.addSpacing(6); actions.addWidget(self.change_future)
        actions.addStretch()
        self.undo_button = button("撤销", self.undo_correction, kind="undo")
        self.undo_button.setToolTip("撤销上次记录修正（Ctrl+Z），修改后新增的时间保留；规则不会被撤销。"); actions.addWidget(self.undo_button)
        inside.addLayout(actions)
        self.table = table(["分类", "应用", "网站域名", "累计时长", "占当日"], [110, 0, 0, 110, 90])
        self.table.setToolTip("Ctrl / Shift 多选；右键可批量设置；双击调整以后的分类规则。离开时间也能改，改后记为“离开期间（补记）”。")
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellDoubleClicked.connect(lambda row, _: self.rule_for_row(row))
        inside.addWidget(self.table, 1)
        self.empty_label = EmptyState("search", "这一天没有符合条件的记录", "换个日期、分类或搜索词试试")
        self.empty_label.hide(); inside.addWidget(self.empty_label, 1)
        layout.addWidget(panel, 1); self.pages.addWidget(view)

    def spin(self, low, high, value, suffix, step=1, off=None, tip=""):
        box = QSpinBox(); box.setRange(low, high); box.setSingleStep(step); box.setSuffix(suffix); box.setValue(value)
        if off: box.setSpecialValueText(off)  # shown at the minimum, i.e. "关闭" for 0
        box.setFixedWidth(124); box.setToolTip(tip); box.valueChanged.connect(self.schedule_preferences)
        return box

    def check(self, label, checked, tip=""):
        box = QCheckBox(label); box.setChecked(checked); box.setToolTip(tip); box.toggled.connect(self.schedule_preferences)
        return box

    @staticmethod
    def form_rows(layout, rows):
        grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(4)
        for index, (label, widget, hint) in enumerate(rows):
            grid.addWidget(text(label), index, 0, Qt.AlignmentFlag.AlignVCenter)
            cell = QHBoxLayout(); cell.setSpacing(10); cell.addWidget(widget)
            if hint: cell.addWidget(text(hint, "small"))
            cell.addStretch(); grid.addLayout(cell, index, 1); grid.setRowMinimumHeight(index, 38)
        grid.setColumnStretch(1, 1); layout.addLayout(grid)

    def build_settings(self):
        view, layout = page()
        c = self.config
        cards = QHBoxLayout(); cards.setSpacing(12)
        panel, inside = card("提醒与记录", spacing=10, kind="bell")
        self.idle_spin = self.spin(1, 120, c["idle_seconds"]//60, " 分钟", tip="超过这个时间没有键盘鼠标操作，就记为离开")
        self.remind_check = self.check("开启", c["remind_distraction"], "关闭后不再弹出摸鱼提醒，摸鱼时间照常记录；专注模式中始终提醒")
        self.reminder_spin = self.spin(15, 3600, c["reminder_seconds"], " 秒", 15, tip="停留在摸鱼网站或应用时，隔多久再提醒一次")
        self.sound_check = self.check("提醒时响一声", c["reminder_sound"])
        self.tray_hint_check = self.check("关闭窗口时提示仍在托盘记录", c["tray_hint"], "关闭主窗口后，在屏幕右下角弹一次系统通知")
        self.summary_combo = QComboBox(); self.summary_combo.setFixedWidth(150)
        self.summary_combo.addItem("关闭", "off")
        for at in ("18:00", "20:00", "21:00", "22:00", "23:00"): self.summary_combo.addItem(f"每天 {at}", at)
        self.summary_combo.setCurrentIndex(max(0, self.summary_combo.findData(c["daily_summary"])))
        self.summary_combo.setToolTip("到点弹一次今天的小结：工作、摸鱼、目标完成情况和用得最多的程序")
        self.summary_combo.currentIndexChanged.connect(self.schedule_preferences)
        self.return_combo = QComboBox(); self.return_combo.setFixedWidth(150)
        self.return_combo.addItem("不询问", 0)
        for minutes in (10, 15, 30, 60): self.return_combo.addItem(f"离开 {minutes} 分钟以上", minutes)
        self.return_combo.setCurrentIndex(max(0, self.return_combo.findData(c["return_prompt_minutes"])))
        self.return_combo.setToolTip("离开这么久再回来时，右下角问一次要不要把这段时间算作工作（开会、看纸质资料、打电话）")
        self.return_combo.currentIndexChanged.connect(self.schedule_preferences)
        self.learning_spin = self.spin(5, 240, c["learning_minutes"], " 分钟", 5, tip="在摸鱼网站或应用上点“继续摸鱼”后，这么长时间内不再提醒；时间照常算摸鱼")
        self.rest_spin = self.spin(0, 240, c["rest_after_minutes"], " 分钟", 5, "关闭", "连续使用电脑这么久后提醒休息；离开一段时间会重新计时")
        self.form_rows(inside, [("无操作多久算离开", self.idle_spin, "阅读或看视频时可以调长"),
                                ("摸鱼提醒", self.remind_check, ""), ("重复提醒间隔", self.reminder_spin, ""),
                                ("提示音", self.sound_check, ""), ("托盘通知", self.tray_hint_check, ""), ("每日总结", self.summary_combo, ""), ("回来时补记", self.return_combo, ""), ("继续摸鱼时长", self.learning_spin, "期间不提醒，照常计入摸鱼"),
                                ("休息提醒", self.rest_spin, "连续用电脑多久提醒")])
        self.reminder_spin.setEnabled(c["remind_distraction"]); self.sound_check.setEnabled(c["remind_distraction"])
        inside.addStretch(); cards.addWidget(panel, 1)
        panel, inside = card("目标、专注与启动", spacing=10, kind="target")
        self.goal_spin = self.spin(0, 960, c["work_goal_minutes"], " 分钟", 30, "不设目标", "每天的工作时长目标；侧栏显示进度，达成时提示一次")
        self.limit_spin = self.spin(0, 600, c["distraction_limit_minutes"], " 分钟", 5, "不设上限", "每天摸鱼超过这个时长时提示一次，摸鱼提醒里也会写明")
        self.focus_spin = self.spin(5, 180, c["focus_minutes"], " 分钟", 5, tip="开始专注时默认选中的时长，也是 Ctrl+Shift+F 快速开始的时长")
        self.break_spin = self.spin(0, 30, c["break_minutes"], " 分钟", 1, "不休息", "完成一段专注后自动开始休息倒计时，结束时提醒开始下一段")
        self.float_check = self.check("专注和休息时显示悬浮计时", c["focus_float"], "屏幕上的小计时窗，可拖动；右键可结束或隐藏")
        self.autostart_check = QCheckBox("登录 Windows 后自动在托盘运行"); self.autostart_check.setChecked(self.autostart_enabled())
        self.autostart_check.toggled.connect(self.set_autostart)
        self.own_combo = QComboBox(); self.own_combo.setFixedWidth(200)
        for mode, label in OWN_WINDOW_MODES.items(): self.own_combo.addItem(label, mode)
        self.own_combo.setCurrentIndex(max(0, self.own_combo.findData(c["own_window"])))
        self.own_combo.setToolTip("看本程序窗口时怎样记录。“略过”会改为记录它下面的第一个程序，\n例如一边写代码一边看统计，仍按代码编辑器计。")
        self.own_combo.currentIndexChanged.connect(self.schedule_preferences)
        self.form_rows(inside, [("每日工作目标", self.goal_spin, ""), ("每日摸鱼上限", self.limit_spin, ""),
                                ("专注时长", self.focus_spin, ""), ("专注后休息", self.break_spin, ""),
                                ("悬浮计时", self.float_check, ""), ("本程序窗口", self.own_combo, ""),
                                ("开机启动", self.autostart_check, "")])
        inside.addStretch(); cards.addWidget(panel, 1)
        layout.addLayout(cards)
        cards = QHBoxLayout(); cards.setSpacing(12)
        panel, inside = card("外观", spacing=10, kind="palette")
        inside.addWidget(text("主题配色，切换后重启生效（可点提示里的“立即重启”）。", "small"))
        swatches = QGridLayout(); swatches.setHorizontalSpacing(8); swatches.setVerticalSpacing(8)
        self.theme_buttons = QButtonGroup(self); self.theme_buttons.setExclusive(True)
        for index, (key, label) in enumerate(THEMES.items()):
            b = QPushButton(label); b.setCheckable(True); b.setProperty("role", "swatch"); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(theme_swatch(key)); b.setIconSize(QSize(22, 22)); b.setChecked(c["theme"] == key)
            b.clicked.connect(lambda checked=False, k=key: self.choose_theme(k))
            self.theme_buttons.addButton(b, index); swatches.addWidget(b, index//3, index % 3)
        inside.addLayout(swatches); inside.addStretch()
        self.theme_restart = button("重启以应用新主题", self.restart_app, True, kind="restart")
        self.theme_restart.setVisible(c["theme"] != THEME); inside.addWidget(self.theme_restart, 0, Qt.AlignmentFlag.AlignLeft)
        cards.addWidget(panel, 1)
        panel, inside = card("分析与导出", spacing=10, kind="report")
        self.focus_unknown_check = self.check("把待分类算作未专注", c["focus_includes_unknown"],
                                              "勾选：专注率 = 工作 ÷（工作 + 摸鱼 + 待分类）\n不勾选：专注率 = 工作 ÷（工作 + 摸鱼）\n系统工具和离开时间都不算进专注率。")
        self.hour_range_combo = QComboBox(); self.hour_range_combo.setFixedWidth(200)
        self.hour_range_combo.addItem("从第一条记录到现在", "elapsed"); self.hour_range_combo.addItem("全天 0–24 点", "full")
        self.hour_range_combo.setCurrentIndex(max(0, self.hour_range_combo.findData(c["hour_range"]))); self.hour_range_combo.currentIndexChanged.connect(self.schedule_preferences)
        self.export_format_combo = QComboBox(); self.export_format_combo.setFixedWidth(200)
        for value, label in (("both", "HTML 报告 + CSV 表格"), ("html", "只要 HTML 报告"), ("csv", "只要 CSV 表格")): self.export_format_combo.addItem(label, value)
        self.export_format_combo.setCurrentIndex(max(0, self.export_format_combo.findData(c["export_format"]))); self.export_format_combo.currentIndexChanged.connect(self.schedule_preferences)
        parts = QGridLayout(); parts.setHorizontalSpacing(14); parts.setVerticalSpacing(4); self.export_checks = {}
        for index, (key, label) in enumerate(EXPORT_PARTS.items()):
            box = self.check(label, key in c["export_parts"]); self.export_checks[key] = box; parts.addWidget(box, index//2, index % 2)
        holder = QWidget(); holder.setLayout(parts)
        data_row = QHBoxLayout(); data_row.setSpacing(8)
        data_row.addWidget(button("备份数据…", self.backup_data, kind="export")); data_row.addWidget(button("打开数据文件夹", self.open_data_folder, kind="folder"))
        data_box = QWidget(); data_box.setLayout(data_row); data_row.setContentsMargins(0, 0, 0, 0)
        data_box.setToolTip("备份是一份完整的记录数据库，换电脑或重装后可以交给本程序恢复")
        self.form_rows(inside, [("专注率", self.focus_unknown_check, ""), ("小时图范围", self.hour_range_combo, ""),
                                ("导出格式", self.export_format_combo, ""), ("导出内容", holder, ""), ("数据", data_box, "")])
        inside.addStretch(); cards.addWidget(panel, 1)
        layout.addLayout(cards)
        layout.addWidget(text("所有设置修改后自动保存。", "small"))
        panel, inside = card()
        head = heading("分类规则", "rules"); self.rules_count = text("", "small"); head.addWidget(self.rules_count); head.addStretch()
        head.addWidget(button("添加规则", self.add_rule, True)); head.addWidget(button("编辑所选", self.edit_rule))
        head.addWidget(button("恢复默认", self.remove_rule)); inside.addLayout(head)
        filters = QHBoxLayout(); filters.setSpacing(10)
        self.rule_filter = Segmented([("全部", "")]+[(LABELS[key], key) for key in RULE_TARGETS])
        self.rule_filter.currentIndexChanged.connect(lambda _: self.refresh_rules()); filters.addWidget(self.rule_filter)
        self.rule_search = QLineEdit(); self.rule_search.setPlaceholderText("搜索应用或域名"); self.rule_search.setClearButtonEnabled(True)
        self.rule_search.textChanged.connect(lambda _: self.refresh_rules()); filters.addWidget(self.rule_search, 1)
        self.custom_only = QCheckBox("只看自定义"); self.custom_only.toggled.connect(lambda _: self.refresh_rules()); filters.addWidget(self.custom_only)
        inside.addLayout(filters)
        inside.addWidget(text("网站规则优先于浏览器应用规则，更具体的子域名优先；没装扩展时，对抖音 / 小红书 / B站的网站规则同样生效。", "small", wrap=True))
        self.rules_table = table(["分类", "类型", "应用 / 域名", "每日上限", "来源"], [110, 80, 0, 96, 90])
        self.rules_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rules_table.setToolTip("点分类可直接改；右键有更多操作；双击编辑。")
        self.rules_table.setItemDelegateForColumn(0, CategoryDelegate(self.rules_table, chevron=True))
        self.rules_table.setMouseTracking(True)
        self.rules_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rules_table.customContextMenuRequested.connect(lambda pos: self.rule_menu(self.rules_table.rowAt(pos.y()), self.rules_table.viewport().mapToGlobal(pos)))
        self.rules_table.cellClicked.connect(lambda row, col: col == 0 and self.rule_menu(row))
        self.rules_table.cellDoubleClicked.connect(lambda row, col: col != 0 and self.edit_rule()); self.rules_table.setMinimumHeight(330)
        inside.addWidget(self.rules_table, 1)
        layout.addWidget(panel, 1); self.pages.addWidget(view)

    def build_browser(self):
        view, layout = page()
        panel, inside = card()
        status = QHBoxLayout(); status.setSpacing(12)
        self.connection_dot = QFrame(); self.connection_dot.setFixedSize(12, 12); status.addWidget(self.connection_dot)
        self.connection_title = text("等待连接", "heading"); status.addWidget(self.connection_title); status.addStretch(); inside.addLayout(status)
        self.connection_detail = text("", "muted", wrap=True); inside.addWidget(self.connection_detail)
        row = QHBoxLayout(); row.addWidget(button("复制本机连接码", self.copy_token, True)); row.addWidget(button("打开扩展文件夹", self.open_extension)); row.addStretch(); inside.addLayout(row)
        layout.addWidget(panel)
        panel, inside = card("连接 Chrome / Edge", kind="browser")
        steps = [("打开扩展管理页", "Chrome：chrome://extensions　　Edge：edge://extensions"),
                 ("加载配套扩展", "打开“开发者模式” → “加载已解压的扩展程序” → 选择下面的文件夹。"),
                 ("粘贴连接码", "在自动弹出的扩展设置页粘贴连接码，保存后回到网页即可。")]
        for index, (title, detail) in enumerate(steps, 1):
            row = QHBoxLayout(); row.setSpacing(12)
            number = text(str(index)); number.setFixedSize(26, 26); number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setStyleSheet(f"background:{P["soft"]};color:{ACCENT};border-radius:13px;font-weight:600;font-size:13px;")
            row.addWidget(number, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout(); col.setSpacing(2); col.addWidget(text(title, "heading")); col.addWidget(text(detail, "muted", wrap=True)); row.addLayout(col, 1)
            inside.addLayout(row)
        path = QHBoxLayout()
        self.extension_path = QLineEdit(str(self.project/"browser_extension")); self.extension_path.setReadOnly(True); path.addWidget(self.extension_path, 1)
        path.addWidget(button("复制路径", self.copy_extension_path)); inside.addLayout(path)
        layout.addWidget(panel)
        panel, inside = card("隐私", kind="shield")
        inside.addWidget(text("所有统计只留在这台电脑。未连接扩展时，仍按应用与常见窗口标题估计活动。扩展只向本机报告当前活动标签页的域名和浏览器已缓存的网站图标；标题只在内存中核对，"
                              "数据库不保存完整网址、标题、截图或键盘内容。Firefox 暂不支持。", "muted", wrap=True))
        layout.addWidget(panel); layout.addStretch(); self.pages.addWidget(view)
