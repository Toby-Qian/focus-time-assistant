"""Render every page to PNG from an isolated data folder with sample activity.

Usage: python tests/snapshot_pages.py <output-folder> [width height]
Only the isolated folder is written; the real data/ directory is never touched.
"""
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/"app"))
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
import theme
# --theme <name> renders in another palette (must be chosen before the interface modules load).
if "--theme" in sys.argv: theme.use(sys.argv[sys.argv.index("--theme")+1]); del sys.argv[sys.argv.index("--theme"):sys.argv.index("--theme")+2]
from theme import setup
from desktop import App
from core import Activity

out = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT/"90_过程文件"/"snapshots"
size = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else None
data = out/"sample_data"
shutil.rmtree(data, ignore_errors=True)
out.mkdir(parents=True, exist_ok=True)

QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
q = QApplication([]); setup(q, data/"theme_cache")
w = App(data, PROJECT, testing=True)
import windows  # show real program icons where the sample programs are installed on this machine
for exe, path in {"chrome.exe": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "winword.exe": "C:/Program Files/Microsoft Office/root/Office16/WINWORD.EXE", "explorer.exe": "C:/Windows/explorer.exe"}.items():
    if Path(path).exists(): windows.paths[exe] = path
start = datetime.now().replace(minute=0, second=0, microsecond=0)-timedelta(hours=5)
sample = [("work", "code.exe", "", 2900), ("work", "chrome.exe", "arxiv.org", 1500), ("distraction", "chrome.exe", "www.bilibili.com", 820),
          ("unknown", "wechat.exe", "", 640), ("idle", "", "", 900), ("work", "winword.exe", "", 2100), ("distraction", "chrome.exe", "www.douyin.com", 300),
          ("system", "explorer.exe", "", 600), ("work", "claude.exe", "", 1800)]
cursor = start
for category, app, host, seconds in sample:
    w.store.add(cursor, cursor+timedelta(seconds=seconds), Activity(category, app, host)); cursor += timedelta(seconds=seconds)
for back in range(1, 60):
    day = start-timedelta(days=back)
    if back % 7 in (3, 4) and back > 7: continue  # a few empty days in the calendar
    w.store.add(day, day+timedelta(seconds=3600+(back*1700) % 18000), Activity("work", "code.exe"))
    w.store.add(day+timedelta(hours=2), day+timedelta(hours=2, seconds=900+back*200 % 2400), Activity("distraction", "chrome.exe", "www.bilibili.com"))
for back, task, done in ((0, "论文第三节", True), (0, "", False), (1, "复习高数", True), (2, "论文第三节", True)):
    begun = start-timedelta(days=back)+timedelta(minutes=30)
    w.store.add_session(begun, 25, 1500 if done else 640, 1380 if done else 600, 60, done, task)
w.config["work_goal_minutes"] = 120; w.config["sub_limits"] = {"video": 10}
w.current = Activity("distraction", "chrome.exe", "www.bilibili.com", "B站", "域名识别")
if size: w.resize(*size)
# Render without putting a window on the user's screen (and without catching their clicks).
w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
w.show()


def settle(seconds=0.45):
    """Let fade / grow animations finish before grabbing."""
    import time
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        q.processEvents(); time.sleep(0.01)


