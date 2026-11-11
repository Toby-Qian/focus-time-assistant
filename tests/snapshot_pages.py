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
w.config["work_goal_minutes"] = 120
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
        if index == 2:
            w.notify("已设为工作：修正 1 条记录，以后也这样记录", lambda: None); q.processEvents()
            w.grab().save(str(out/"3b_toast.png")); w.toast.hide()
    from desktop import Reminder, FocusDialog
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
    bar = area.verticalScrollBar(); bar.setValue(bar.maximum()//2); settle(.2); w.grab().save(str(out/"2c_analysis_middle.png"))
    w.show_analysis_day(datetime.now().date().isoformat()); settle()  # today: the hour mark path
    assert w.analysis_dates()[0] == datetime.now().date().isoformat() and "前一天" in w.compare_title.text()
    w.show_analysis_day((start-timedelta(days=5)).date().isoformat()); settle(); w.grab().save(str(out/"2d_analysis_day.png"))
    bar.setValue(bar.maximum()//2); settle(.2); w.grab().save(str(out/"2e_analysis_day_middle.png"))
    from desktop import Reminder
    popup = Reminder(w, w.current); popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen); popup.show(); q.processEvents(); popup.grab().save(str(out/"6_reminder.png")); popup.close()
    print("saved", out, "dpr", w.devicePixelRatioF())
    w.shutdown(); q.quit()


QTimer.singleShot(700, shoot)
q.exec()
