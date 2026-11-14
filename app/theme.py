"""Design tokens, palettes, the Qt style sheet and resolution-independent vector icons.

A palette is chosen once at start-up with use(name), before the interface modules
are imported, so every `from theme import ACCENT` style constant picks it up.
All sizes in the style sheet are logical pixels; Qt multiplies them by the monitor's
device pixel ratio, so text and icons stay sharp at 100–300 %.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

UI_FONT = "Microsoft YaHei UI"
NUM_FONT = "Segoe UI"

LIGHT = {
    "bg": "#F4F6FA", "card": "#FFFFFF", "line": "#E5E9F1", "ink": "#18213A", "muted": "#56627A", "subtle": "#667186",
    "body": "#4A566C", "track": "#EEF1F6", "grid": "#F0F2F6", "axis": "#E3E7EF", "lane": "#F3F5F9", "tick": "#DCE1EA",
    "field": "#DCE2EE", "disabled_text": "#AEB5C4", "disabled_bg": "#F6F7FA", "disabled_line": "#ECEEF3",
    "row_line": "#F0F2F7", "row_hover": "#F7F8FC", "scroll": "#D3D9E5", "scroll_hover": "#B9C1D2",
    "seg": "#ECEFF5", "seg_hover": "#E2E6EF", "note_hover": "#EEF1F7", "tooltip": "#2E3A52", "nav": "#5D6A80",
    "check_line": "#C3CCDD", "toast": "#1E2638", "toast_text": "#F3F5FA", "toast_hover": "#2C3650",
    "danger": "#D9573B", "offline": "#C4CAD6", "on_accent": "#FFFFFF", "shadow": (24, 33, 58), "dark": False,
}
ACCENTS = {
    "indigo": {"label": "靛蓝", "accent": "#4B5FE0", "accent_hover": "#3E52D2", "soft": "#EEF0FD", "strong": "#34459F",
               "accent_line": "#AEB9F0", "wash": "#F5F6FE", "press": "#E9ECFC", "select": "#EAEEFD", "select_hover": "#E2E7FC",
               "mark": "#F1F3FD", "hover_col": "#E9ECF8", "focus": "#7D8CEB", "text_select": "#CDD4FA", "stat_hover": "#C3CBF3", "toast_action": "#9DB0FF"},
    "ocean": {"label": "海蓝", "accent": "#1F78D1", "accent_hover": "#1A68B6", "soft": "#E6F1FC", "strong": "#155A9E",
              "accent_line": "#A9CDF2", "wash": "#F3F8FE", "press": "#DFEDFB", "select": "#E4F0FC", "select_hover": "#D6E7FA",
              "mark": "#F0F6FE", "hover_col": "#E2EEFB", "focus": "#5FA0E6", "text_select": "#C4DDF7", "stat_hover": "#AFCFF2", "toast_action": "#9CC8FF"},
    "rose": {"label": "樱粉", "accent": "#CC4479", "accent_hover": "#B73A6A", "soft": "#FCE9F0", "strong": "#9C2D56",
             "accent_line": "#EDB0C6", "wash": "#FEF4F8", "press": "#F9E0E9", "select": "#FAE6EE", "select_hover": "#F5D7E3",
             "mark": "#FDF2F6", "hover_col": "#F8E3EB", "focus": "#E37BA2", "text_select": "#F4C9D8", "stat_hover": "#EDB5CA", "toast_action": "#FFA8C8"},
    "graphite": {"label": "石墨", "accent": "#3A4356", "accent_hover": "#2B3242", "soft": "#ECEEF2", "strong": "#1F2530",
                 "accent_line": "#B9C0CC", "wash": "#F5F6F8", "press": "#E6E9EE", "select": "#E9ECF1", "select_hover": "#DFE3EA",
                 "mark": "#F2F4F7", "hover_col": "#E6E9EF", "focus": "#7C8698", "text_select": "#D2D7E0", "stat_hover": "#C3C9D4", "toast_action": "#C9D2E3"},
}
DARK = {
    "bg": "#12151C", "card": "#1A1F29", "line": "#283041", "ink": "#E6E9F0", "muted": "#A0A9BA", "subtle": "#808B9F",
    "body": "#C3CAD6", "track": "#262D3A", "grid": "#212835", "axis": "#2F3746", "lane": "#20262F", "tick": "#363F50",
    "field": "#323B4C", "disabled_text": "#5B6577", "disabled_bg": "#1E242F", "disabled_line": "#283041",
    "row_line": "#232A37", "row_hover": "#1F2531", "scroll": "#343D4E", "scroll_hover": "#465166",
    "seg": "#222935", "seg_hover": "#2B3342", "note_hover": "#222936", "tooltip": "#E6E9F0", "nav": "#9AA4B6",
    "check_line": "#4A5468", "toast": "#2E3647", "toast_text": "#F3F5FA", "toast_hover": "#3A4357",
    "danger": "#F07A5F", "offline": "#4A5468", "on_accent": "#FFFFFF", "shadow": (0, 0, 0), "dark": True,
    "label": "墨夜", "accent": "#7C8CFF", "accent_hover": "#6A7BF2", "soft": "#262D4A", "strong": "#B9C2FF",
    "accent_line": "#4B5694", "wash": "#20263A", "press": "#283053", "select": "#2A3255", "select_hover": "#323B63",
    "mark": "#20273D", "hover_col": "#283048", "focus": "#7C8CFF", "text_select": "#3A4580", "stat_hover": "#46508A", "toast_action": "#AEB9FF",
}
CATEGORY_LIGHT = ({"work": "#26998A", "distraction": "#E4943A", "system": "#3F88C5", "idle": "#A4AFC2", "unknown": "#8C7AD9", "ignore": "#7C8798"},
                  {"work": "#E6F5F2", "distraction": "#FDF1E3", "system": "#E6F0F9", "idle": "#EFF2F7", "unknown": "#F1EEFB", "ignore": "#EEF0F3"})
CATEGORY_DARK = ({"work": "#34B5A4", "distraction": "#F0A24E", "system": "#5EA3DD", "idle": "#8C97AB", "unknown": "#A190EE", "ignore": "#7C8798"},
                 {"work": "#173430", "distraction": "#3A2A16", "system": "#172A3A", "idle": "#262C36", "unknown": "#2A2546", "ignore": "#252A32"})
THEMES = {**{key: value["label"] for key, value in ACCENTS.items()}, "dark": DARK["label"]}

P = {}  # the active palette; filled by use()
COLORS, TINTS = {}, {}
TEXTS = {}  # category colours adjusted so text in them reaches 4.5:1 on cards (fills keep COLORS)


def contrast(a, b):
    def lum(h):
        h = h.lstrip("#"); c = [int(h[i:i+2], 16)/255 for i in (0, 2, 4)]
        c = [x/12.92 if x <= 0.03928 else ((x+0.055)/1.055)**2.4 for x in c]
        return .2126*c[0]+.7152*c[1]+.0722*c[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb)+.05)/(min(la, lb)+.05)


def readable(color, background, target=4.6):
    """Darken (on light) or lighten (on dark) `color` in small steps until text reaches `target` contrast."""
    if contrast(color, background) >= target:
        return color
    light_bg = contrast(background, "#000000") > contrast(background, "#FFFFFF")
    goal = (0, 0, 0) if light_bg else (255, 255, 255)
    rgb = [int(color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
    for step in range(1, 21):
        mixed = "#"+"".join(f"{round(c+(g-c)*step/20):02X}" for c, g in zip(rgb, goal))
        if contrast(mixed, background) >= target:
            return mixed
    return "#000000" if light_bg else "#FFFFFF"
BG = CARD = LINE = INK = MUTED = SUBTLE = ACCENT = ACCENT_SOFT = ""
THEME = "indigo"


def _mix(a, b, t):
    ra = [int(a.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]; rb = [int(b.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
    return "#"+"".join(f"{round(x+(y-x)*t):02X}" for x, y in zip(ra, rb))


def sub_colors(category, count):
    """`count` shades of a category colour for its 小类, strongest first, in both light and dark themes.

    The first is a little deeper than the category colour, the last a soft tint of it; 其他 (no
    subcategory) is drawn in the neutral idle grey instead.
    """
    base = COLORS[category]
    deep = _mix(base, "#FFFFFF" if P["dark"] else "#000000", .22)
    soft = _mix(base, P["card"], .58)
    if count <= 1: return [deep][:count]
    return [_mix(deep, soft, i/(count-1)) for i in range(count)]


def palette(name):
    if name == "dark":
        return dict(DARK), CATEGORY_DARK
    return {**LIGHT, **ACCENTS.get(name, ACCENTS["indigo"])}, CATEGORY_LIGHT


def use(name):
    """Activate a palette. Call before importing the interface modules."""
    global BG, CARD, LINE, INK, MUTED, SUBTLE, ACCENT, ACCENT_SOFT, THEME
    THEME = name if name in THEMES else "indigo"
    tokens, (colors, tints) = palette(THEME)
    P.clear(); P.update(tokens); COLORS.clear(); COLORS.update(colors); TINTS.clear(); TINTS.update(tints)
    TEXTS.clear(); TEXTS.update({key: readable(value, tokens["card"]) for key, value in colors.items()})
    BG, CARD, LINE, INK, MUTED, SUBTLE = P["bg"], P["card"], P["line"], P["ink"], P["muted"], P["subtle"]
    ACCENT, ACCENT_SOFT = P["accent"], P["soft"]
    _ICONS.clear()


APP_NAMES = {"codex.exe": "Codex", "chatgpt.exe": "Codex / ChatGPT", "claude.exe": "Claude", "code.exe": "Visual Studio Code",
             "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "winword.exe": "Word", "excel.exe": "Excel",
             "powerpnt.exe": "PowerPoint", "zotero.exe": "Zotero", "python.exe": "Python", "专注时间助手.exe": "专注时间助手", "explorer.exe": "文件资源管理器",
             "firefox.exe": "Firefox", "windowsterminal.exe": "Windows Terminal", "pythonw.exe": "Python", "matlab.exe": "MATLAB", "wechat.exe": "微信", "weixin.exe": "微信", "qq.exe": "QQ"}


# Display names for common websites; a domain also covers its subdomains (the longest match wins).
SITE_NAMES = {"leetcode.cn": "力扣", "leetcode.com": "LeetCode", "runoob.com": "菜鸟教程", "cnblogs.com": "博客园", "juejin.cn": "稀土掘金", "kdocs.cn": "金山文档", "shimo.im": "石墨文档", "docs.qq.com": "腾讯文档", "wanfangdata.com.cn": "万方数据", "cqvip.com": "维普", "semanticscholar.org": "Semantic Scholar", "science.org": "Science", "mdpi.com": "MDPI", "ncbi.nlm.nih.gov": "PubMed", "x-mol.com": "X-MOL", "kimi.moonshot.cn": "Kimi", "tongyi.aliyun.com": "通义", "yuanbao.tencent.com": "腾讯元宝", "yiyan.baidu.com": "文心一言", "gemini.google.com": "Gemini", "chat.openai.com": "ChatGPT", "xuetangx.com": "学堂在线", "yuketang.cn": "雨课堂", "zhihuishu.com": "智慧树", "coursera.org": "Coursera", "tiktok.com": "TikTok", "mgtv.com": "芒果TV", "netflix.com": "Netflix", "4399.com": "4399 小游戏", "7k7k.com": "7k7k 小游戏", "store.steampowered.com": "Steam 商店", "weibo.cn": "微博", "yangkeduo.com": "拼多多", "goofish.com": "闲鱼", "dewu.com": "得物", "vip.com": "唯品会", "amazon.com": "亚马逊", "amazon.cn": "亚马逊", "toutiao.com": "今日头条", "sina.com.cn": "新浪", "sohu.com": "搜狐", "thepaper.cn": "澎湃新闻", "hupu.com": "虎扑", "ifeng.com": "凤凰网", "36kr.com": "36氪", "news.163.com": "网易新闻", "channels.weixin.qq.com": "视频号", "figma.com": "Figma",
              "douyin.com": "抖音", "iesdouyin.com": "抖音", "xiaohongshu.com": "小红书", "xhslink.com": "小红书",
              "bilibili.com": "B站", "b23.tv": "B站", "kuaishou.com": "快手", "ixigua.com": "西瓜视频",
              "weibo.com": "微博", "zhihu.com": "知乎", "douban.com": "豆瓣", "tieba.baidu.com": "百度贴吧",
              "baidu.com": "百度", "qq.com": "腾讯", "v.qq.com": "腾讯视频", "mail.qq.com": "QQ 邮箱", "iqiyi.com": "爱奇艺", "youku.com": "优酷",
              "taobao.com": "淘宝", "tmall.com": "天猫", "jd.com": "京东", "pinduoduo.com": "拼多多", "csdn.net": "CSDN",
              "cnki.net": "中国知网", "github.com": "GitHub", "gitee.com": "Gitee", "stackoverflow.com": "Stack Overflow",
              "google.com": "Google", "scholar.google.com": "Google 学术", "mail.google.com": "Gmail", "youtube.com": "YouTube",
              "chatgpt.com": "ChatGPT", "openai.com": "OpenAI", "claude.ai": "Claude", "deepseek.com": "DeepSeek", "kimi.com": "Kimi",
              "doubao.com": "豆包", "notion.so": "Notion", "feishu.cn": "飞书", "yuque.com": "语雀", "wikipedia.org": "维基百科",
              "x.com": "X", "twitter.com": "X", "reddit.com": "Reddit", "sciencedirect.com": "ScienceDirect",
              "springer.com": "Springer", "nature.com": "Nature", "ieee.org": "IEEE", "overleaf.com": "Overleaf", "arxiv.org": "arXiv",
              "researchgate.net": "ResearchGate", "webofscience.com": "Web of Science", "wiley.com": "Wiley", "acs.org": "ACS",
              "translate.google.com": "Google 翻译", "fanyi.baidu.com": "百度翻译", "deepl.com": "DeepL", "chaoxing.com": "超星学习通",
              "icourse163.org": "中国大学 MOOC", "163.com": "网易", "music.163.com": "网易云音乐", "huya.com": "虎牙直播", "douyu.com": "斗鱼直播"}
SITE_ALIASES = {}  # the user's own names, set from settings ("site_names"); they win over SITE_NAMES


def _site_lookup(host, names):
    """The name of the longest known domain that is `host` or one of its parents. Walks the host's
    own suffixes (a few dict lookups) instead of testing every known domain against it."""
    host = host.lower()
    while host:
        if host in names: return names[host]
        dot = host.find(".")
        if dot < 0: return ""
        host = host[dot+1:]
    return ""


def site_name(host):
    """The website's name for a domain: the user's own name, then a known one, else the domain."""
    if not host: return ""
    return _site_lookup(host, SITE_ALIASES) or _site_lookup(host, SITE_NAMES) or host


def site_default(host):
    return _site_lookup(host, SITE_NAMES) if host else ""


def key_name(key):
    """Name for a rule / limit key: a program (".exe"), a system surface or a website domain."""
    if key.endswith(".exe") or key.startswith("界面:") or "." not in key: return app_name(key)
    return site_name(key)


def label(app, host):
    """One row's display name: the website if there is one, else the program."""
    return site_name(host) if host else app_name(app)


