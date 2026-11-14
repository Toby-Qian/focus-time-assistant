from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
from core import DEFAULTS, validate_config
import copy
import subcats


def config():
    result = copy.deepcopy(DEFAULTS); result["token"] = "x"; validate_config(result)
    return result


class SubcategoryTests(unittest.TestCase):
    def test_built_in_choices_follow_category_and_site(self):
        c = config()
        self.assertEqual(subcats.lookup("distraction", "chrome.exe", "www.douyin.com", c), "short")
        self.assertEqual(subcats.lookup("distraction", "chrome.exe", "search.bilibili.com", c), "video")
        self.assertEqual(subcats.lookup("work", "chrome.exe", "www.bilibili.com", c), "course")  # B站 counted as work
        self.assertEqual(subcats.lookup("work", "code.exe", "", c), "code")
        self.assertEqual(subcats.lookup("work", "chrome.exe", "unknown-site.org", c), subcats.OTHER)
        self.assertEqual(subcats.lookup("system", "explorer.exe", "", c), subcats.OTHER)
        self.assertEqual(subcats.lookup("distraction", "code.exe", "", c), subcats.OTHER)  # no 摸鱼 choice for VS Code

    def test_user_choice_wins_and_other_overrides_built_in(self):
        c = config()
        c["sub_rules"]["bilibili.com"] = {"distraction": "short"}
        self.assertEqual(subcats.lookup("distraction", "chrome.exe", "www.bilibili.com", c), "short")
        self.assertEqual(subcats.lookup("work", "chrome.exe", "www.bilibili.com", c), "course")  # the other category keeps its default
        c["sub_rules"]["douyin.com"] = {"distraction": ""}
        self.assertEqual(subcats.lookup("distraction", "", "douyin.com", c), subcats.OTHER)
        c["sub_rules"]["chrome.exe"] = {"work": "papers"}  # the website is more specific than the browser
        self.assertEqual(subcats.lookup("work", "chrome.exe", "arxiv.org", c), "papers")
        self.assertEqual(subcats.lookup("work", "chrome.exe", "github.com", c), "code")

    def test_deleted_subcategory_falls_back_to_other_and_totals_add_up(self):
        c = config()
        c["subcategories"]["distraction"] = [i for i in c["subcategories"]["distraction"] if i["id"] != "short"]
        self.assertEqual(subcats.lookup("distraction", "", "douyin.com", c), subcats.OTHER)
        rows = [("distraction", "chrome.exe", "www.douyin.com", 600), ("distraction", "chrome.exe", "www.bilibili.com", 300),
                ("work", "code.exe", "", 1200), ("work", "开会（补记）", "", 900), ("idle", "", "", 50)]
        sums = subcats.totals(rows, c)
        self.assertEqual(sums["distraction"], {"": 600, "video": 300})
        self.assertEqual(sums["work"], {"code": 1200, "": 900})
        self.assertEqual(sum(sums["work"].values()), 2100)

    def test_settings_are_repaired_and_limits_follow_existing_ids(self):
        c = config()
        c["subcategories"] = {"work": [{"id": "a", "name": ""}], "distraction": []}
        c["sub_rules"] = {"x.exe": "work"}; c["sub_limits"] = {"short": 20, "gone": 10, "code": 5}
        validate_config(c)
        self.assertEqual(c["subcategories"], subcats.default_list())
        self.assertEqual(c["sub_rules"], {}); self.assertEqual(c["sub_limits"], {"short": 20})

    def test_rule_key_keeps_known_subdomains_and_uses_main_domain(self):
        c = config()
        self.assertEqual(subcats.rule_key("chrome.exe", "tieba.baidu.com", c), "tieba.baidu.com")
        self.assertEqual(subcats.rule_key("chrome.exe", "www.example.com.cn", c), "example.com.cn")
        self.assertEqual(subcats.rule_key("chrome.exe", "a.b.example.org", c), "example.org")
        self.assertEqual(subcats.rule_key("Code.exe", "", c), "code.exe")
        self.assertIn("douyin.com", subcats.members("distraction", "short", c))
        c["sub_rules"]["douyin.com"] = {"distraction": "video"}
        self.assertNotIn("douyin.com", subcats.members("distraction", "short", c))
        self.assertIn("douyin.com", subcats.members("distraction", "video", c))


if __name__ == "__main__": unittest.main()
