from datetime import datetime,timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from core import Activity,Store

TEMP=Path(__file__).resolve().parents[1]/"90_过程文件"/"2026-11-10_v02验证"/"test_tmp"
TEMP.mkdir(parents=True,exist_ok=True)


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=TEMP)
        self.store=Store(Path(self.folder.name)/"activity.sqlite3")
        self.start=datetime(2026,11,10,12,59,30)
        self.day="2026-11-10"

    def tearDown(self):
        self.store.close(); self.folder.cleanup()

    def add(self,category,seconds=90,app="code.exe"):
        self.store.add(self.start,self.start+timedelta(seconds=seconds),Activity(category,app))

    def test_hourly_split_preserves_exact_total(self):
        self.add("work")
        rows=self.store.hourly(self.day)
        self.assertEqual(rows,[("12","work",30.0),("13","work",60.0)])
        self.assertEqual(sum(row[2] for row in rows),sum(self.store.totals(self.day).values()))

    def test_migration_keeps_daily_legacy_data_out_of_hourly_chart(self):
        self.store.db.execute("INSERT INTO usage VALUES (?,?,?,?,?)",(self.day,"work","legacy.exe","",1000)); self.store.db.commit()
        self.assertEqual(self.store.totals(self.day)["work"],1000)
        self.assertEqual(self.store.hourly(self.day),[])

    def test_reclassification_merges_target_and_preserves_total(self):
        self.add("unknown"); self.add("work",10)
        self.assertEqual(self.store.reclassify(self.day,[("unknown","code.exe","")],"work"),1)
        self.assertEqual(self.store.totals(self.day)["work"],100)
        self.assertEqual(self.store.totals(self.day)["unknown"],0)
        self.assertEqual(sum(row[2] for row in self.store.hourly(self.day)),100)
        self.assertTrue(all(row[1]=="work" for row in self.store.hourly(self.day)))

    def test_undo_keeps_newly_accumulated_time(self):
        self.add("unknown"); self.add("work",10)
        before=self.store.details(self.day)
        self.store.reclassify(self.day,[("unknown","code.exe","")],"work")
        self.add("work",7)
        self.assertTrue(self.store.undo())
        totals=self.store.totals(self.day)
        self.assertEqual(totals["unknown"],90); self.assertEqual(totals["work"],17)
        self.assertEqual(sum(row[2] for row in self.store.hourly(self.day)),107)
        self.assertFalse(self.store.can_undo())

    def test_multiple_selected_rows_and_lifo_undo(self):
        self.add("unknown",30); self.add("distraction",10)
        keys=[("unknown","code.exe",""),("distraction","code.exe","")]
        self.store.reclassify(self.day,keys,"work")
        self.store.reclassify(self.day,[("work","code.exe","")],"unknown")
        self.assertEqual(self.store.totals(self.day)["unknown"],40)
        self.store.undo(); self.assertEqual(self.store.totals(self.day)["work"],40)
        self.store.undo(); self.assertEqual(self.store.totals(self.day)["unknown"],30)
        self.assertEqual(self.store.totals(self.day)["distraction"],10)

    def test_idle_rows_can_be_reclassified_and_undone(self):
        from core import AWAY_LABEL
        self.store.add(self.start,self.start+timedelta(seconds=90),Activity("idle"))
        self.assertEqual(self.store.reclassify(self.day,[("idle","","")],"work"),1)
        t=self.store.totals(self.day); self.assertEqual((t["idle"],t["work"]),(0,90))
        self.assertEqual([r[1] for r in self.store.details(self.day)],[AWAY_LABEL])
        self.assertEqual(sum(r[2] for r in self.store.hourly(self.day)),90)
        self.assertTrue(self.store.undo())
        t=self.store.totals(self.day); self.assertEqual((t["idle"],t["work"]),(90,0))
        self.assertEqual(self.store.details(self.day)[0][:2],("idle",""))

    def test_undo_of_a_record_from_before_away_relabelling(self):
        import json
        self.add("unknown",60); self.store.reclassify(self.day,[("unknown","code.exe","")],"work")
        ident,payload=self.store.db.execute("SELECT id,payload FROM corrections").fetchone(); data=json.loads(payload)
        data["rows"]=[r[:4] for r in data["rows"]]; data["minutes"]=[m[:5] for m in data["minutes"]]  # the old shape
        self.store.db.execute("UPDATE corrections SET payload=? WHERE id=?",(json.dumps(data),ident)); self.store.db.commit()
        self.assertTrue(self.store.undo()); self.assertEqual(self.store.totals(self.day)["unknown"],60)
        self.assertFalse(self.store.can_undo())

    def test_other_day_and_app_remain_unchanged(self):
        self.add("unknown",30); self.add("unknown",40,"chrome.exe")
        self.store.add(self.start-timedelta(days=1),self.start-timedelta(days=1)+timedelta(seconds=50),Activity("unknown","code.exe"))
        self.store.reclassify(self.day,[("unknown","code.exe","")],"work")
        self.assertEqual(self.store.totals(self.day)["unknown"],40)
        self.assertEqual(self.store.totals("2026-11-09")["unknown"],50)

    def test_ignored_activity_is_never_stored(self):
        self.add("ignore",120,"pythonw.exe")
        self.assertEqual(self.store.details(self.day),[])
        self.assertEqual(self.store.hourly(self.day),[])

    def test_ignore_removes_time_and_undo_restores_it(self):
        self.add("work",90,"pythonw.exe"); self.add("work",30)
        self.assertEqual(self.store.reclassify(self.day,[("work","pythonw.exe","")],"ignore"),1)
        self.assertEqual(self.store.totals(self.day)["work"],30)
        self.assertEqual(sum(row[2] for row in self.store.hourly(self.day)),30)
        self.add("work",5)  # new time after the correction must survive the undo
        self.assertTrue(self.store.undo())
        self.assertEqual(self.store.totals(self.day)["work"],125)
        self.assertEqual(sum(row[2] for row in self.store.hourly(self.day)),125)
        self.assertFalse(self.store.can_undo())

    def test_focus_sessions_are_counted_per_day(self):
        self.assertEqual(self.store.sessions(self.day),(0,0.0))
        self.store.add_session(self.start,25,1500,1400,60,True)
        self.store.add_session(self.start,25,600,500,0,False)
        self.assertEqual(self.store.sessions(self.day),(1,2100.0))
        self.assertEqual(self.store.sessions("2026-11-09"),(0,0.0))

    def test_session_task_names_and_old_databases(self):
        self.store.add_session(self.start,25,1500,1400,60,True,"  论文第三节  ")
        self.store.add_session(self.start,25,600,500,0,False)
        rows=self.store.session_list(self.day,self.day)
        self.assertEqual([r[6] for r in rows],["论文第三节",""])
        # A database from before task names gains the column without losing sessions.
        path=Path(self.folder.name)/"old.sqlite3"; db=sqlite3.connect(path)
        db.execute("CREATE TABLE focus_sessions (id INTEGER PRIMARY KEY, day TEXT NOT NULL, started TEXT NOT NULL, planned INTEGER NOT NULL, seconds REAL NOT NULL, work REAL NOT NULL, distraction REAL NOT NULL, completed INTEGER NOT NULL)")
        db.execute("INSERT INTO focus_sessions(day,started,planned,seconds,work,distraction,completed) VALUES ('2026-11-01','2026-11-01T09:00:00',25,1500,1500,0,1)"); db.commit(); db.close()
        old=Store(path); self.assertEqual(old.session_list("2026-11-01","2026-11-01")[0][6],""); old.close()

    def test_sampled_time_is_committed_in_batches(self):
        path=Path(self.folder.name)/"activity.sqlite3"
        def on_disk():
            other=sqlite3.connect(path); n=other.execute("SELECT COALESCE(SUM(seconds),0) FROM usage").fetchone()[0]; other.close(); return n
        self.add("work",30)
        self.assertEqual(self.store.totals(self.day)["work"],30)  # visible at once to the app
        self.assertEqual(on_disk(),0)
        self.store.flush(); self.assertEqual(on_disk(),30)
        self.add("work",10); self.store.close(); self.assertEqual(on_disk(),40)  # quitting commits
        self.store=Store(path)

    def test_away_time_can_be_counted_as_work_and_back(self):
        start=datetime(2026,11,10,14,0); self.store.add(start,start+timedelta(minutes=20),Activity("idle"))
        self.store.add(start+timedelta(minutes=20),start+timedelta(minutes=25),Activity("work","code.exe"))
        moved=self.store.move_minutes(start,start+timedelta(minutes=20),("idle","",""),("work","离开期间（补记）",""))
        self.assertAlmostEqual(moved,1200); t=self.store.totals(self.day)
        self.assertAlmostEqual(t["idle"],0); self.assertAlmostEqual(t["work"],1500)
        self.store.move_minutes(start,start+timedelta(minutes=20),("work","离开期间（补记）",""),("idle","",""))
        t=self.store.totals(self.day); self.assertAlmostEqual(t["idle"],1200); self.assertAlmostEqual(t["work"],300)

    def test_used_today_counts_subdomains_across_categories(self):
        self.store.add(self.start,self.start+timedelta(seconds=60),Activity("distraction","chrome.exe","www.bilibili.com"))
        self.store.add(self.start,self.start+timedelta(seconds=30),Activity("work","chrome.exe","bilibili.com"))
        self.store.add(self.start,self.start+timedelta(seconds=40),Activity("work","code.exe"))
        self.assertEqual(self.store.used_today(self.day,"bilibili.com"),90)
        self.assertEqual(self.store.used_today(self.day,"code.exe"),40)

    def test_site_icons_are_checked_before_saving(self):
        import base64
        from bridge import BrowserState
        state=BrowserState(); state.icon_dir=Path(self.folder.name)/"icons"
        png=base64.b64encode(bytes([0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A])+b"0"*40).decode()
        state.put_icon({"host":"www.bilibili.com","png":png})
        self.assertTrue((state.icon_dir/"www.bilibili.com.png").exists()); self.assertEqual(state.icon_version,1)
        for bad in ({"host":"www.bilibili.com","png":base64.b64encode(b"GIF89a").decode()}, {"host":"../evil","png":png}, {"host":"a.com","png":"!!"}):
            with self.assertRaises((ValueError,TypeError)): state.put_icon(bad)

    def test_program_paths_and_names(self):
        import windows
        self.assertTrue(windows.describe(sys.executable))  # e.g. "Python"
        before = windows.paths_version; windows._learn("unit-check.exe", sys.executable)
        self.assertEqual(windows.paths_version, before+1); self.assertEqual(windows.find_path("unit-check.exe"), sys.executable)
        self.assertIsNone(windows.find_path("no-such-program-xyz.exe"))
        windows.paths.pop("unit-check.exe", None)

    def test_goal_streaks(self):
        from core import streaks
        goal=3600; work={"2026-11-01":4000,"2026-11-02":3600,"2026-11-03":100,"2026-11-04":5000,"2026-11-05":5000,"2026-11-06":5000,"2026-11-07":7000}
        self.assertEqual(streaks(work,goal,"2026-11-07"),(4,4))
        self.assertEqual(streaks({**work,"2026-11-08":10},goal,"2026-11-08"),(4,4))  # unfinished today keeps the run
        self.assertEqual(streaks(work,goal,"2026-11-09"),(0,4))                       # a missed day ends it
        self.assertEqual(streaks({"2026-11-07":1800},0,"2026-11-07"),(1,1))           # no goal: 30 minutes counts
        self.assertEqual(streaks({},goal,"2026-11-07"),(0,0))

    def test_insights_describe_the_range(self):
        from insights import insights
        for back in range(14):
            day=datetime(2026,11,10,9,0)-timedelta(days=back)
            self.store.add(day,day+timedelta(minutes=50 if back<7 else 30),Activity("work","code.exe"))
            self.store.add(day+timedelta(hours=12),day+timedelta(hours=12,minutes=20),Activity("distraction","chrome.exe","www.bilibili.com","B站"))
        self.store.add_session(datetime(2026,11,10,9,0),25,1500,1500,0,True,"论文")
        found=insights(self.store,"2026-11-04","2026-11-10")
        text=" ".join(line for _,line in found)
        self.assertIn("9–10 点",text); self.assertIn("21–22 点",text); self.assertIn("B站",text)
        self.assertIn("多",text); self.assertIn("「论文」",text)
        self.assertEqual(insights(self.store,"2020-01-01","2020-01-07"),[])

    def test_same_time_yesterday_uses_minute_records_only(self):
        self.add("work",90)  # 12:59:30 – 13:01:00
        self.assertEqual(self.store.totals_until(self.day,"13:00")["work"],30)
        self.assertIsNone(self.store.totals_until("2026-11-09","13:00"))

    def test_undo_survives_restart(self):
        self.add("unknown",30)
        self.store.reclassify(self.day,[("unknown","code.exe","")],"work")
        self.store.close(); self.store=Store(Path(self.folder.name)/"activity.sqlite3")
        self.assertTrue(self.store.undo()); self.assertEqual(self.store.totals(self.day)["unknown"],30)


if __name__=="__main__": unittest.main()