APP_ALIASES = {}  # the user's own program names, set from settings ("app_names")


def app_name(app):
    return APP_ALIASES.get(app) or app_default(app)


def app_default(app):
    """The program's name without the user's own: built-in, then the file's description."""
    if app.startswith("界面:"):  # counted desktop / taskbar … time
        return app[3:]+"（系统界面）"
    if app in APP_NAMES:
        return APP_NAMES[app]
    if app.endswith(".exe"):  # the program's own display name, e.g. windowsterminal.exe → Windows Terminal
        import windows
        path = windows.paths.get(app)
        if path:
            return windows.describe(path) or app
    return app


def style_sheet():
    p = P
    return f"""
QMainWindow, QWidget#canvas {{ background:{p['bg']}; }}
QWidget {{ font-family:'{UI_FONT}'; font-size:14px; color:{p['ink']}; }}
QDialog {{ background:{p['card']}; }}
QFrame#sidebar {{ background:{p['card']}; border-right:1px solid {p['line']}; }}
QFrame#card {{ background:{p['card']}; border:1px solid {p['line']}; border-radius:12px; }}
QFrame#stat {{ background:{p['card']}; border:1px solid {p['line']}; border-radius:12px; }}
QFrame#stat:hover {{ border-color:{p['stat_hover']}; }}
QFrame#note {{ background:{p['bg']}; border:0; border-radius:10px; }}
QFrame#note:hover {{ background:{p['note_hover']}; }}
QFrame#kpi {{ background:{p['card']}; border:1px solid {p['line']}; border-radius:12px; }}
QLabel[role='kpi'] {{ font-family:'{NUM_FONT}','{UI_FONT}'; font-size:20px; font-weight:600; }}
QFrame#segmented {{ background:{p['seg']}; border:0; border-radius:9px; }}
QPushButton[role='segment'] {{ background:transparent; border:0; border-radius:7px; padding:5px 12px; color:{p['muted']}; }}
QPushButton[role='segment']:hover {{ color:{p['ink']}; background:{p['seg_hover']}; }}
QPushButton[role='segment']:checked {{ background:{p['card']}; color:{p['ink']}; font-weight:600; }}
QPushButton[role='icon'] {{ padding:7px 8px; }}
QPushButton[role='swatch'] {{ border-radius:10px; padding:8px 10px; text-align:left; }}
QPushButton[role='swatch']:checked {{ border:2px solid {p['accent']}; background:{p['soft']}; font-weight:600; }}
QFrame#toast {{ background:{p['toast']}; border-radius:10px; }}
QLabel#toastText {{ color:{p['toast_text']}; font-size:13px; }}
QPushButton#toastAction {{ background:transparent; border:0; color:{p['toast_action']}; font-weight:600; padding:4px 8px; }}
QPushButton#toastAction:hover {{ color:white; background:{p['toast_hover']}; border-radius:6px; }}
QLabel {{ background:transparent; border:none; }}
QLabel[role='muted'] {{ color:{p['muted']}; font-size:13px; }}
QLabel#insights {{ color:{p['body']}; font-size:13px; }}
QLabel[role='small'] {{ color:{p['subtle']}; font-size:12px; }}
QLabel[role='title'] {{ font-size:24px; font-weight:700; }}
QLabel[role='heading'] {{ font-size:15px; font-weight:600; }}
QLabel[role='value'] {{ font-family:'{NUM_FONT}','{UI_FONT}'; font-size:28px; font-weight:600; }}
QLabel[role='now'] {{ font-size:20px; font-weight:600; }}
QLabel[role='pill'] {{ border-radius:10px; padding:2px 10px; font-size:12px; font-weight:600; }}
QPushButton {{ background:{p['card']}; border:1px solid {p['field']}; border-radius:8px; padding:7px 14px; color:{p['ink']}; }}
QPushButton:hover {{ border-color:{p['accent_line']}; background:{p['wash']}; }}
QPushButton:pressed {{ background:{p['press']}; }}
QPushButton[kbfocus='true'] {{ border:2px solid {p['focus']}; padding:6px 13px; }}
QPushButton[role='nav'][kbfocus='true'] {{ border:2px solid {p['focus']}; padding:8px 12px; }}
QPushButton[role='segment'][kbfocus='true'] {{ border:2px solid {p['focus']}; padding:3px 10px; }}
QPushButton:disabled {{ color:{p['disabled_text']}; background:{p['disabled_bg']}; border-color:{p['disabled_line']}; }}
QPushButton[role='primary'] {{ background:{p['accent']}; color:{p['on_accent']}; border-color:{p['accent']}; }}
QPushButton[role='primary']:hover {{ background:{p['accent_hover']}; border-color:{p['accent_hover']}; }}
QPushButton[role='primary'][kbfocus='true'] {{ border:2px solid {p['ink']}; padding:6px 13px; }}
QPushButton[role='primary']:disabled {{ color:{p['disabled_text']}; background:{p['disabled_bg']}; border-color:{p['disabled_line']}; }}
QPushButton[role='quiet'] {{ border:0; background:transparent; padding:3px 8px; border-radius:6px; color:{p['subtle']}; font-size:12px; }}
QPushButton[role='quiet']:hover {{ background:{p['note_hover']}; color:{p['muted']}; }}
QPushButton[role='link'] {{ border:0; background:transparent; padding:2px 0; color:{p['accent']}; }}
QPushButton[role='nav'] {{ text-align:left; padding:10px 14px; border:0; border-radius:8px; background:transparent; color:{p['nav']}; }}
QPushButton[role='nav']:hover {{ background:{p['row_hover']}; color:{p['strong']}; }}
QPushButton[role='nav']:checked {{ background:{p['soft']}; color:{p['accent']}; font-weight:600; }}
QLineEdit,QComboBox,QDateEdit,QSpinBox {{ background:{p['card']}; color:{p['ink']}; border:1px solid {p['field']}; border-radius:8px; padding:6px 10px; min-height:20px; selection-background-color:{p['text_select']}; selection-color:{p['ink']}; }}
QSpinBox {{ padding-right:26px; }}
QSpinBox::up-button,QSpinBox::down-button {{ subcontrol-origin:border; width:24px; border:0; background:transparent; }}
QSpinBox::up-button {{ subcontrol-position:top right; border-top-right-radius:8px; margin:1px 1px 0 0; }}
QSpinBox::down-button {{ subcontrol-position:bottom right; border-bottom-right-radius:8px; margin:0 1px 1px 0; }}
QSpinBox::up-button:hover,QSpinBox::down-button:hover {{ background:{p['wash']}; }}
QSpinBox::up-button:pressed,QSpinBox::down-button:pressed {{ background:{p['press']}; }}
QLineEdit:focus,QComboBox:focus,QDateEdit:focus,QSpinBox:focus {{ border:1px solid {p['focus']}; }}
QLineEdit[readOnly='true'] {{ background:{p['bg']}; color:{p['muted']}; }}
QComboBox::drop-down,QDateEdit::drop-down {{ border:0; width:26px; subcontrol-origin:padding; subcontrol-position:center right; }}
QDateEdit {{ padding-right:30px; }}
QComboBox QAbstractItemView {{ background:{p['card']}; color:{p['ink']}; border:1px solid {p['line']}; selection-background-color:{p['soft']}; selection-color:{p['strong']}; outline:0; }}
QTableWidget {{ background:{p['card']}; color:{p['ink']}; border:0; selection-background-color:{p['select']}; selection-color:{p['ink']}; outline:0; }}
QTableWidget::item {{ padding:0 10px; border-bottom:1px solid {p['row_line']}; }}
QTableWidget::item:selected {{ background:{p['select']}; color:{p['ink']}; }}
QTableWidget::item:hover {{ background:{p['row_hover']}; }}
QTableWidget::item:selected:hover {{ background:{p['select_hover']}; }}
QHeaderView {{ background:{p['card']}; }}
QHeaderView::section {{ background:{p['card']}; color:{p['subtle']}; padding:8px 10px; border:0; border-bottom:1px solid {p['line']}; font-size:12px; }}
QTableCornerButton::section {{ background:{p['card']}; border:0; }}
QScrollArea {{ background:transparent; border:0; }}
QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{p['scroll']}; border-radius:3px; min-height:32px; }}
QScrollBar::handle:vertical:hover {{ background:{p['scroll_hover']}; }}
QScrollBar::add-line,QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page,QScrollBar::sub-page {{ background:transparent; }}
QScrollBar:horizontal {{ height:8px; background:transparent; }}
QScrollBar::handle:horizontal {{ background:{p['scroll']}; border-radius:3px; }}
QToolTip {{ color:{p['tooltip']}; background:{p['card']}; border:1px solid {p['field']}; padding:7px 9px; font-size:13px; }}
QCheckBox {{ spacing:7px; color:{p['muted']}; font-size:13px; }}
QCheckBox::indicator {{ width:15px; height:15px; }}
QCheckBox::indicator:unchecked {{ border:1px solid {p['check_line']}; border-radius:4px; background:{p['card']}; }}
QMenu {{ background:{p['card']}; color:{p['ink']}; border:1px solid {p['line']}; padding:4px; }}
QMenu::item {{ padding:7px 22px 7px 14px; border-radius:6px; }}
QMenu::item:selected {{ background:{p['soft']}; color:{p['strong']}; }}
QMenu::separator {{ height:1px; background:{p['line']}; margin:4px 8px; }}
QMessageBox {{ background:{p['card']}; }}
QCalendarWidget QWidget {{ background:{p['card']}; color:{p['ink']}; alternate-background-color:{p['bg']}; }}
QCalendarWidget QToolButton {{ background:transparent; color:{p['ink']}; border:0; padding:4px 8px; }}
QCalendarWidget QAbstractItemView:enabled {{ selection-background-color:{p['accent']}; selection-color:white; }}
"""


