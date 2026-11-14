"""Run as a fresh Windows process for meaningful high-DPI and focus integration checks."""
import ctypes
import json
import os
from datetime import datetime,timedelta
from pathlib import Path
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app"))
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from theme import setup
from desktop import App
from ui_qt import CategoryDelegate
from core import Activity
import windows

QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
q=QApplication([]); setup(q)
folder=PROJECT/"90_过程文件"/"2026-11-10_性能与界面优化"
folder.mkdir(parents=True,exist_ok=True)
w=App(folder/f"run_{datetime.now():%H%M%S_%f}",PROJECT,testing=True)
w.show(); errors=[]; evidence={}


def check():
    try:
        q.processEvents()
        user=ctypes.WinDLL("user32")
        user.GetThreadDpiAwarenessContext.restype=ctypes.c_void_p
        user.GetAwarenessFromDpiAwarenessContext.argtypes=[ctypes.c_void_p]
        awareness=user.GetAwarenessFromDpiAwarenessContext(user.GetThreadDpiAwarenessContext())
        assert awareness==2,"Qt 未启用每显示器 DPI 适配"
        for widget in w.card_values.values():
            assert widget.width()>=widget.fontMetrics().horizontalAdvance(widget.text()),"数字裁切"
        for i in range(5): w.navigate(i); q.processEvents(); assert w.pages.currentIndex()==i
        # Isolated fixtures only; no invented history is ever written to the real database.
        now=datetime.now(); day=now.date().isoformat()
        w.store.add(now,now+timedelta(seconds=35),Activity("unknown","qt-check.exe"))
        w.store.add(now,now+timedelta(seconds=25),Activity("unknown","second-check.exe"))
        w.open_manager(category="unknown",query="qt-check.exe")
        assert w.table.rowCount()==1
        w.table.selectRow(0); keys=w.selected_keys(); w.manager_signature=None; w.refresh_manager()
        assert w.selected_keys()==keys,"实时刷新丢失选择"
        before=sum(w.store.totals(day).values()); old=w.store.totals(day)["unknown"]
        w.apply_classification("work")
        assert w.store.totals(day)["unknown"]==old-35
        assert sum(w.store.totals(day).values())==before
        assert w.config["app_rules"]["qt-check.exe"]=="work"
        w.undo_correction(); assert w.store.totals(day)["unknown"]==old
        # "不计入" removes the selected time (undoable) and stores an ignore rule.
        w.open_manager(query="second-check.exe"); w.table.selectRow(0); total_before=sum(w.store.totals(day).values())
        w.apply_classification("ignore")
        assert sum(w.store.totals(day).values())==total_before-25,"不计入未移除时间"
        assert w.config["app_rules"]["second-check.exe"]=="ignore"
        w.undo_correction(); assert sum(w.store.totals(day).values())==total_before,"撤销不计入失败"
        # Keyboard focus ring: none on start-up, shown after Tab, cleared by a mouse press.
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtGui import QFocusEvent, QMouseEvent
        from PySide6.QtCore import QEvent, QPointF
        assert not [b.text() for b in w.findChildren(QPushButton) if b.property("kbfocus")],"启动时出现了焦点框"
        target=w.nav_buttons[0]
        q.sendEvent(target,QFocusEvent(QEvent.Type.FocusIn,Qt.FocusReason.ActiveWindowFocusReason)); assert not target.property("kbfocus")
        q.sendEvent(target,QFocusEvent(QEvent.Type.FocusIn,Qt.FocusReason.TabFocusReason)); assert target.property("kbfocus"),"Tab 后没有焦点框"
        target.setFocus(Qt.FocusReason.TabFocusReason); q.processEvents()
        press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(5,5),QPointF(5,5),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        q.sendEvent(w.nav_buttons[1],press); q.sendEvent(target,QFocusEvent(QEvent.Type.FocusOut,Qt.FocusReason.MouseFocusReason))
        assert not target.property("kbfocus"),"鼠标点击后焦点框没消失"
        # Rules page: category filter and in-place category change.
        w.navigate(3); w.refresh_rules()
        w.rule_filter.setCurrentIndex(w.rule_filter.findData("system"))
        assert w.rule_entries and all(cat=="system" for _,(cat,_) in w.rule_entries),"规则分类筛选失败"
        assert ("app","taskmgr.exe") in [k for k,_ in w.rule_entries] or "taskmgr.exe" in w.config["app_rules"]
        w.rule_filter.setCurrentIndex(0)
        w.set_rule_category("app","qt-check.exe","system"); assert w.config["app_rules"]["qt-check.exe"]=="system"
        w.set_rule_category("app","everything.exe","work"); assert w.config["app_rules"]["everything.exe"]=="work"
        w.set_rule_category("app","everything.exe","system"); assert "everything.exe" not in w.config["app_rules"],"改回默认分类应删除自定义规则"
        w.open_manager(query="qt-check.exe"); w.table.selectRow(0); w.apply_classification("system")
        assert w.store.totals(day)["system"]>=35,"设为系统工具失败"
        w.undo_correction()
        # The assistant's own window is never counted.
        real_foreground=windows.foreground
        def own(skip=None): windows.last_pid=os.getpid(); windows.last_background=""; windows.last_skipped=False; return "pythonw.exe","专注时间助手"
        windows.foreground=own; w.accountant.previous=None; w.tick(); windows.foreground=real_foreground
        assert w.current.category=="idle" and w.own_window,"本程序窗口未记为离开"
        # The own window's category is adjustable; "skip" uses the program below it instead.
        w.set_own_window("work"); windows.foreground=own; w.accountant.previous=None; w.tick()
        assert w.current.category=="work" and w.current.app=="专注时间助手.exe","本程序窗口分类不可调整"
        def below(skip=None):
            assert skip==os.getpid(),"略过模式没有传入本进程"
            windows.last_pid=1; windows.last_background=""; windows.last_skipped=True; return "code.exe","main.py - Visual Studio Code"
        w.set_own_window("skip"); windows.foreground=below; w.accountant.previous=None; w.tick()
        assert w.current.app=="code.exe" and w.current.category=="work" and "略过" in w.current.source and not w.own_window,"略过本程序失败"
        windows.foreground=real_foreground; w.set_own_window("idle"); assert w.config["own_window"]=="idle"
        # Desktop / taskbar / hidden windows hold focus without an app being used: not counted.
        def desktop(skip=None): windows.last_pid=1; windows.last_skipped=False; windows.last_background="桌面"; return "explorer.exe","Program Manager"
        windows.foreground=desktop; before_desktop=sum(w.store.totals(day).values()); w.accountant.previous=None
        w.tick(); w.tick(); windows.foreground=real_foreground
        assert w.current.category=="ignore" and w.background=="桌面","桌面未判定为后台"
        assert sum(w.store.totals(day).values())==before_desktop,"后台时间被计入"
        # …but every foreground can be adjusted: a desktop rule makes that time count.
        w.refresh_overview(False); assert w.classify_current.isEnabled(),"桌面上“调整分类”不可用"
        w.set_rule_category("surface","桌面","system"); assert w.config["surface_rules"]=={"桌面":"system"}
        windows.foreground=desktop; w.accountant.previous=None; w.tick()
        wall,mono,act=w.accountant.previous; w.accountant.previous=(wall-timedelta(seconds=2),mono-2,act)  # 2 s on the desktop
        w.tick(); windows.foreground=real_foreground
        assert w.current.category=="system" and w.current.app=="界面:桌面","桌面规则未生效"
        assert w.store.totals(day)["system"]>0,"桌面时间未按规则计入"
        page_before=w.pages.currentIndex(); w.navigate(3); w.refresh_rules(); assert (("surface","桌面"),("system","自定义")) in w.rule_entries,"规则表没有系统界面"
        w.set_rule_category("surface","桌面","ignore"); assert w.config["surface_rules"]=={},"改回默认应删除界面规则"
        w.navigate(page_before)
        # Charts show their data even when Windows animation effects are off.
        windows._animations=False; w.trend_chart.rows=[]; w.trend_chart.set_data([("2026-11-10",{"work":60.0})]); windows._animations=None
        assert w.trend_chart.grow==1.0,"关闭动画时图表高度为 0"
        # Per-second refresh updates the time cells in place instead of rebuilding rows.
        w.search.setText("qt-check.exe"); cell=w.table.item(0,3); before_text=cell.text()
        w.store.add(now+timedelta(seconds=40),now+timedelta(seconds=45),Activity("unknown","qt-check.exe"))
        w.refresh_manager(); assert w.table.item(0,3) is cell and cell.text()!=before_text,"表格未原位更新"
        w.search.setText("no-such-record"); assert w.table.rowCount()==0; assert w.empty_label.isVisible()
        # Focus session: starts, shows in the header, records a session when finished.
        w.config["focus_minutes"]=25; w.start_focus(); assert w.focus and w.focus_button.text().startswith("专注中"),"专注未开始"
        w.focus["mono"]-=120; w.finish_focus(completed=False)
        assert w.focus is None and w.store.sessions(day)[1]>=119,"专注记录未保存"
        assert w.focus_button.text()=="开始专注"
        # Focus with a task name: floating timer, break afterwards, next session from the break.
        w.config["break_minutes"]=5; w.config["focus_float"]=True
        w.start_focus(15,"写周报"); assert w.focus["task"]=="写周报" and w.config["recent_tasks"][0]=="写周报"
        assert w.floater and w.floater.isVisible() and w.floater.state[0]=="focus" and w.floater.state[3]=="写周报","悬浮计时未显示"
        w.focus["mono"]-=w.focus["planned"]; w.finish_focus(completed=True)
        assert w.rest_break and w.floater.state[0]=="break" and w.focus_button.text().startswith("休息中"),"专注后未开始休息"
        assert w.store.session_list(day,day)[-1][6]=="写周报","任务名未记录"
        w.rest_break["mono"]-=w.rest_break["planned"]; w.end_break(done=True)
        assert w.rest_break is None and not w.floater.isVisible() and w.toast.action is not None
        w.toast.run_action(); assert w.focus and w.focus["task"]=="写周报","休息后未能开始下一段"
        w.finish_focus(completed=False); assert w.focus is None and not w.floater.isVisible()
        # Timed pause resumes by itself.
        w.pause_for(15); assert w.paused and w.pause_until
        w.pause_until=time.monotonic()-1; w.tick(); assert not w.paused and w.pause_until is None,"定时暂停未自动继续"
        w.own_window=False; w.skipped=False; w.background=""  # tick() sampled the real foreground window
        # Analysis: calendar, findings and focus sessions.
        w.navigate(1); w.refresh_analysis()
        assert w.heatmap.totals and w.insight_label.text() and w.session_table.rowCount()>=1 and w.session_table.isVisible(), (bool(w.heatmap.totals), bool(w.insight_label.text()), w.session_table.rowCount(), w.session_table.isVisible(), w.pages.currentIndex())
        w.navigate(0)
        # Daily summary, backup, tray colour and program icons.
        w.config["daily_summary"]="00:00"; w.summary_day=None; w.check_summary()
        assert w.summary_day==day and w.toast.label.text().startswith("今日总结"),"每日总结未出现"
        w.config["daily_summary"]="off"
        import sqlite3, appicons
        from PySide6.QtWidgets import QFileDialog
        target=folder/"backup_check.sqlite3"; target.unlink(missing_ok=True)
        original_dialog=QFileDialog.getSaveFileName; QFileDialog.getSaveFileName=staticmethod(lambda *a, **k: (str(target), ""))
        try: w.backup_data()
        finally: QFileDialog.getSaveFileName=original_dialog
        copy=sqlite3.connect(target); rows=copy.execute("SELECT COUNT(*) FROM usage").fetchone()[0]; copy.close()
        assert rows>0,"备份里没有记录"
        w.current=Activity("distraction","chrome.exe","bilibili.com","B站"); w.tray_state=None; w.update_tray_icon(); assert w.tray_state=="distraction"
        w.toggle_pause(); w.update_tray_icon(); assert w.tray_state=="paused"; w.toggle_pause()
        windows.paths["python-check.exe"]=sys.executable; assert not appicons.icon_for("python-check.exe","work").isNull()
        assert not appicons.icon_for("bilibili.com","distraction").isNull() and not appicons.icon_for("nowhere.exe","unknown").isNull()
        # Back after a long time away: prompt, count as work, undo; per-program daily limit.
        from core import Activity as A
        w.config["return_prompt_minutes"]=15; w.idle_now=False; w.track_away(A("distraction","chrome.exe","www.bilibili.com"),0,False)
        w.idle_now=True; w.track_away(A("idle"),600,False); w.away_since=datetime.now()-timedelta(minutes=20)
        w.idle_now=False; w.track_away(A("distraction","chrome.exe","www.bilibili.com"),0,False); assert w.return_prompt is not None,"回来时没有询问补记"
        assert set(w.return_prompt.choices)=={"idle","distraction","work"} and w.return_prompt.choices["distraction"].property("role")=="primary","回来补记的选项不对"
        w.return_prompt.choices["idle"].click(); q.processEvents(); assert w.return_prompt is None
        distracted=now-timedelta(hours=5); w.store.add(distracted,distracted+timedelta(minutes=12),A("idle")); w.store.flush()
        before=w.store.totals(day); w.count_away_as("distraction",distracted,distracted+timedelta(minutes=12))
        after=w.store.totals(day); assert after["distraction"]-before["distraction"]>=700 and before["idle"]-after["idle"]>=700,"算作摸鱼没有生效"
        w.toast.run_action(); assert abs(w.store.totals(day)["distraction"]-before["distraction"])<1,"算作摸鱼撤销失败"
        away=now-timedelta(hours=3); w.store.add(away,away+timedelta(minutes=18),A("idle")); w.store.flush()
        idle_before=w.store.totals(day)["idle"]; w.count_away_as_work(away,away+timedelta(minutes=18))
        assert w.store.totals(day)["idle"]<=idle_before-1000,"补记没有生效"; w.toast.run_action()
        assert abs(w.store.totals(day)["idle"]-idle_before)<1,"补记撤销失败"
        w.store.add(now-timedelta(hours=4),now-timedelta(hours=4)+timedelta(seconds=70),A("distraction","limit-check.exe"))
        w.config["app_limits"]={"limit-check.exe":1}; w.goal_flags={"day":day}; w.check_app_limits(day)
        assert w.goal_flags.get("app:limit-check.exe"),"每日上限没有提醒"; w.check_app_limits(day); w.config["app_limits"]={}
        # Goals: announced once per day.
        w.config["work_goal_minutes"]=1; w.goal_flags={}; w.store.add(now,now+timedelta(seconds=70),Activity("work","goal-check.exe"))
        w.check_goals(); assert w.goal_flags.get("work"),"工作目标未提示"
        w.toast.hide(); w.check_goals(); assert not w.toast.isVisible(),"目标重复提示"
        # Rest reminder after continuous use; idle resets the counter.
        w.config["rest_after_minutes"]=1; w.active_run=0
        for _ in range(30): w.track_rest(Activity("work","x.exe"))
        w.idle_now=True; w.track_rest(Activity("idle")); w.idle_now=False; assert w.active_run==0  # real inactivity resets it
        for _ in range(60): w.track_rest(Activity("work","x.exe"))
        assert w.active_run==0 and w.toast.isVisible(),"休息提醒未触发"
        # Start with Windows: the test instance writes only inside its own data folder.
        assert "startup_test" in str(w.startup_link)
        w.set_autostart(True); assert w.startup_link.exists() and w.autostart_check.isChecked(),"开机启动快捷方式未创建"
        w.set_autostart(False); assert not w.startup_link.exists() and not w.autostart_check.isChecked()
        # Settings page writes every option back.
        w.navigate(3); w.goal_spin.setValue(300); w.remind_check.setChecked(False); w.save_preferences()
        assert w.config["work_goal_minutes"]==300 and w.config["remind_distraction"] is False and not w.reminder_spin.isEnabled()
        w.remind_check.setChecked(True); w.save_preferences(); assert w.config["remind_distraction"] is True
        # Theme choice is saved and offers a restart; the running palette stays until then.
        w.choose_theme("dark"); assert w.config["theme"]=="dark" and w.theme_restart.isVisibleTo(w),"主题选择未保存"
        w.choose_theme("indigo"); assert not w.theme_restart.isVisibleTo(w)
        # Analysis page: range ranking and hour profile come from the same records.
        w.navigate(1); assert w.range_rank.entries and w.range_rank.entries[0][3]>0,"范围排行为空"
        # A day picked in the calendar never makes the page wider than the window, nor scrolls it.
        from PySide6.QtCore import QEvent,QPoint
        from PySide6.QtWidgets import QToolTip
        w.navigate(1); bar=w.analysis_view.verticalScrollBar(); bar.setValue(40); q.processEvents()
        for back in (2,5):
            w.show_analysis_day((now.date()-timedelta(days=back)).isoformat(),keep_scroll=True); q.processEvents()
            assert w.analysis_view.horizontalScrollBar().maximum()==0,"选中某天后页面比窗口宽"
            assert bar.value()==min(40,bar.maximum()),"点日历后页面滚动了"
        # A chart's tooltip goes away as soon as the pointer leaves it.
        QToolTip.showText(w.heatmap.mapToGlobal(QPoint(20,20)),"check",w.heatmap); q.processEvents()
        if QToolTip.isVisible():
            QApplication.sendEvent(w.heatmap,QEvent(QEvent.Type.Leave)); assert not QToolTip.isVisible(),"离开后提示框还在"
        # 更多 ranges: 上周 is Monday–Sunday of last week; picking a day or a segment leaves it again.
        w.choose_custom("lastweek"); first,last=w.analysis_dates(); monday=now.date()-timedelta(days=now.weekday())
        assert (first,last)==((monday-timedelta(days=7)).isoformat(),(monday-timedelta(days=1)).isoformat()) and w.more_range.text()=="上周","上周范围不对"
        w.choose_custom("custom",(now.date()-timedelta(days=9)).isoformat(),(now.date()-timedelta(days=2)).isoformat()); assert w.analysis_span()==(8,2)
        w.range_combo.setCurrentIndex(0); w.choose_range(); assert w.analysis_custom is None and not w.more_range.isChecked() and w.analysis_span()==(1,0)
        # 按时间: one stretch of a past day is re-labelled without touching the rest or the rules; undo restores it.
        past=datetime.combine(now.date()-timedelta(days=3),datetime.min.time())+timedelta(hours=9); past_day=past.date().isoformat()
        w.store.add(past,past+timedelta(minutes=30),Activity("work","stretch.exe")); w.store.add(past+timedelta(minutes=30),past+timedelta(minutes=50),Activity("idle"))
        w.store.add(past+timedelta(minutes=50),past+timedelta(minutes=80),Activity("work","stretch.exe"))
        w.open_manager(day=past_day); w.manager_view.setCurrentIndex(1); q.processEvents()
        assert w.table.rowCount()==3 and w.table.item(1,1).text()=="09:30 – 09:50" and not w.change_future.isVisibleTo(w),"按时间视图不对"
        rules_before=json.dumps(w.config["app_rules"],sort_keys=True); w.table.selectRow(2); w.apply_classification("distraction")
        totals=w.store.totals(past_day); assert (totals["work"],totals["distraction"])==(1800,1800) and json.dumps(w.config["app_rules"],sort_keys=True)==rules_before,"只改一段失败"
        w.undo_correction(); assert w.store.totals(past_day)["work"]==3600
        # 补记: the away stretch becomes work under the given name, and can be undone.
        assert w.store.add_manual(past+timedelta(minutes=30),past+timedelta(minutes=50),"work","开会")==1200
        w.records_changed(); w.refresh_manager(force=True); assert w.table.item(1,2).text()=="开会（补记）" and w.store.totals(past_day)["idle"]==0,"补记失败"
        w.undo_correction(); assert w.store.totals(past_day)["idle"]==1200
        w.manager_view.setCurrentIndex(0); q.processEvents(); assert w.table.horizontalHeaderItem(1).text()=="应用" and w.change_future.isVisibleTo(w)
        # 小类: overview breakdown, filtering the activity list, re-assigning a site (undoable), deleting, daily limit.
        import subcats
        w.store.add(past+timedelta(hours=5),past+timedelta(hours=5,minutes=25),Activity("distraction","chrome.exe","www.douyin.com"))
        w.store.add(past+timedelta(hours=6),past+timedelta(hours=6,minutes=10),Activity("distraction","chrome.exe","www.bilibili.com")); w.store.flush()
        w.navigate(0); w.refresh(force=True); assert not hasattr(w,"sub_overview"),"总览不应有小类卡片"
        w.open_manager(category="distraction",day=past_day,sub=("distraction","short")); q.processEvents()
        assert w.table.rowCount()==1 and w.sub_chip.isVisible() and "短视频" in w.sub_chip.text(),"按小类筛选失败"
        assert w.table.item(0,0).data(CategoryDelegate.SUB)=="短视频","活动管理没有显示小类"
        w.clear_sub_filter(); assert not w.sub_chip.isVisible() and w.table.rowCount()>=2
        w.set_sub("distraction",["bilibili.com"],"short")
        assert subcats.lookup("distraction","chrome.exe","www.bilibili.com",w.config)=="short" and subcats.lookup("work","chrome.exe","www.bilibili.com",w.config)=="course"
        w.toast.run_action(); assert subcats.lookup("distraction","chrome.exe","www.bilibili.com",w.config)=="video","小类修改撤销失败"
        w.delete_sub("distraction","video"); assert all(i["id"]!="video" for i in w.config["subcategories"]["distraction"])
        assert subcats.lookup("distraction","","bilibili.com",w.config)==""; w.toast.run_action()
        assert any(i["id"]=="video" for i in w.config["subcategories"]["distraction"]),"删除小类撤销失败"
        w.store.add(now-timedelta(minutes=50),now-timedelta(minutes=29),Activity("distraction","chrome.exe","www.douyin.com")); w.store.flush()
        w.set_sub_limit("short",20); w.sub_limit_timer.stop(); w.goal_flags={}; w.toast.hide(); w.check_sub_limits(day)
        assert w.goal_flags.get("sub:short") and w.toast.isVisible() and "短视频" in w.toast.label.text(),"小类上限没有提醒"
        w.toast.hide(); w.check_sub_limits(day); assert not w.toast.isVisible(),"小类上限重复提醒"
        assert "短视频今天" in w.sub_limit_note(Activity("distraction","chrome.exe","www.douyin.com"))
        w.navigate(3); w.switch_settings(2); q.processEvents(); assert w.sub_lists["distraction"].count()==len(w.config["subcategories"]["distraction"])
        w.set_sub_limit("short",0); w.switch_settings(0); w.navigate(1); w.sub_kind.setCurrentIndex(1); q.processEvents(); w.sub_kind.setCurrentIndex(0)
        # Backup and restore: records come back exactly, and what was there is kept in a file beside them.
        import sqlite3
        saved=w.data_dir/"backup_check.sqlite3"; w.store.flush(); target=sqlite3.connect(saved); w.store.db.backup(target); target.close()
        before_restore=w.store.totals(past_day); w.store.add(past+timedelta(hours=3),past+timedelta(hours=4),Activity("work","later.exe")); w.store.flush()
        assert w.inspect_backup(saved)[0]>=1; aside=w.restore_from(saved)
        assert w.store.totals(past_day)==before_restore and aside.exists() and w.inspect_backup(aside)[0]>=1,"恢复备份失败"
        try: w.inspect_backup(w.config_path); raise AssertionError("把设置文件当成了备份")
        except ValueError: pass
        # Settings: two tabs; the rule table fills on demand. First-run dialog hands back a goal in minutes.
        w.navigate(3); w.switch_settings(1); assert w.settings_stack.currentIndex()==1 and w.rules_table.rowCount()>0 and w.settings_tab.currentIndex()==1
        w.switch_settings(0)
        from desktop import WelcomeDialog
        welcome=WelcomeDialog(w,360,False); welcome.goal.setValue(4); welcome.autostart.setChecked(True); assert welcome.values()==(240,True); welcome.deleteLater()
        # Report export writes the chosen parts (dialog skipped: call the exporter directly).
        import report
        out=folder/"report_check"; written=report.export(w.store,out,day,day,["daily","apps"],"both","#4B5FE0")
        assert (out/"专注时间报告.html").exists() and (out/"每日汇总.csv").exists() and len(written)==3,"报告导出失败"
        # Hidden in the tray: sampling continues but no widget work happens.
        w.navigate(0); w.hide(); q.processEvents(); w.now_label.setText("sentinel"); w.refresh()
        assert w.now_label.text()=="sentinel","隐藏时仍在刷新界面"
        w.show(); q.processEvents(); w.refresh(force=True); assert w.now_label.text()!="sentinel"
        # A second launch wakes the running instance through a named event.
        name="Local\\FocusTimeAssistantCheck%d.Show"%time.time_ns(); handle=windows.wake_event(name,True)
        assert windows.signal_wake(name) and windows.wait(handle,0),"单实例唤起失败"
        w.navigate(0); q.processEvents()
        w.current=Activity("distraction","chrome.exe","bilibili.com","B站","验证")
        previous=windows.u.GetForegroundWindow()
        w.maybe_remind(time.monotonic()); q.processEvents()
        assert w.popup is not None
        assert previous==windows.u.GetForegroundWindow(),"提醒抢占了输入焦点"
        assert w.popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        w.allow_learning("B站"); assert w.popup is None
        assert w.learning["B站"]>time.monotonic()+1790
        w.reminded={}; w.maybe_remind(time.monotonic()); assert w.popup is None,"继续摸鱼期间仍然提醒"
        assert w.current.category=="distraction","继续摸鱼不应改变分类"
        w.cancel_learning(); assert "B站" not in w.learning
        w.reminded={}; w.maybe_remind(time.monotonic()); assert w.popup is not None,"恢复提醒后没有提醒"; w.dismiss_reminder()
        w.toggle_pause(); assert w.paused
        w.toggle_pause(); assert not w.paused
        evidence.update(status="PASS",dpi_awareness=awareness,device_pixel_ratio=w.devicePixelRatioF(),logical_dpi=q.primaryScreen().logicalDotsPerInch(),pages=5,
            checks=["card text fits","navigation","search and empty state","selection survives refresh","batch classification conserves time","undo preserves totals","rules persist","focus ring only after Tab","rule category filter and quick change","ignore removes time and undo restores","own window recorded as away","own window category adjustable","skip own window","theme choice saved","range ranking","report export","desktop / background focus not counted","system surfaces adjustable","charts drawn with animations off","focus session recorded","focus task, floating timer and break","timed pause resumes","analysis calendar and findings","daily summary, backup, tray colour, program icons","return prompt and away-as-work undo","per-program daily limit","goals announced once","rest reminder","start with Windows toggle","settings saved",
                    "table cells update in place","no widget refresh while hidden","second launch wakes instance","notification retains focus","keep scrolling snoozes reminders only","pause/resume"])
        (folder/"qt_desktop_check.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(evidence,ensure_ascii=True))
    except BaseException as exc:
        import traceback
        traceback.print_exc(); errors.append(str(exc))
    finally:
        w.shutdown(); q.quit()


QTimer.singleShot(600,check)
q.exec()
if errors: raise AssertionError(errors[0])
