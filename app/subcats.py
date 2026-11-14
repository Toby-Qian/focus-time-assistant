"""Subcategories (小类) of 工作 and 摸鱼: what kind of work, what kind of distraction.

A subcategory belongs to a program or a website, like a classification rule, and is not stored with
each record: changing "VS Code → 编程" changes every day already recorded, and the database keeps its
shape. A website can name one subcategory for each of the two categories, e.g. B站 is 视频影视 while it
counts as 摸鱼 and 学习课程 for the stretches the user counted as 工作.

Settings:
  subcategories  {"work": [{"id", "name"}], "distraction": [...]}   order = display order and colour
  sub_rules      {"app.exe" | "domain": {"work": id, "distraction": id}}   the user's own choices; "" = none
  sub_limits     {id: minutes}                                       daily limit, distraction only
"""
from __future__ import annotations

SUB_CATEGORIES = ("work", "distraction")
OTHER = ""  # no subcategory: shown as 其他工作 / 其他摸鱼
NAME_LIMIT = 12
MOST = 12  # subcategories per category

DEFAULT_SUBS = {
    "work": [("code", "编程"), ("write", "写作"), ("papers", "文献"), ("ai", "AI 助手"), ("data", "数据与设计"), ("course", "学习课程")],
    "distraction": [("short", "短视频"), ("video", "视频影视"), ("game", "游戏"), ("social", "社交"), ("shop", "购物"), ("news", "资讯")],
}

_W, _D = "work", "distraction"
# Built-in: key -> {category: id}. Websites cover their subdomains. Users override any of it in sub_rules.
DEFAULT_RULES = {}
for _keys, _cat, _sid in [
    (["code.exe", "pycharm64.exe", "devenv.exe", "idea64.exe", "clion64.exe", "webstorm64.exe", "sublime_text.exe", "notepad++.exe", "cursor.exe",
      "windowsterminal.exe", "wt.exe", "powershell.exe", "pwsh.exe", "git-bash.exe", "github.com", "gitee.com", "gitlab.com", "stackoverflow.com",
      "csdn.net", "leetcode.cn", "leetcode.com", "runoob.com", "cnblogs.com", "juejin.cn", "pypi.org", "python.org"], _W, "code"),
    (["winword.exe", "wps.exe", "powerpnt.exe", "wpp.exe", "typora.exe", "obsidian.exe", "notion.exe", "texstudio.exe", "overleaf.com",
      "notion.so", "docs.qq.com", "kdocs.cn", "yuque.com", "shimo.im"], _W, "write"),
    (["zotero.exe", "endnote.exe", "acrobat.exe", "acrord32.exe", "sumatrapdf.exe", "foxitpdfreader.exe", "cnki.net", "wanfangdata.com.cn",
      "cqvip.com", "arxiv.org", "scholar.google.com", "semanticscholar.org", "sciencedirect.com", "springer.com", "nature.com", "science.org",
      "wiley.com", "ieee.org", "acs.org", "rsc.org", "mdpi.com", "researchgate.net", "webofscience.com", "ncbi.nlm.nih.gov", "x-mol.com",
      "connectedpapers.com", "sci-hub.se"], _W, "papers"),
    (["claude.exe", "chatgpt.exe", "codex.exe", "claude.ai", "chatgpt.com", "chat.openai.com", "kimi.moonshot.cn", "kimi.com",
      "doubao.com", "yiyan.baidu.com", "chat.deepseek.com", "gemini.google.com", "tongyi.aliyun.com", "yuanbao.tencent.com"], _W, "ai"),
    (["matlab.exe", "origin.exe", "origin64.exe", "excel.exe", "et.exe", "spss.exe", "rstudio.exe", "acad.exe", "sldworks.exe", "solidworks.exe",
      "comsol.exe", "comsolmphserver.exe", "ansys.exe", "photoshop.exe", "illustrator.exe", "figma.exe", "visio.exe", "figma.com"], _W, "data"),
    (["icourse163.org", "xuetangx.com", "yuketang.cn", "chaoxing.com", "zhihuishu.com", "coursera.org", "edx.org", "khanacademy.org",
      "bilibili.com", "b23.tv"], _W, "course"),
    (["douyin.com", "iesdouyin.com", "douyin.exe", "kuaishou.com", "kuaishou.exe", "tiktok.com", "channels.weixin.qq.com"], _D, "short"),
    (["bilibili.com", "b23.tv", "bilibili.exe", "iqiyi.com", "v.qq.com", "youku.com", "mgtv.com", "youtube.com", "netflix.com",
      "huya.com", "douyu.com", "qiyiplayer.exe", "qqlive.exe", "youkuclient.exe"], _D, "video"),
    (["steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "wegame.exe", "yuanshen.exe", "genshinimpact.exe", "starrail.exe",
      "leagueclient.exe", "league of legends.exe", "valorant.exe", "cs2.exe", "dota2.exe", "4399.com", "7k7k.com", "store.steampowered.com"], _D, "game"),
    (["xiaohongshu.com", "xhslink.com", "weibo.com", "weibo.cn", "tieba.baidu.com", "zhihu.com", "douban.com", "x.com", "twitter.com",
      "instagram.com", "reddit.com", "xiaohongshu.exe"], _D, "social"),
    (["taobao.com", "tmall.com", "jd.com", "pinduoduo.com", "yangkeduo.com", "goofish.com", "dewu.com", "vip.com", "amazon.com", "amazon.cn"], _D, "shop"),
    (["toutiao.com", "news.163.com", "sina.com.cn", "sohu.com", "thepaper.cn", "hupu.com", "ifeng.com", "36kr.com"], _D, "news"),
]:
    for _key in _keys:
        DEFAULT_RULES.setdefault(_key, {})[_cat] = _sid