def shoot():
    names = ["overview", "analysis", "manager", "settings", "browser"]
    for index, name in enumerate(names):
        w.navigate(index); settle()
        if index == 2:
            w.table.selectRow(0); q.processEvents()
        w.grab().save(str(out/f"{index+1}_{name}.png"))
        if index == 0:
            w.rhythm_mode.setCurrentIndex(1); settle(); w.grab().save(str(out/"1b_timeline.png"))
            w.rhythm_mode.setCurrentIndex(0); q.processEvents()
            w.toggle_away(); settle(); w.grab().save(str(out/"1c_without_away.png"))
            w.toggle_away(); q.processEvents()
            assert w.pages.currentWidget().verticalScrollBar().maximum() == 0, "总览需要滚动"
        if index == 2:
            w.notify("已设为工作：修正 1 条记录，以后也这样记录", lambda: None); q.processEvents()
            w.grab().save(str(out/"3b_toast.png")); w.toast.hide()
            w.manager_view.setCurrentIndex(1); settle(); w.table.selectRow(4); q.processEvents(); w.grab().save(str(out/"3c_by_time.png"))
            assert w.table.horizontalHeaderItem(1).text() == "时间" and not w.change_future.isVisibleTo(w)
            w.manager_view.setCurrentIndex(0); q.processEvents()
        if index == 3:
            w.switch_settings(1); settle(); w.grab().save(str(out/"4b_rules.png"))
            w.switch_settings(2); settle(); w.grab().save(str(out/"4c_subs.png")); w.switch_settings(0); q.processEvents()
    from desktop import Reminder, FocusDialog, ManualDialog, RangeDialog, WelcomeDialog
    today = datetime.now().date().isoformat()
    for name, dialog in (("9_manual_dialog", ManualDialog(w, today, 14*60, 15*60+30, 23*60)), ("9b_range_dialog", RangeDialog(w, "2026-10-01", "2026-10-20", None)),
                         ("9c_welcome", WelcomeDialog(w, 360, False))):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen); dialog.show(); q.processEvents(); dialog.grab().save(str(out/f"{name}.png")); dialog.close()
    w.start_focus(25, "论文第三节"); w.focus["mono"] -= 9*60+12; w.update_float(); q.processEvents(); w.floater.grab().save(str(out/"7_float_focus.png"))
    w.current = Activity("distraction", "chrome.exe", "www.bilibili.com", "B站"); w.update_float(); q.processEvents(); w.floater.grab().save(str(out/"7b_float_distracted.png"))
    w.focus["mono"] -= w.focus["planned"]; w.finish_focus(completed=True); w.rest_break["mono"] -= 70; w.update_float(); q.processEvents()
    w.floater.grab().save(str(out/"7c_float_break.png")); w.end_break()
    dialog = FocusDialog(w, 25, w.config["recent_tasks"], 5); dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen); dialog.show(); q.processEvents()
    dialog.grab().save(str(out/"8_focus_dialog.png")); dialog.close()
    from PySide6.QtWidgets import QScrollArea
    w.navigate(1); settle(); area = w.pages.currentWidget()
    if isinstance(area, QScrollArea): area.verticalScrollBar().setValue(area.verticalScrollBar().maximum()); settle(.2)
    w.grab().save(str(out/"2b_analysis_lower.png"))
    w.range_combo.setCurrentIndex(3); settle(); bar = area.verticalScrollBar(); bar.setValue(w.sub_analysis.parentWidget().y()-200); settle(.2)
    w.grab().save(str(out/"2h_analysis_subs.png")); w.sub_kind.setCurrentIndex(1); settle(.2); w.grab().save(str(out/"2i_analysis_subs_play.png")); w.sub_kind.setCurrentIndex(0)
    bar = area.verticalScrollBar(); bar.setValue(bar.maximum()//2); settle(.2); w.grab().save(str(out/"2c_analysis_middle.png"))
    w.choose_custom("lastweek"); settle(); bar.setValue(0); settle(.2); w.grab().save(str(out/"2f_last_week.png"))
    assert w.more_range.isChecked() and w.more_range.text() == "上周" and w.analysis_span()[0] == 7
    w.choose_custom("custom", (start-timedelta(days=50)).date().isoformat(), start.date().isoformat()); settle()
    bar.setValue(bar.maximum()//3); settle(.2); w.grab().save(str(out/"2g_custom_51_days.png"))
    w.show_analysis_day(datetime.now().date().isoformat()); settle()  # today: the hour mark path
    w.analysis_view.verticalScrollBar().setValue(0); settle(.2); w.heatmap.parentWidget().grab().save(str(out/"2j_calendar_today_picked.png"))
    assert w.analysis_custom is None and not w.more_range.isChecked()
    assert w.analysis_dates()[0] == datetime.now().date().isoformat() and "前一天" in w.compare_title.text()
    w.show_analysis_day((start-timedelta(days=5)).date().isoformat()); settle(); w.grab().save(str(out/"2d_analysis_day.png"))
    bar.setValue(bar.maximum()//2); settle(.2); w.grab().save(str(out/"2e_analysis_day_middle.png"))
    from desktop import Reminder
    popup = Reminder(w, w.current); popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen); popup.show(); q.processEvents(); popup.grab().save(str(out/"6_reminder.png")); popup.close()
    from desktop import ReturnPrompt
    back = datetime.now().replace(second=0, microsecond=0)
    for name, before in (("6b_return_watching", Activity("distraction", "chrome.exe", "www.bilibili.com")), ("6c_return_working", Activity("work", "code.exe"))):
        card = ReturnPrompt(w, back-timedelta(minutes=42), back, before); card.show(); q.processEvents(); card.grab().save(str(out/f"{name}.png")); card.close()
    print("saved", out, "dpr", w.devicePixelRatioF())
    w.shutdown(); q.quit()


def run():
    try: shoot()
    except BaseException:
        import traceback
        traceback.print_exc(); w.shutdown(); q.exit(1)


QTimer.singleShot(700, run)
sys.exit(q.exec())