def _draw(p, kind, color):
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    fill = QColor(color)
    if kind in ("clock", "logo"):
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(12, 7, 12, 12); p.drawLine(12, 12, 16, 14)
    elif kind == "overview":
        for x, y in [(3, 3), (14, 3), (3, 14), (14, 14)]: p.drawRoundedRect(QRectF(x, y, 7, 7), 1.5, 1.5)
    elif kind == "chart":
        p.drawLine(3, 20, 21, 20)
        for x, h in [(6, 7), (12, 13), (18, 17)]: p.drawLine(x, 18, x, 20-h)
    elif kind == "list":
        for y in (6, 12, 18): p.drawLine(8, y, 21, y); p.drawEllipse(QRectF(2, y-1, 2, 2))
    elif kind == "settings":
        for y, x in ((6, 8), (12, 16), (18, 10)):
            p.drawLine(3, y, 21, y); p.setBrush(QColor(P.get("card", "white"))); p.drawEllipse(QRectF(x-2, y-2, 4, 4)); p.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == "browser":
        p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2); p.drawLine(3, 9, 21, 9); p.drawLine(7, 6, 8, 6)
    elif kind == "work":
        p.drawRoundedRect(QRectF(3, 7, 18, 14), 2, 2); p.drawRoundedRect(QRectF(8, 3, 8, 4), 1, 1); p.drawLine(3, 12, 21, 12)
    elif kind == "distraction":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(9, 8, 16, 12); p.drawLine(16, 12, 9, 16); p.drawLine(9, 16, 9, 8)
    elif kind == "idle":
        p.drawArc(QRectF(4, 4, 16, 16), 60*16, 260*16); p.drawLine(15, 4, 20, 4); p.drawLine(20, 4, 15, 9); p.drawLine(15, 9, 20, 9)
    elif kind == "unknown":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawArc(QRectF(9, 7, 6, 6), 160*16, -250*16); p.drawLine(12, 13, 12, 14.5); p.drawPoint(12, 17)
    elif kind == "monitor":  # system surfaces: desktop, taskbar …
        p.drawRoundedRect(QRectF(3, 4, 18, 12), 2, 2); p.drawLine(12, 16, 12, 20); p.drawLine(8, 20, 16, 20)
    elif kind == "bulb":  # light bulb: findings
        p.drawArc(QRectF(6, 3, 12, 12), -40*16, 260*16); p.drawLine(QPointF(8.3, 13), QPointF(9.5, 17)); p.drawLine(QPointF(15.7, 13), QPointF(14.5, 17))
        p.drawLine(9, 17, 15, 17); p.drawLine(10, 20, 14, 20)
    elif kind == "system":  # gear
        import math
        p.drawEllipse(QRectF(8.5, 8.5, 7, 7)); p.drawEllipse(QRectF(5, 5, 14, 14))
        for step in range(8):
            a = step*math.pi/4; c, s = math.cos(a), math.sin(a)
            p.drawLine(QPointF(12+7*c, 12+7*s), QPointF(12+9.5*c, 12+9.5*s))
    elif kind == "ignore":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(6.5, 17.5, 17.5, 6.5)
    elif kind == "pause":
        p.drawLine(9, 5, 9, 19); p.drawLine(15, 5, 15, 19)
    elif kind == "play":
        p.drawLine(8, 5, 18, 12); p.drawLine(18, 12, 8, 19); p.drawLine(8, 19, 8, 5)
    elif kind == "focus":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawEllipse(QRectF(7.5, 7.5, 9, 9)); p.drawPoint(12, 12)
    elif kind == "left":
        p.drawLine(15, 5, 8, 12); p.drawLine(8, 12, 15, 19)
    elif kind == "right":
        p.drawLine(9, 5, 16, 12); p.drawLine(16, 12, 9, 19)
    elif kind == "undo":
        p.drawArc(QRectF(5, 6, 15, 13), 90*16, -230*16); p.drawLine(12, 6, 8, 3); p.drawLine(12, 6, 8, 9)
    elif kind == "export":
        p.drawLine(12, 3, 12, 15); p.drawLine(7, 8, 12, 3); p.drawLine(17, 8, 12, 3); p.drawLine(4, 15, 4, 20); p.drawLine(4, 20, 20, 20); p.drawLine(20, 20, 20, 15)
    elif kind == "pie":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(12, 12, 12, 3); p.drawLine(12, 12, 19.5, 16.5)
    elif kind == "rank":
        p.drawLine(4, 20, 20, 20); p.drawRoundedRect(QRectF(4, 12, 4, 8), 1, 1); p.drawRoundedRect(QRectF(10, 5, 4, 15), 1, 1); p.drawRoundedRect(QRectF(16, 9, 4, 11), 1, 1)
    elif kind == "calendar":
        p.drawRoundedRect(QRectF(3, 5, 18, 16), 2.5, 2.5); p.drawLine(3, 10, 21, 10); p.drawLine(8, 3, 8, 7); p.drawLine(16, 3, 16, 7)
        p.drawPoint(8, 14); p.drawPoint(12, 14); p.drawPoint(16, 14); p.drawPoint(8, 17.5); p.drawPoint(12, 17.5)
    elif kind == "bell":
        path = QPainterPath(QPointF(6, 17)); path.lineTo(6, 11); path.cubicTo(6, 7, 8.5, 4.5, 12, 4.5); path.cubicTo(15.5, 4.5, 18, 7, 18, 11)
        path.lineTo(18, 17); path.closeSubpath(); p.drawPath(path); p.drawLine(4, 17, 20, 17); p.drawArc(QRectF(10, 17, 4, 4), 180*16, 180*16)
    elif kind == "target":
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawEllipse(QRectF(7, 7, 10, 10)); p.setBrush(fill); p.drawEllipse(QRectF(10.5, 10.5, 3, 3))
    elif kind == "trophy":
        p.drawLine(8, 4, 16, 4); p.drawArc(QRectF(7, -2, 10, 14), 180*16, 180*16); p.drawLine(7, 5, 7, 5); p.drawLine(12, 12, 12, 16)
        p.drawLine(8, 20, 16, 20); p.drawLine(9, 16, 15, 16); p.drawArc(QRectF(3, 5, 5, 5), 90*16, 180*16); p.drawArc(QRectF(16, 5, 5, 5), 90*16, -180*16)
    elif kind == "coffee":
        p.drawRoundedRect(QRectF(4, 9, 12, 10), 2, 2); p.drawArc(QRectF(13, 10, 6, 6), 90*16, -180*16)
        p.drawLine(7, 4, 7, 6); p.drawLine(10, 3, 10, 6); p.drawLine(13, 4, 13, 6); p.drawLine(3, 21, 17, 21)
    elif kind == "palette":
        path = QPainterPath(QPointF(12, 3)); path.cubicTo(5, 3, 3, 9, 3, 12); path.cubicTo(3, 17, 7, 21, 12, 21); path.cubicTo(14, 21, 14, 18, 13, 17)
        path.cubicTo(12, 15, 14, 14, 16, 14); path.cubicTo(19, 14, 21, 12.5, 21, 10); path.cubicTo(21, 6, 17, 3, 12, 3); p.drawPath(path)
        p.setBrush(fill)
        for x, y in ((8, 9), (12, 7), (16, 9), (7.5, 13.5)): p.drawEllipse(QRectF(x-1.1, y-1.1, 2.2, 2.2))
    elif kind == "report":
        p.drawRoundedRect(QRectF(5, 3, 14, 18), 2, 2); p.drawLine(9, 16, 9, 13); p.drawLine(12, 16, 12, 9); p.drawLine(15, 16, 15, 11); p.drawLine(8, 7, 11, 7)
    elif kind == "shield":
        path = QPainterPath(QPointF(12, 3)); path.lineTo(19, 6); path.lineTo(19, 12); path.cubicTo(19, 16, 16, 19, 12, 21)
        path.cubicTo(8, 19, 5, 16, 5, 12); path.lineTo(5, 6); path.closeSubpath(); p.drawPath(path); p.drawLine(9, 12, 11, 14); p.drawLine(11, 14, 15, 10)
    elif kind == "rules":
        p.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2); p.drawLine(8, 8, 16, 8); p.drawLine(8, 12, 16, 12); p.drawLine(8, 16, 13, 16)
    elif kind == "timeline":
        p.drawLine(3, 18, 21, 18)
        for x, h in [(4, 6), (7, 10), (10, 4), (13, 12), (16, 8), (19, 5)]: p.drawLine(x, 16, x, 16-h)
    elif kind == "moon":
        path = QPainterPath(QPointF(16, 4)); path.cubicTo(9, 4, 6, 9, 6, 13); path.cubicTo(6, 18, 10, 21, 14, 21); path.cubicTo(17, 21, 19.5, 19.5, 21, 17)
        path.cubicTo(15, 18, 11, 14, 12, 9); path.cubicTo(12.5, 6.5, 14, 5, 16, 4); p.drawPath(path)
    elif kind == "flame":
        path = QPainterPath(QPointF(12, 3)); path.cubicTo(14, 7, 18, 9, 18, 14); path.cubicTo(18, 18, 15, 21, 12, 21)
        path.cubicTo(9, 21, 6, 18, 6, 14.5); path.cubicTo(6, 12, 7.5, 10.5, 9, 9.5); path.cubicTo(9, 11.5, 10, 13, 11, 13)
        path.cubicTo(10.5, 9, 11, 6, 12, 3); p.drawPath(path)
    elif kind == "folder":
        path = QPainterPath(QPointF(3, 7)); path.lineTo(3, 18); path.cubicTo(3, 19.5, 4, 20, 5, 20); path.lineTo(19, 20)
        path.cubicTo(20.5, 20, 21, 19, 21, 18); path.lineTo(21, 9); path.cubicTo(21, 8, 20, 7.5, 19, 7.5); path.lineTo(11.5, 7.5)
        path.lineTo(9.5, 5); path.lineTo(5, 5); path.cubicTo(4, 5, 3, 5.8, 3, 7); p.drawPath(path)
    elif kind == "restart":
        p.drawArc(QRectF(4, 4, 16, 16), 100*16, 300*16); p.drawLine(11, 4, 14.5, 2); p.drawLine(11, 4, 14, 7)
    elif kind == "search":
        p.drawEllipse(QRectF(4, 4, 12, 12)); p.drawLine(14.5, 14.5, 20, 20)
    elif kind == "pen":  # time entered by hand
        path = QPainterPath(QPointF(4, 20)); path.lineTo(5, 15.5); path.lineTo(15.5, 5); path.lineTo(19, 8.5); path.lineTo(8.5, 19); path.closeSubpath()
        p.drawPath(path); p.drawLine(QPointF(13.5, 7), QPointF(17, 10.5))
    elif kind == "close":
        p.drawLine(7, 7, 17, 17); p.drawLine(17, 7, 7, 17)
    elif kind == "tags":  # 小类
        path = QPainterPath(QPointF(4, 4)); path.lineTo(11, 4); path.lineTo(20, 13); path.lineTo(13, 20); path.lineTo(4, 11); path.closeSubpath()
        p.drawPath(path); p.drawEllipse(QRectF(7, 7, 2.6, 2.6))
    elif kind == "plus":
        p.drawLine(12, 5, 12, 19); p.drawLine(5, 12, 19, 12)
    elif kind == "down":
        p.drawLine(6, 9, 12, 15); p.drawLine(12, 15, 18, 9)
    elif kind == "import":  # arrow into a tray: bring a backup back
        p.drawLine(12, 3, 12, 14); p.drawLine(7, 9, 12, 14); p.drawLine(17, 9, 12, 14); p.drawLine(4, 15, 4, 20); p.drawLine(4, 20, 20, 20); p.drawLine(20, 20, 20, 15)
    else:
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(8, 12, 11, 15); p.drawLine(11, 15, 17, 8)