def default_list():
    return {cat: [{"id": sid, "name": name} for sid, name in items] for cat, items in DEFAULT_SUBS.items()}


def validate(config):
    """Repair the three settings in place; anything malformed falls back to the defaults."""
    subs = config.get("subcategories")
    ok = isinstance(subs, dict) and set(subs) == set(SUB_CATEGORIES)
    seen = set()
    if ok:
        for cat in SUB_CATEGORIES:
            items = subs[cat]
            if not isinstance(items, list) or len(items) > MOST:
                ok = False; break
            for item in items:
                if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("name"), str)
                        or not item["id"] or not 0 < len(item["name"].strip()) <= NAME_LIMIT or item["id"] in seen):
                    ok = False; break
                seen.add(item["id"])
    if not ok:
        config["subcategories"] = default_list()
    ids = {item["id"]: cat for cat in SUB_CATEGORIES for item in config["subcategories"][cat]}
    rules = config.get("sub_rules")
    if not isinstance(rules, dict) or any(not isinstance(k, str) or not 0 < len(k) <= 253 or not isinstance(v, dict)
                                          or any(c not in SUB_CATEGORIES or not isinstance(s, str) for c, s in v.items()) for k, v in rules.items()):
        config["sub_rules"] = {}
    limits = config.get("sub_limits")
    if not isinstance(limits, dict) or any(not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 1440 for v in limits.values()):
        config["sub_limits"] = {}
    # Limits of subcategories that no longer exist are dropped.
    config["sub_limits"] = {k: v for k, v in config["sub_limits"].items() if ids.get(k) == "distraction"}


def _keys(app, host):
    """Lookup keys from most to least specific: the website and its parent domains, then the program."""
    keys = []
    if host:
        host = host.lower().removeprefix("www.")
        parts = host.split(".")
        keys += [".".join(parts[i:]) for i in range(len(parts)-1)]
    if app:
        keys.append(app.lower() if app.endswith(".exe") else app)
    return keys


SECOND_LEVEL = {"com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "co.uk", "ac.uk", "com.hk", "com.tw", "co.jp"}


def rule_key(app, host, config):
    """Where a choice for this record is saved: the most specific website key that already has a rule
    (tieba.baidu.com stays apart from baidu.com), else the site's main domain, else the program."""
    if not host:
        return app.lower() if app.endswith(".exe") else app
    for key in _keys("", host):
        if key in config["sub_rules"] or key in DEFAULT_RULES: return key
    parts = host.lower().removeprefix("www.").split(".")
    keep = 3 if ".".join(parts[-2:]) in SECOND_LEVEL and len(parts) >= 3 else 2
    return ".".join(parts[-keep:])


def lookup(category, app, host, config):
    """The subcategory id of a record, or OTHER. The user's choice wins over the built-in one for the
    same key; a more specific key (the website) wins over a less specific one (the browser)."""
    if category not in SUB_CATEGORIES:
        return OTHER
    valid = {item["id"] for item in config["subcategories"][category]}
    rules = config["sub_rules"]
    for key in _keys(app, host):
        for source in (rules, DEFAULT_RULES):
            choice = source.get(key, {}).get(category)
            if choice is not None:
                return choice if choice in valid else OTHER
    return OTHER


def name(category, sid, config):
    for item in config["subcategories"].get(category, []):
        if item["id"] == sid: return item["name"]
    return "其他工作" if category == "work" else "其他摸鱼"


def order(category, config):
    """Ids in display order, OTHER last."""
    return [item["id"] for item in config["subcategories"][category]]+[OTHER]


def totals(rows, config):
    """{category: {id: seconds}} for work and distraction from (category, app, host, seconds) rows."""
    result = {cat: {} for cat in SUB_CATEGORIES}
    for category, app, host, seconds in rows:
        if category in result:
            sid = lookup(category, app, host, config)
            result[category][sid] = result[category].get(sid, 0.0)+seconds
    return result


def members(category, sid, config):
    """Keys (programs / websites) that point at this subcategory, the user's own first."""
    found = []
    for source in (config["sub_rules"], DEFAULT_RULES):
        for key, choice in source.items():
            if choice.get(category) == sid and key not in found:
                # A built-in entry the user re-pointed elsewhere no longer belongs here.
                if source is DEFAULT_RULES and config["sub_rules"].get(key, {}).get(category, sid) != sid: continue
                found.append(key)
    return found


def new_id(config):
    used = {item["id"] for cat in SUB_CATEGORIES for item in config["subcategories"][cat]}
    number = 1
    while f"u{number}" in used: number += 1
    return f"u{number}"
