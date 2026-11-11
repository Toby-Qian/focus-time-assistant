"""Interactive vector charts painted with QPainter.

Each widget repaints only when its data actually changes, and hourly charts never
infer data from legacy daily aggregates.
"""
import math

from PySide6.QtCore import Qt, QEasingCurve, QRectF, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QToolTip
from core import CATEGORIES
from theme import ACCENT, COLORS, INK, LINE, MUTED, P, SUBTLE, font

STEPS = [60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800, 14400, 21600]


def short_time(seconds):
    minutes = int(seconds)//60
    if minutes >= 60:
        return f"{minutes//60}小时" if minutes % 60 == 0 else f"{minutes//60}小时{minutes%60:02d}分"
    return f"{minutes}分钟" if minutes else f"{int(seconds)}秒" if int(seconds) else "0 分钟"


def share(part, total):
    if not total:
        return "—"
    ratio = part/total
    if 0 < ratio < .01:
        return "<1%"
    return f"{ratio:.0%}"


def focus_ratio(totals, include_unknown=True):
    """Work ÷ (work + distraction [+ unknown]); idle and ignored time are left out."""
    active = totals.get("work", 0)+totals.get("distraction", 0)+(totals.get("unknown", 0) if include_unknown else 0)
    return totals.get("work", 0)/active if active else None


def tick_label(value):
    value = int(round(value))
    if value >= 3600:
        hours, rest = divmod(value, 3600)
        return f"{hours}h" if not rest else f"{hours}h{rest//60}m"
    if value >= 60:
        return f"{value//60}m"
    return f"{value}s"


