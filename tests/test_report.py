"""Themes and the exportable report."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
import theme
from core import Activity, Store
import report

TEMP = Path(__file__).resolve().parents[1]/"90_过程文件"/"2026-11-10_性能与界面优化"/"test_tmp"
TEMP.mkdir(parents=True, exist_ok=True)


class ThemeTests(unittest.TestCase):
    def test_every_theme_defines_every_token(self):
        keys = set(theme.palette("indigo")[0])
        for name in theme.THEMES:
            tokens, (colors, tints) = theme.palette(name)
            self.assertEqual(set(tokens), keys, name)
            self.assertEqual(set(colors), set(tints), name)

    def test_use_switches_constants_and_falls_back(self):
        try:
            theme.use("dark"); self.assertTrue(theme.P["dark"]); self.assertEqual(theme.ACCENT, theme.DARK["accent"])
            theme.use("no-such-theme"); self.assertEqual(theme.THEME, "indigo")
        finally:
            theme.use("indigo")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=TEMP)
        self.store = Store(Path(self.folder.name)/"activity.sqlite3")
        start = datetime(2026, 11, 9, 10, 0)
        self.store.add(start, start+timedelta(minutes=50), Activity("work", "code.exe"))
        self.store.add(start+timedelta(days=1), start+timedelta(days=1, minutes=20), Activity("distraction", "chrome.exe", "www.bilibili.com"))
        self.store.add_session(start, 25, 1500, 1400, 30, True)

    def tearDown(self):
        self.store.close(); self.folder.cleanup()

    def test_html_and_csv_contain_the_numbers(self):
        out = Path(self.folder.name)/"report"
        written = report.export(self.store, out, "2026-11-09", "2026-11-10", ["daily", "apps", "hours", "sessions", "details"], "both", "#4B5FE0")
        names = {path.name for path in written}
        self.assertIn("专注时间报告.html", names); self.assertIn("每日汇总.csv", names); self.assertIn("专注记录.csv", names)
        html = (out/"专注时间报告.html").read_text(encoding="utf-8")
        self.assertIn("www.bilibili.com", html); self.assertIn("50分钟", html); self.assertIn("<svg", html)
        daily = (out/"每日汇总.csv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(daily[1].split(",")[:2], ["2026-11-09", "3000"])

    def test_parts_and_format_are_respected(self):
        out = Path(self.folder.name)/"csv_only"
        written = report.export(self.store, out, "2026-11-09", "2026-11-10", ["apps"], "csv", "#4B5FE0")
        self.assertEqual([path.name for path in written], ["应用与网站排行.csv"])

    def test_range_queries(self):
        apps = self.store.range_apps("2026-11-09", "2026-11-10")
        self.assertEqual(apps[0][:3], ("code.exe", "", "work")); self.assertEqual(round(apps[0][3]), 3000)
        days, profile = self.store.hour_profile("2026-11-09", "2026-11-10")
        self.assertEqual(days, 2)
        self.assertAlmostEqual(sum(s for h, c, s in profile if c == "work"), 1500)


if __name__ == "__main__":
    unittest.main()