_ICONS = {}
_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def icon(kind, color=None, size=20):
    """A QIcon carrying one vector rendering per common scale factor.

    Qt picks the best rendering for whichever monitor the widget is on, so the
    icon stays sharp after moving the window between screens.
    """
    color = color or ACCENT
    key = (kind, color, size)
    if key not in _ICONS:
        result = QIcon()
        for scale in _SCALES:
            pixels = round(size*scale)
            pix = QPixmap(pixels, pixels); pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.scale(pixels/24, pixels/24)
            _draw(p, kind, color); p.end()
            pix.setDevicePixelRatio(scale)
            result.addPixmap(pix)
        _ICONS[key] = result
    return _ICONS[key]


def font(px, weight=QFont.Weight.Normal, family=UI_FONT):
    """family "num": the numeral face chosen in setup(), with the UI face for Chinese characters
    (Segoe UI Variable has no CJK glyphs; without the second family Windows picks an unrelated one)."""
    f = QFont()
    f.setFamilies([NUM_FONT, UI_FONT] if family == "num" else [family])
    f.setPixelSize(px); f.setWeight(weight)
    return f


def _assets(cache):
    """Check mark and drop-down arrow in the palette's colours, as SVG files the style sheet can load."""
    assets = Path(__file__).parent/"assets"
    if cache is None:
        return (assets/"check.svg").as_posix(), (assets/"chevron.svg").as_posix()
    cache.mkdir(parents=True, exist_ok=True)
    check, arrow = cache/f"check_{THEME}.svg", cache/f"chevron_{THEME}.svg"
    for name, path, colour in (("up", "m4 10 4-4 4 4", P["muted"]), ("down", "m4 6 4 4 4-4", P["muted"]),
                               ("up_off", "m4 10 4-4 4 4", P["disabled_text"]), ("down_off", "m4 6 4 4 4-4", P["disabled_text"])):
        (cache/f"spin_{name}_{THEME}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="{path}" fill="none" stroke="{colour}" '
            'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
    check.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18"><rect x=".5" y=".5" width="17" height="17" rx="4" fill="{P["accent"]}"/>'
                     '<path d="m4.5 9 3 3 6-6" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
    arrow.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" fill="none" stroke="{P["muted"]}" '
                     'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
    return check.as_posix(), arrow.as_posix()


def setup(application, cache=None):
    global NUM_FONT
    from PySide6.QtGui import QFontDatabase
    # Segoe UI Variable (Windows 11) has crisper, tabular numerals; Windows 10 keeps Segoe UI.
    NUM_FONT = "Segoe UI Variable Display" if "Segoe UI Variable Display" in QFontDatabase.families() else "Segoe UI"
    base = QFont(UI_FONT); base.setPointSizeF(10.5); application.setFont(base)  # 14 px at 96 dpi; style sheet sets px
    check, arrow = _assets(cache)
    extras = (f"QCheckBox::indicator:checked {{ image:url('{check}'); }}"
              f" QComboBox::down-arrow,QDateEdit::down-arrow {{ image:url('{arrow}'); width:12px; height:12px; }}")
    if cache is not None:
        spin = lambda name: (cache/f"spin_{name}_{THEME}.svg").as_posix()
        extras += (f" QSpinBox::up-arrow {{ image:url('{spin('up')}'); width:11px; height:11px; }}"
                   f" QSpinBox::down-arrow {{ image:url('{spin('down')}'); width:11px; height:11px; }}"
                   f" QSpinBox::up-arrow:off,QSpinBox::up-arrow:disabled {{ image:url('{spin('up_off')}'); }}"
                   f" QSpinBox::down-arrow:off,QSpinBox::down-arrow:disabled {{ image:url('{spin('down_off')}'); }}")
    application.setStyleSheet(style_sheet()+extras)
    application.setWindowIcon(icon("logo", ACCENT, 48))
    # Roll / fade animations run a timer per frame; plain show and hide are cheaper.
    for effect in (Qt.UIEffect.UI_AnimateTooltip, Qt.UIEffect.UI_FadeTooltip, Qt.UIEffect.UI_AnimateMenu,
                   Qt.UIEffect.UI_FadeMenu, Qt.UIEffect.UI_AnimateCombo):
        application.setEffectEnabled(effect, False)


use("indigo")