def nice_scale(peak, lines=4):
    """Round axis maximum with whole-unit steps: 20m / 40m / 1h, not 0.33h."""
    step = next((s for s in STEPS if s*lines >= peak), STEPS[-1])
    count = max(1, min(lines, -(-int(peak)//step))) if peak else 3
    return step, step*count


class Grow:
    """Short ease-out "grow in" animation; runs only when a chart gets a new shape of data."""
    def init_grow(self):
        self.grow = 1.0
        self.anim = QVariantAnimation(self); self.anim.setDuration(420); self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setStartValue(0.0); self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._grow_step)
        self.anim.finished.connect(lambda: self._grow_step(1.0))  # never leave a chart part-grown

    def _grow_step(self, value):
        self.grow = float(value); self.update()

    def start_grow(self):
        import windows
        if not windows.animations_enabled():  # reduced motion: show the data at once
            self.anim.stop(); self._grow_step(1.0); return
        if self.isVisible():
            self.anim.stop(); self.grow = 0.0; self.anim.start()


class Donut(Grow, QWidget):
    """Today's share per category as a ring; hover a segment for details, click to inspect it."""
    chosen = Signal(str)
    THICK = 16

    def __init__(self, size=148):
        super().__init__()
        self.totals = dict.fromkeys(CATEGORIES, 0)
        self.spans = []
        self.hover = None
        self.setFixedSize(size, size)
        self.setMouseTracking(True)
        self.init_grow()

    def set_data(self, totals):
        rounded = {k: int(v) for k, v in totals.items()}
        if rounded != self.totals:
            first = not sum(self.totals.values()) and sum(rounded.values())
            self.totals = rounded; self.update()
            if first: self.start_grow()

    def ring(self):
        inset = self.THICK/2+3
        return QRectF(inset, inset, self.width()-2*inset, self.height()-2*inset)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        rect = self.ring()
        p.setPen(QPen(QColor(P["track"]), self.THICK)); p.drawEllipse(rect)
        total = sum(self.totals.values()); self.spans = []
        start = 90.0  # degrees, counter-clockwise from 3 o'clock; segments run clockwise from 12
        gap = 1.6 if sum(1 for v in self.totals.values() if v) > 1 else 0
        for key, seconds in self.totals.items():
            if not seconds or not total:
                continue
            sweep = 360*seconds/total
            self.spans.append(((90-start) % 360, sweep, key))
            sweep_drawn = sweep*self.grow
            width = self.THICK+(4 if key == self.hover else 0)
            p.setPen(QPen(QColor(COLORS[key]), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            drawn = max(.5, sweep_drawn-gap)
            p.drawArc(rect, round((start-gap/2)*16), -round(drawn*16))
            start -= sweep_drawn
        p.setPen(QColor(INK)); p.setFont(font(20, QFont.Weight.DemiBold, "num"))
        hours, minutes = divmod(int(total)//60, 60)
        centre = self.rect().adjusted(0, -10, 0, -10)
        p.drawText(centre, Qt.AlignmentFlag.AlignCenter, (f"{hours}h {minutes:02d}m" if hours else f"{minutes}m") if total else "—")
        p.setPen(QColor(SUBTLE)); p.setFont(font(12))
        p.drawText(self.rect().adjusted(0, 26, 0, 26), Qt.AlignmentFlag.AlignCenter, "今日已记录" if total else "等待记录")

    def key_at(self, point):
        dx, dy = point.x()-self.width()/2, point.y()-self.height()/2
        radius = math.hypot(dx, dy); ring = self.ring().width()/2
        if abs(radius-ring) > self.THICK/2+4:
            return None
        angle = (math.degrees(math.atan2(dy, dx))+90) % 360  # 0 at 12 o'clock, clockwise
        return next((k for begin, sweep, k in self.spans if begin <= angle < begin+sweep), None)

    def mouseMoveEvent(self, event):
        # React only when the hovered segment changes: re-showing a tooltip on every
        # (possibly synthetic) mouse move keeps Windows and Qt busy in a loop.
        key = self.key_at(event.position())
        if key == self.hover:
            return
        self.hover = key; self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
        if key:
            total = sum(self.totals.values())
            QToolTip.showText(event.globalPosition().toPoint(), f"{CATEGORIES[key]} · {short_time(self.totals[key])} · {share(self.totals[key], total)}\n点击查看明细", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        if self.hover is not None:
            self.hover = None; self.update()

    def mousePressEvent(self, event):
        key = self.key_at(event.position())
        if key:
            self.chosen.emit(key)


class StackedBars(Grow, QWidget):
    chosen = Signal(str)

    def __init__(self, hourly=False):
        super().__init__()
        self.hourly = hourly
        self.rows = []
        self.hits = []
        self.hover = None
        self.mark = None  # highlighted column: the current hour, or today
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.init_grow()

    def set_data(self, rows):
        compact = [(label, {k: int(v) for k, v in totals.items()}) for label, totals in rows]
        if compact != self.rows:
            # Grow in when the set of columns changes (first data, 7 / 30 days, a new hour), not on every update.
            reshape = [label for label, _ in compact] != [label for label, _ in self.rows]
            self.rows = compact; self.update()
            if reshape: self.start_grow()

    def set_mark(self, index):
        if index != self.mark:
            self.mark = index; self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        width, height = self.width(), self.height()
        left, top, right, bottom = 44, 10, width-6, height-24
        graph = max(1, bottom-top)
        peak = max((sum(t.values()) for _, t in self.rows), default=0)
        step, maximum = nice_scale(peak)
        p.setFont(font(11))
        lines = round(maximum/step)
        for i in range(lines+1):
            y = round(bottom-graph*i/lines)+.5
            p.setPen(QPen(QColor(P["axis"] if i == 0 else P["grid"]), 1)); p.drawLine(left, y, right, y)
            p.setPen(QColor(SUBTLE))
            p.drawText(QRectF(0, y-8, left-8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tick_label(step*i))
        self.hits = []
        if not self.rows:
            p.setPen(QColor(MUTED)); p.setFont(font(13))
            p.drawText(QRectF(left, top, right-left, graph), Qt.AlignmentFlag.AlignCenter, "开始记录后，这里会出现时间的节奏")
            return
        slot = (right-left)/len(self.rows)
        bar = max(3.0, min(30.0, slot*.62))
        count = len(self.rows)
        every = (1 if count <= 12 else 2 if count <= 18 else 3) if self.hourly else max(1, round(count/8))
        for index, (label, totals) in enumerate(self.rows):
            x = left+slot*index+(slot-bar)/2
            area = QRectF(left+slot*index, top, slot, graph)
            if index == self.mark:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(P["mark"])); p.drawRoundedRect(area.adjusted(1, 0, -1, 0), 4, 4)
            if index == self.hover and sum(totals.values()):
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(P["hover_col"])); p.drawRoundedRect(area.adjusted(1, 0, -1, 0), 4, 4)
            y = float(bottom)
            p.setPen(Qt.PenStyle.NoPen)
            heights = [(key, graph*seconds/maximum*self.grow if maximum else 0) for key, seconds in totals.items()]
            heights = [(key, h) for key, h in heights if h >= .3]
            total = sum(h for _, h in heights)
            if total:
                radius = min(4.0, bar/2, total)
                shape = QPainterPath(); shape.addRoundedRect(QRectF(x, bottom-total, bar, total+radius), radius, radius)
                p.save(); p.setClipPath(shape); p.setClipRect(QRectF(x, top-radius, bar, bottom-top+radius), Qt.ClipOperation.IntersectClip)
                gap = QColor(P["card"])
                for n, (key, h) in enumerate(heights):
                    p.setBrush(QColor(COLORS[key])); p.drawRect(QRectF(x, y-h, bar, h))
                    if n and h > 2: p.fillRect(QRectF(x, y-.6, bar, 1.2), gap)  # separate stacked categories
                    y -= h
                p.restore()
            self.hits.append((area, label, totals, index))
            if index % every == 0 or index == self.mark or (not self.hourly and index == len(self.rows)-1):
                text = f"{int(label):02d}" if self.hourly else label[5:].replace("-", "/")
                if index == self.mark:
                    text = "现在" if self.hourly else "今天"
                    p.setPen(QColor(ACCENT)); p.setFont(font(11, QFont.Weight.DemiBold))
                else:
                    if every > 1 and self.mark is not None and abs(index-self.mark) == 1 and self.hourly:
                        continue  # keep the "现在" label from colliding with its neighbours
                    p.setPen(QColor(SUBTLE)); p.setFont(font(11))
                p.drawText(QRectF(left+slot*index-14, bottom+6, slot+28, 16), Qt.AlignmentFlag.AlignCenter, text)

    def at(self, point):
        return next((row for row in self.hits if row[0].contains(point)), None)

    def mouseMoveEvent(self, event):
        hit = self.at(event.position())
        index = hit[3] if hit else None
        if index == self.hover:
            return
        self.hover = index; self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit and not self.hourly else Qt.CursorShape.ArrowCursor)
        if hit:
            _, label, totals, _ = hit
            heading = f"{label}:00 – {int(label)+1:02d}:00" if self.hourly else label
            detail = "\n".join(f"{CATEGORIES[k]}  {short_time(v)}" for k, v in totals.items() if v)
            QToolTip.showText(event.globalPosition().toPoint(), heading+"\n"+(detail or "未记录活动")+("" if self.hourly or not detail else "\n点击查看当天明细"), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        if self.hover is not None:
            self.hover = None; self.update()

    def mousePressEvent(self, event):
        hit = self.at(event.position())
        if hit and not self.hourly:
            self.chosen.emit(hit[1])


class RankList(QWidget):
    """Top activities painted as one widget: name, time and a category-coloured bar.

    Painting a single widget instead of a button, label and progress bar per row
    keeps per-second updates cheap.
    """
    chosen = Signal(str)
    ROW = 44

    def __init__(self, rows=6):
        super().__init__()
        self.limit = rows
        self.entries = []
        self.tip = None
        self.setMouseTracking(True)
        self.setMinimumHeight(self.ROW*3)

    def set_data(self, entries):
        """entries: [(label, query, category, seconds)] sorted by seconds."""
        compact = [(n, q, c, int(s)) for n, q, c, s in entries[:self.limit]]
        if compact != self.entries:
            self.entries = compact; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        width = self.width()
        if not self.entries:
            p.setPen(QColor(MUTED)); p.setFont(font(13))
            p.drawText(QRectF(0, 0, width, self.ROW*2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "这里会显示今天投入时间最多的应用与网站。")
            return
        top = max(1, self.entries[0][3])
        name_font, value_font = font(13), font(12, family="num")
        from appicons import icon_for
        for i, (name, query, category, seconds) in enumerate(self.visible()):
            y = i*self.ROW
            value = short_time(seconds)
            p.setFont(value_font); p.setPen(QColor(MUTED))
            value_width = p.fontMetrics().horizontalAdvance(value)+4
            p.drawText(QRectF(width-value_width, y+4, value_width, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)
            p.setFont(name_font); p.setPen(QColor(INK))
            p.drawText(QRectF(28, y+4, width-value_width-36, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, int(width-value_width-36)))
            icon_for(query, category).paint(p, 0, int(y+4), 20, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P["track"])); p.drawRoundedRect(QRectF(28, y+30, width-28, 5), 2.5, 2.5)
            p.setBrush(QColor(COLORS[category])); p.drawRoundedRect(QRectF(28, y+30, max(5, (width-28)*seconds/top), 5), 2.5, 2.5)

    def visible(self):
        """Only whole rows that fit the current height."""
        return self.entries[:max(1, self.height()//self.ROW)]

    def row_at(self, point):
        index = int(point.y()//self.ROW); rows = self.visible()
        return rows[index] if 0 <= index < len(rows) else None

    def mouseMoveEvent(self, event):
        row = self.row_at(event.position())
        key = row[1] if row else None
        if key == self.tip:
            return
        self.tip = key
        self.setCursor(Qt.CursorShape.PointingHandCursor if row else Qt.CursorShape.ArrowCursor)
        if row:
            QToolTip.showText(event.globalPosition().toPoint(), f"{row[0]} · {CATEGORIES[row[2]]} · {short_time(row[3])}\n点击查看明细", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        self.tip = None

    def mousePressEvent(self, event):
        row = self.row_at(event.position())
        if row:
            self.chosen.emit(row[1])


class Timeline(QWidget):
    """Today minute by minute: each recorded minute coloured by what filled most of it."""
    chosen = Signal(str)

    def __init__(self):
        super().__init__()
        self.minutes = []  # (minute 0-1439, category, label, query)
        self.span = (0, 24)
        self.hover = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

    def set_data(self, minutes, span):
        if minutes != self.minutes or span != self.span:
            self.minutes, self.span = minutes, span; self.update()

    def frame_span(self):
        left, right = 26, self.width()-26
        start, end = self.span
        return left, right, start*60, max(1, (end-start)*60)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        left, right, origin, length = self.frame_span()
        height = min(self.height()-60, 110.0)
        top = (self.height()-24-height)/2; bottom = top+height
        lane = QRectF(left, top, right-left, bottom-top)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(P["lane"])); p.drawRoundedRect(lane, 6, 6)
        if not self.minutes:
            p.setPen(QColor(MUTED)); p.setFont(font(13))
            p.drawText(lane, Qt.AlignmentFlag.AlignCenter, "开始记录后，这里会按分钟画出今天的时间线")
            return
        scale = (right-left)/length
        p.setClipRect(lane)
        for minute, category, _, _ in self.minutes:
            x = left+(minute-origin)*scale
            p.setBrush(QColor(COLORS[category])); p.drawRect(QRectF(x, top, max(1.0, scale+.4), bottom-top))
        p.setClipping(False)
        if self.hover is not None:
            x = left+(self.hover-origin)*scale
            p.setPen(QPen(QColor(INK), 1.2)); p.drawLine(round(x)+.5, top-6, round(x)+.5, bottom+4)
        p.setFont(font(11)); hours = self.span[1]-self.span[0]
        every = 1 if hours <= 8 else 2 if hours <= 14 else 3
        for hour in range(self.span[0], self.span[1]+1):
            if (hour-self.span[0]) % every: continue
            x = left+(hour*60-origin)*scale
            p.setPen(QPen(QColor(P["tick"]), 1)); p.drawLine(round(x)+.5, bottom, round(x)+.5, bottom+4)
            p.setPen(QColor(SUBTLE)); p.drawText(QRectF(x-20, bottom+6, 40, 16), Qt.AlignmentFlag.AlignCenter, f"{hour:02d}:00")

    def minute_at(self, x):
        left, right, origin, length = self.frame_span()
        if not left <= x <= right: return None
        return int(origin+(x-left)*length/(right-left))

    def entry(self, minute):
        return next((m for m in self.minutes if m[0] == minute), None) if minute is not None else None

    def mouseMoveEvent(self, event):
        minute = self.minute_at(event.position().x())
        if minute == self.hover: return
        self.hover = minute; self.update()
        found = self.entry(minute)
        self.setCursor(Qt.CursorShape.PointingHandCursor if found else Qt.CursorShape.ArrowCursor)
        if minute is None:
            QToolTip.hideText(); return
        clock = f"{minute//60:02d}:{minute%60:02d}"
        QToolTip.showText(event.globalPosition().toPoint(), f"{clock} · {found[2]} · {CATEGORIES[found[1]]}" if found else f"{clock} · 无记录", self)

    def leaveEvent(self, _):
        if self.hover is not None:
            self.hover = None; self.update()

    def mousePressEvent(self, event):
        found = self.entry(self.minute_at(event.position().x()))
        if found and found[3]: self.chosen.emit(found[3])


class Heatmap(QWidget):
    """Calendar of recent weeks (Monday at the top), one cell per day shaded by work time.

    Levels are relative to the daily goal (or 4 h without one); a ring marks today and a
    small dot marks days that reached the goal. As many weeks are shown as fit the width.
    """
    chosen = Signal(str)
    CELL, GAP, LEFT, TOP = 15, 4, 26, 20
    LEVELS = (0.0, .3, .52, .76, 1.0)  # fill opacity of the work colour per level

    def __init__(self):
        super().__init__(); self.work = {}; self.goal = 0; self.today = None; self.hover = None
        self.setMouseTracking(True); self.setMinimumHeight(self.TOP+7*(self.CELL+self.GAP)+30)

    def set_data(self, work_by_day, goal_seconds, today):
        data = ({d: int(s) for d, s in work_by_day.items()}, int(goal_seconds), today)
        if data != (self.work, self.goal, self.today):
            self.work, self.goal, self.today = data; self.update()

    def weeks(self):
        return max(6, min(26, (self.width()-self.LEFT)//(self.CELL+self.GAP)))

    def cells(self):
        """[(QRectF, iso day, date)] for every visible day up to today."""
        if not self.today: return []
        from datetime import date, timedelta
        today = date.fromisoformat(self.today); step = self.CELL+self.GAP
        start = today-timedelta(days=today.weekday())-timedelta(weeks=self.weeks()-1)
        out = []
        for index in range((today-start).days+1):
            day = start+timedelta(days=index)
            out.append((QRectF(self.LEFT+index//7*step, self.TOP+index % 7*step, self.CELL, self.CELL), day.isoformat(), day))
        return out

    def level(self, seconds):
        if seconds < 60: return 0
        ratio = seconds/(self.goal or 4*3600)
        return 1 if ratio < .25 else 2 if ratio < .5 else 3 if ratio < 1 else 4

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        cells = self.cells()
        if not cells: return
        p.setFont(font(11)); p.setPen(QColor(SUBTLE))
        for row, name in ((0, "一"), (2, "三"), (4, "五"), (6, "日")):
            p.drawText(QRectF(0, self.TOP+row*(self.CELL+self.GAP), self.LEFT-6, self.CELL), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
        shown_month = None
        for rect, iso, day in cells:
            if day.weekday() == 0 and day.month != shown_month and day.day <= 7:
                shown_month = day.month; p.setPen(QColor(SUBTLE))
                p.drawText(QRectF(rect.x(), 0, 40, self.TOP-4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{day.month}月")
        work = QColor(COLORS["work"]); goal = self.goal or 4*3600
        for rect, iso, day in cells:
            seconds = self.work.get(iso, 0); level = self.level(seconds)
            p.setPen(Qt.PenStyle.NoPen)
            if level:
                fill = QColor(work); fill.setAlphaF(self.LEVELS[level]); p.setBrush(fill)
            else:
                p.setBrush(QColor(P["track"]))
            p.drawRoundedRect(rect, 3.5, 3.5)
            if self.goal and seconds >= goal:
                p.setBrush(QColor(P["card"])); p.drawEllipse(rect.center(), 2, 2)
            if iso == self.today or iso == self.hover:
                p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor(ACCENT if iso == self.today else INK), 1.6))
                p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 4.5, 4.5)
        # Legend: 少 ▢▢▢▢▢ 多
        y = self.TOP+7*(self.CELL+self.GAP)+8; x = self.LEFT
        p.setPen(QColor(SUBTLE)); p.drawText(QRectF(x, y, 20, 12), Qt.AlignmentFlag.AlignVCenter, "少"); x += 18
        for level in range(5):
            p.setPen(Qt.PenStyle.NoPen)
            if level:
                fill = QColor(work); fill.setAlphaF(self.LEVELS[level]); p.setBrush(fill)
            else: p.setBrush(QColor(P["track"]))
            p.drawRoundedRect(QRectF(x, y, 12, 12), 3, 3); x += 15
        p.setPen(QColor(SUBTLE)); p.drawText(QRectF(x+3, y, 160, 12), Qt.AlignmentFlag.AlignVCenter,
                                             "多" + ("　·　圆点 = 达到每日目标" if self.goal else "　·　以 4 小时为满格"))

    def cell_at(self, point):
        return next(((iso, day) for rect, iso, day in self.cells() if rect.adjusted(-2, -2, 2, 2).contains(point)), None)

    def mouseMoveEvent(self, event):
        found = self.cell_at(event.position()); iso = found[0] if found else None
        if iso == self.hover: return
        self.hover = iso; self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if found else Qt.CursorShape.ArrowCursor)
        if found:
            seconds = self.work.get(iso, 0); day = found[1]
            reached = "　已达成目标" if self.goal and seconds >= self.goal else ""
            QToolTip.showText(event.globalPosition().toPoint(), f"{day.month}月{day.day}日 周{'一二三四五六日'[day.weekday()]}\n工作 {short_time(seconds)}{reached}\n点击查看当天明细", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = None; self.update()

    def mousePressEvent(self, event):
        found = self.cell_at(event.position())
        if found: self.chosen.emit(found[0])
