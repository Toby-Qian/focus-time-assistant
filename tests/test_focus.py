import copy
from datetime import datetime, timedelta
import http.client
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from core import DEFAULTS, Activity, Accountant, Store, classify, hostname, load_config, site_for, validate_config
from bridge import BrowserState, start_bridge

TEST_TMP = Path(__file__).resolve().parents[1] / "90_过程文件" / "2026-11-10_验证" / "test_tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULTS)

    def test_real_domains_and_deceptive_domains(self):
        for domain in ["bilibili.com", "www.bilibili.com", "m.douyin.com", "www.xiaohongshu.com", "b23.tv"]:
            self.assertTrue(site_for(domain))
        for domain in ["bilibili.com.evil.test", "fakebilibili.com", "notdouyin.com", "google.com"]:
            self.assertFalse(site_for(domain))
        self.assertEqual(hostname("https://WWW.BILIBILI.COM./video/a?private=query"), "www.bilibili.com")

    def test_unknown_is_not_assumed_to_be_work(self):
        self.assertEqual(classify("chrome.exe", "任意页面", "", self.config).category, "unknown")
        self.assertEqual(classify("mystery.exe", "", "", self.config).category, "unknown")
        self.assertEqual(classify("WINWORD.EXE", "", "", self.config).category, "work")

    def test_title_fallback_is_browser_only(self):
        title = "学习课程_哔哩哔哩_bilibili"
        self.assertEqual(classify("chrome.exe", title, "", self.config).category, "distraction")
        self.assertEqual(classify("winword.exe", title, "", self.config).category, "work")
        self.assertEqual(classify("chrome.exe", "抖音是什么 - 搜索", "", self.config).category, "unknown")

    def test_domain_overrides_misleading_title_and_learning(self):
        title = "视频_哔哩哔哩_bilibili"
        self.assertEqual(classify("chrome.exe", title, "arxiv.org", self.config).category, "unknown")
        self.assertEqual(classify("chrome.exe", "", "bilibili.com", self.config, learning=True).category, "work")
        self.assertEqual(classify("chrome.exe", title, "bilibili.com", self.config, idle=True, learning=True).category, "idle")

    def test_specific_host_rule_wins(self):
        self.config["host_rules"] = {"bilibili.com": "distraction", "learn.bilibili.com": "work"}
        self.assertEqual(classify("chrome.exe", "", "learn.bilibili.com", self.config).category, "work")
        self.assertEqual(classify("chrome.exe", "", "www.bilibili.com", self.config).category, "distraction")

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder)/"settings.json"
            first = load_config(path)
            self.assertEqual(first, load_config(path))
            self.assertGreaterEqual(len(first["token"]), 40)
            self.assertIs(first["remember_rule"], True)

    def test_feature_settings_fall_back_instead_of_failing(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder)/"settings.json"
            path.write_text(json.dumps({"work_goal_minutes": -5, "focus_minutes": "25", "rest_after_minutes": 50,
                                        "remind_distraction": "yes", "learning_minutes": 15}), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config["work_goal_minutes"], DEFAULTS["work_goal_minutes"])
            self.assertEqual(config["focus_minutes"], DEFAULTS["focus_minutes"])
            self.assertIs(config["remind_distraction"], True)
            self.assertEqual(config["rest_after_minutes"], 50)
            self.assertEqual(config["learning_minutes"], 15)

    def test_old_settings_without_new_keys_still_load(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            path = Path(folder)/"settings.json"
            path.write_text(json.dumps({"idle_seconds": 600, "host_rules": {"arxiv.org": "work"}, "token": "x"*43}), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config["idle_seconds"], 600)
            self.assertEqual(config["host_rules"], {"arxiv.org": "work"})
            self.assertEqual(config["token"], "x"*43)
            self.assertIs(config["remember_rule"], True)

    def test_ignore_rules_for_apps_and_sites(self):
        self.config["app_rules"] = {"pythonw.exe": "ignore"}
        self.config["host_rules"] = {"localhost.test": "ignore"}
        self.assertEqual(classify("PYTHONW.EXE", "", "", self.config).category, "ignore")
        self.assertEqual(classify("chrome.exe", "", "localhost.test", self.config).category, "ignore")
        self.assertEqual(classify("pythonw.exe", "", "", self.config, idle=True).category, "idle")
        validate_config(self.config)  # "ignore" is an accepted rule target
        self.config["app_rules"] = {"x.exe": "bogus"}
        with self.assertRaises(ValueError):
            validate_config(self.config)

    def test_system_tools_category(self):
        from charts import focus_ratio
        self.assertEqual(classify("Explorer.EXE", "", "", self.config).category, "system")
        self.assertEqual(classify("taskmgr.exe", "", "", self.config).category, "system")
        self.config["app_rules"] = {"explorer.exe": "ignore", "notepad.exe": "system"}
        self.assertEqual(classify("explorer.exe", "", "", self.config).category, "ignore")  # a user rule wins
        self.assertEqual(classify("notepad.exe", "", "", self.config).category, "system")
        validate_config(self.config)
        # System time is neutral: it never lowers the focus ratio.
        self.assertEqual(focus_ratio({"work": 60, "distraction": 20, "system": 500, "unknown": 20}), .6)

    def test_platform_domain_rule_applies_without_extension(self):
        title = "学习课程_哔哩哔哩_bilibili"
        self.config["host_rules"] = {"bilibili.com": "work"}
        self.assertEqual(classify("chrome.exe", title, "", self.config).category, "work")
        # A rule for a subdomain cannot be confirmed from a title alone.
        self.config["host_rules"] = {"learn.bilibili.com": "work"}
        self.assertEqual(classify("chrome.exe", title, "", self.config).category, "distraction")
        # Other platforms stay untouched, and a browser-wide rule does not hide the platform.
        self.config["host_rules"] = {"bilibili.com": "work"}; self.config["app_rules"] = {"chrome.exe": "work"}
        self.assertEqual(classify("chrome.exe", "首页 - 小红书", "", self.config).category, "distraction")


class DisplayTests(unittest.TestCase):
    def test_share_never_shows_zero_for_recorded_time(self):
        from charts import share
        self.assertEqual(share(0, 0), "—")
        self.assertEqual(share(0, 100), "0%")
        self.assertEqual(share(3, 1000), "<1%")
        self.assertEqual(share(740, 1000), "74%")

    def test_axis_uses_whole_units(self):
        from charts import nice_scale, tick_label
        for peak in [0, 20, 59, 61, 700, 3600, 3601, 11000, 40000, 86400]:
            step, maximum = nice_scale(peak)
            self.assertGreaterEqual(maximum, peak)
            self.assertLessEqual(round(maximum/step), 4)
            labels = [tick_label(step*i) for i in range(round(maximum/step)+1)]
            self.assertFalse(any("." in label for label in labels), labels)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.path = Path(self.temp.name)/"activity.sqlite3"
        self.store = Store(self.path)
        self.counter = Accountant(self.store)
        self.start = datetime(2026, 11, 10, 12)
        self.work = Activity("work", "code.exe")
        self.play = Activity("distraction", "chrome.exe", "bilibili.com")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def tick(self, offset, activity, mono=None, idle_seconds=0):
        self.counter.tick(self.start+timedelta(seconds=offset), offset if mono is None else mono, activity, idle_seconds)

    def test_transition_charges_previous_observation(self):
        self.tick(0, self.work)
        self.tick(2, self.play)
        self.tick(4, self.work)
        totals = self.store.totals("2026-11-10")
        self.assertEqual(totals["work"], 2)
        self.assertEqual(totals["distraction"], 2)

    def test_pause_has_no_charge(self):
        self.tick(0, self.work)
        self.tick(2, None)
        self.tick(4, None)
        self.tick(6, self.work)
        self.tick(8, None)
        self.assertEqual(self.store.totals("2026-11-10")["work"], 4)

    def test_sleep_and_wall_clock_adjustment_are_not_charged(self):
        self.tick(0, self.work)
        self.tick(1000, self.work)
        self.tick(1002, self.work)
        self.assertEqual(self.store.totals("2026-11-10")["work"], 2)
        self.tick(4602, self.work, mono=1004)
        self.assertEqual(self.store.totals("2026-11-10")["work"], 2)

    def test_midnight_and_restart_are_durable(self):
        self.store.add(datetime(2026, 11, 10, 23, 59, 59), datetime(2026, 11, 11, 0, 0, 2), self.work)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.totals("2026-11-10")["work"], 1)
        self.assertEqual(self.store.totals("2026-11-11")["work"], 2)

    def test_idle_threshold_splits_interval(self):
        self.tick(0, self.work)
        self.tick(2, Activity("idle"), idle_seconds=1)
        totals = self.store.totals("2026-11-10")
        self.assertEqual(totals["work"], 1)
        self.assertEqual(totals["idle"], 1)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.state = BrowserState()
        self.token = "test-private-token"
        self.server = start_bridge(self.state, self.token, port=0)
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, token=None, origin="chrome-extension://test", host=None, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"X-Focus-Token": self.token if token is None else token, "Origin": origin,
                   "Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        body = payload or {"browser": "chrome.exe", "host": "www.bilibili.com", "title": "学习视频", "focused": True}
        connection.request("POST", "/api/browser", json.dumps(body), headers)
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        return status

    def test_authenticated_report_and_foreground_matching(self):
        self.assertEqual(self.request(), 200)
        self.assertEqual(self.state.current("chrome.exe", "学习视频"), "www.bilibili.com")
        self.assertEqual(self.state.current("chrome.exe", "另一个页面"), "")
        self.assertEqual(self.state.current("msedge.exe", "学习视频"), "")
        self.state.reports["chrome.exe"]["at"] = time.monotonic()-46
        self.assertEqual(self.state.current("chrome.exe", "学习视频"), "")

    def test_foreign_site_wrong_token_and_host_are_rejected(self):
        self.assertEqual(self.request(token="wrong"), 403)
        self.assertEqual(self.request(origin="https://evil.example"), 403)
        self.assertEqual(self.request(host="evil.example"), 404)
        self.assertFalse(self.state.connected())

    def test_full_url_cannot_be_stored_as_host(self):
        self.assertEqual(self.request(payload={"browser": "chrome.exe", "host": "https://bilibili.com/private?query=x"}), 400)

    def test_background_window_is_not_evidence(self):
        self.assertEqual(self.request(payload={"browser": "chrome.exe", "host": "bilibili.com", "title": "学习视频", "focused": False}), 200)
        self.assertEqual(self.state.current("chrome.exe", "学习视频"), "")


if __name__ == "__main__":
    unittest.main()
