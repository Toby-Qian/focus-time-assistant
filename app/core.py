"""Classification and durable, local-only accounting; no platform dependencies."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import subcats

# "system" (系统工具) is neutral: recorded, but left out of the focus ratio like 离开.
CATEGORIES = {"work": "工作", "distraction": "摸鱼", "system": "系统工具", "idle": "离开", "unknown": "待分类"}
EXPORT_PARTS = {"daily": "每日汇总", "apps": "应用与网站排行", "hours": "一天中的时段分布", "sessions": "专注记录", "details": "每日应用明细"}
OWN_WINDOW_MODES = {"idle": "记为离开", "ignore": "不计入", "work": "记为工作", "system": "记为系统工具", "unknown": "记为待分类",
                    "skip": "略过，按下面的程序计"}
# "ignore" is a rule target, not a recorded category: matching time is never stored.
IGNORE = "ignore"
LABELS = {**CATEGORIES, IGNORE: "不计入"}
# 离开 is a category like the others: rules and corrections may use it (e.g. a meeting app).
RULE_TARGETS = ("work", "distraction", "system", "idle", "unknown", IGNORE)
# Desktop, taskbar, Start menu, minimised / hidden windows …: not counted unless the user sets a rule.
SURFACES = ("桌面", "任务栏", "托盘区", "任务栏预览", "任务切换", "开始菜单", "搜索", "系统面板", "输入法面板", "截图工具栏",
            "已最小化", "隐藏窗口", "其他桌面", "锁屏", "无前台窗口")
SURFACE_TARGETS = ("work", "distraction", "system", "idle", "unknown", IGNORE)
SURFACE_PREFIX = "界面:"
MANUAL_MARK = "（补记）"  # ends the stored name of time the user entered or re-labelled by hand
AWAY_LABEL = "离开期间"+MANUAL_MARK  # stored app name of away time the user re-labelled (it has no program of its own)
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "vivaldi.exe", "opera.exe", "360chrome.exe", "qqbrowser.exe"}
SITES = {"douyin.com": "抖音", "iesdouyin.com": "抖音", "xiaohongshu.com": "小红书", "xhslink.com": "小红书", "bilibili.com": "B站", "b23.tv": "B站"}
DEFAULTS = {
    "idle_seconds": 300, "reminder_seconds": 120,
    "work_apps": ["codex.exe", "chatgpt.exe", "claude.exe", "code.exe", "winword.exe", "excel.exe", "powerpnt.exe", "zotero.exe", "matlab.exe", "acad.exe", "solidworks.exe", "origin.exe", "origin64.exe", "pycharm64.exe", "devenv.exe"],
    "distraction_apps": ["douyin.exe", "bilibili.exe"],
    # File manager, task manager, settings and similar housekeeping windows. The desktop and the
    # taskbar (also explorer.exe) are background windows and stay uncounted.
    "system_apps": ["explorer.exe", "taskmgr.exe", "systemsettings.exe", "control.exe", "mmc.exe", "regedit.exe",
                    "resmon.exe", "perfmon.exe", "msconfig.exe", "everything.exe", "7zfm.exe", "winrar.exe", "bandizip.exe",
                    "snippingtool.exe", "screenclippinghost.exe", "rundll32.exe"],
    "app_rules": {}, "host_rules": {}, "surface_rules": {}, "remember_rule": True,
    "window": "", "page": 0,  # last window geometry (hex) and page, restored on start
    "rhythm_view": 0,  # overview chart: 0 = by hour, 1 = minute timeline
    "own_window": "idle",  # this program's own window: a category, or "skip" to use the window below it
    # Appearance and analysis options
    "theme": "indigo", "focus_includes_unknown": True, "hour_range": "elapsed",
    "export_format": "html", "export_parts": ["daily", "apps", "hours", "sessions"],
    # Reminders, goals and focus sessions; every number is minutes, 0 switches the feature off.
    "remind_distraction": True, "reminder_sound": True, "learning_minutes": 30,
    "tray_hint": False,  # system notification "still recording" when the window is closed to the tray
    "daily_summary": "off",  # "off" or the HH:MM at which today's summary is shown once
    "return_prompt_minutes": 15,  # back after at least this long away: ask whether it was work, distraction or away (0 = never)
    "app_limits": {},  # {"x.exe" or "domain": daily minutes} — one reminder when reached
    "app_names": {},  # {"x.exe": "display name"} — the user's own names for programs
    "site_names": {},  # {"domain": "display name"} — the user's own names for websites (subdomains included)
    "overview_away": True,  # overview shares, ring and hourly bars include away time
    # 小类 of 工作 / 摸鱼 (see subcats.py): the list, the user's program / website choices, daily limits by id.
    "subcategories": subcats.default_list(), "sub_rules": {}, "sub_limits": {},
    "work_goal_minutes": 360, "distraction_limit_minutes": 30, "focus_minutes": 25, "rest_after_minutes": 0,
    # Focus sessions: a short break after each completed session (0 = none), the floating timer,
    # recently used task names (newest first) and where the floating timer was left.
    "break_minutes": 5, "focus_float": True, "recent_tasks": [], "float_pos": "",
}
STREAK_FALLBACK_SECONDS = 30*60  # without a daily goal, a day "counts" after 30 minutes of work
# (key, lowest, highest) for user-adjustable minute settings; out-of-range values fall back to the default.
MINUTE_SETTINGS = [("learning_minutes", 5, 240), ("work_goal_minutes", 0, 960), ("distraction_limit_minutes", 0, 600),
                   ("focus_minutes", 5, 180), ("rest_after_minutes", 0, 240), ("break_minutes", 0, 30)]


def hostname(value: str) -> str:
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def match_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def site_for(host: str) -> str:
    return next((name for domain, name in SITES.items() if match_host(host, domain)), "")


def load_config(path: Path) -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        config.update({key: saved[key] for key in DEFAULTS if key in saved})
        config["token"] = saved.get("token") or secrets.token_urlsafe(32)
    else:
        config["token"] = secrets.token_urlsafe(32)
    validate_config(config)
    save_config(path, config)
    return config


def validate_config(config: dict):
    for key, low, high in [("idle_seconds", 60, 7200), ("reminder_seconds", 15, 3600)]:
        if not isinstance(config[key], int) or not low <= config[key] <= high:
            raise ValueError(f"{key} 应在 {low}–{high} 秒之间")
    for key in ["app_rules", "host_rules"]:
        if not isinstance(config[key], dict) or any(v not in RULE_TARGETS for v in config[key].values()):
            raise ValueError(f"{key} 的分类无效")
    rules = config.get("surface_rules")
    if not isinstance(rules, dict) or any(k not in SURFACES or v not in SURFACE_TARGETS for k, v in rules.items()):
        config["surface_rules"] = {}
    if not isinstance(config["remember_rule"], bool):
        raise ValueError("remember_rule 应为 true / false")
    if not isinstance(config["window"], str) or len(config["window"]) > 4096:
        config["window"] = ""
    if not isinstance(config["page"], int) or not 0 <= config["page"] <= 4:
        config["page"] = 0
    if config.get("rhythm_view") not in (0, 1) or isinstance(config.get("rhythm_view"), bool):
        config["rhythm_view"] = 0
    if config.get("own_window") not in OWN_WINDOW_MODES:
        config["own_window"] = "idle"
    if config.get("theme") not in ("indigo", "ocean", "rose", "graphite", "dark"):
        config["theme"] = "indigo"
    if not isinstance(config.get("focus_includes_unknown"), bool):
        config["focus_includes_unknown"] = True
    if config.get("hour_range") not in ("elapsed", "full"):
        config["hour_range"] = "elapsed"
    if config.get("export_format") not in ("html", "csv", "both"):
        config["export_format"] = "html"
    parts = config.get("export_parts")
    if not isinstance(parts, list) or any(part not in EXPORT_PARTS for part in parts):
        config["export_parts"] = list(DEFAULTS["export_parts"])
    if config.get("return_prompt_minutes") not in (0, 10, 15, 30, 60) or isinstance(config.get("return_prompt_minutes"), bool):
        config["return_prompt_minutes"] = 15
    limits = config.get("app_limits")
    if not isinstance(limits, dict) or any(not isinstance(k, str) or len(k) > 253 or not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 1440
                                           for k, v in limits.items()):
        config["app_limits"] = {}
    for key in ("site_names", "app_names"):
        names = config.get(key)
        if not isinstance(names, dict) or any(not isinstance(k, str) or not 0 < len(k) <= 253 or not isinstance(v, str) or not 0 < len(v.strip()) <= 30
                                              for k, v in names.items()):
            config[key] = {}
    subcats.validate(config)
    if not isinstance(config.get("overview_away"), bool):
        config["overview_away"] = True
    if config.get("daily_summary") not in ("off", "18:00", "20:00", "21:00", "22:00", "23:00"):
        config["daily_summary"] = "off"
    if not isinstance(config.get("focus_float"), bool):
        config["focus_float"] = True
    tasks = config.get("recent_tasks")
    if not isinstance(tasks, list) or any(not isinstance(t, str) or not t.strip() or len(t) > 40 for t in tasks):
        config["recent_tasks"] = []
    config["recent_tasks"] = config["recent_tasks"][:8]
    if not isinstance(config.get("float_pos"), str) or len(config["float_pos"]) > 40:
        config["float_pos"] = ""
    for key in ("remind_distraction", "reminder_sound", "tray_hint"):
        if not isinstance(config[key], bool):
            config[key] = DEFAULTS[key]
    for key, low, high in MINUTE_SETTINGS:
        if not isinstance(config[key], int) or isinstance(config[key], bool) or not low <= config[key] <= high:
            config[key] = DEFAULTS[key]
    for key in ["work_apps", "distraction_apps", "system_apps"]:
        if not isinstance(config[key], list) or any(not isinstance(x, str) for x in config[key]):
            raise ValueError(f"{key} 应为程序名列表")


def save_config(path: Path, config: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


@dataclass(frozen=True)
class Activity:
    category: str
    app: str = ""
    host: str = ""
    site: str = ""
    source: str = ""


def classify(app: str, title: str, host: str, config: dict, idle: bool = False, learning: bool = False) -> Activity:
    app = app.lower()
    if idle:
        return Activity("idle", source="无操作 / 已锁屏")
    if app in BROWSERS:
        # An authenticated, current domain is stronger evidence than title keywords.
        if host:
            site = site_for(host)
            rules = sorted(config["host_rules"], key=len, reverse=True)
            rule = next((config["host_rules"][d] for d in rules if match_host(host, d)), None)
            category = rule or ("distraction" if site else config["app_rules"].get(app, "unknown"))
            if site and learning:
                category = "work"
            return Activity(category, app, host, site, "域名识别")
        lower = title.lower()
        site = ""
        # Only suffix/branding matches, not arbitrary occurrences in a search query.
        if any(lower.endswith(s) for s in [" - 抖音", " - 抖音 - 记录美好生活", "抖音 - 记录美好生活"]):
            site = "抖音"
        elif any(lower.endswith(s) for s in [" - 小红书", "小红书 - 你的生活指南"]):
            site = "小红书"
        elif "_哔哩哔哩_bilibili" in lower or lower.endswith(("哔哩哔哩 (゜-゜)つロ 干杯~-bilibili", " - 哔哩哔哩", " - bilibili")):
            site = "B站"
        if site:
            # A user rule for the platform's own domain also applies without the extension.
            domains = [d for d, name in SITES.items() if name == site]
            rule = next((config["host_rules"][r] for r in sorted(config["host_rules"], key=len) if any(match_host(d, r) for d in domains)), None)
            category = "work" if learning else rule or "distraction"
            return Activity(category, app, site=site, source="标题推测 · 安装扩展可按域名识别")
        return Activity(config["app_rules"].get(app, "unknown"), app, source="浏览器未取得域名")
    rule = config["app_rules"].get(app)
    category = rule or ("work" if app in config["work_apps"] else "distraction" if app in config["distraction_apps"]
                        else "system" if app in config.get("system_apps", ()) else "unknown")
    source = "设为不计入，不记录时间" if category == IGNORE else "应用规则" if category != "unknown" else "尚无分类规则"
    return Activity(category, app, source=source)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: each per-second commit is an append without fsync; an app crash
        # loses nothing, a power cut may lose the last few seconds.
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS usage (
            day TEXT NOT NULL, category TEXT NOT NULL, app TEXT NOT NULL, host TEXT NOT NULL,
            seconds REAL NOT NULL, PRIMARY KEY(day,category,app,host))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS minute_usage (
            minute TEXT NOT NULL, category TEXT NOT NULL, app TEXT NOT NULL, host TEXT NOT NULL,
            seconds REAL NOT NULL, PRIMARY KEY(minute,category,app,host))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, started TEXT NOT NULL, planned INTEGER NOT NULL,
            seconds REAL NOT NULL, work REAL NOT NULL, distraction REAL NOT NULL, completed INTEGER NOT NULL)""")
        if "task" not in [row[1] for row in self.db.execute("PRAGMA table_info(focus_sessions)")]:
            self.db.execute("ALTER TABLE focus_sessions ADD COLUMN task TEXT NOT NULL DEFAULT ''")  # v03.1: task names
        self.db.execute("""CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL,
            created TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)""")
        self.db.commit()

    def add(self, start: datetime, end: datetime, activity: Activity):
        if end <= start or activity.category == IGNORE:
            return
        original_start = start
        while start < end:
            boundary = datetime.combine(start.date() + timedelta(days=1), datetime.min.time())
            stop = min(end, boundary)
            self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?)
                ON CONFLICT(day,category,app,host) DO UPDATE SET seconds=seconds+excluded.seconds""",
                (start.date().isoformat(), activity.category, activity.app, activity.host, (stop-start).total_seconds()))
            start = stop
        start = original_start
        while start < end:
            minute = start.replace(second=0, microsecond=0)
            stop = min(end, minute+timedelta(minutes=1))
            self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?)
                ON CONFLICT(minute,category,app,host) DO UPDATE SET seconds=seconds+excluded.seconds""",
                (minute.isoformat(timespec="minutes"), activity.category, activity.app, activity.host, (stop-start).total_seconds()))
            start = stop
        # Committed in batches by flush(): this connection already sees the rows, and other
        # writes (`with self.db`) or close() commit them too.
        self.pending = True

    def flush(self):
        """Commit sampled time recorded since the last flush (the app calls this every few seconds)."""
        if getattr(self, "pending", False):
            self.db.commit(); self.pending = False

    def totals(self, day: str) -> dict:
        result = dict.fromkeys(CATEGORIES, 0.0)
        result.update(dict(self.db.execute("SELECT category,SUM(seconds) FROM usage WHERE day=? GROUP BY category", (day,))))
        return result

    def range_apps(self, first: str, last: str):
        """Per (app, host) over days first..last: total seconds, the category with most time, days used."""
        rows = self.db.execute("""SELECT app,host,category,SUM(seconds),COUNT(DISTINCT day) FROM usage
            WHERE day>=? AND day<=? AND category!='idle' GROUP BY app,host,category""", (first, last)).fetchall()
        grouped = {}
        for app, host, category, seconds, days in rows:
            entry = grouped.setdefault((app, host), {"seconds": 0.0, "category": category, "best": 0.0, "days": 0})
            entry["seconds"] += seconds; entry["days"] = max(entry["days"], days)
            if seconds > entry["best"]: entry["category"], entry["best"] = category, seconds
        return sorted(((app, host, e["category"], e["seconds"], e["days"]) for (app, host), e in grouped.items()),
                      key=lambda row: row[3], reverse=True)

    def hour_profile(self, first: str, last: str):
        """Average seconds per hour of day and category, over the days first..last that have minute records."""
        end = (datetime.fromisoformat(last)+timedelta(days=1)).date().isoformat()
        days = self.db.execute("SELECT COUNT(DISTINCT substr(minute,1,10)) FROM minute_usage WHERE minute>=? AND minute<?",
                               (first+"T00:00", end+"T00:00")).fetchone()[0]
        rows = self.db.execute("""SELECT substr(minute,12,2),category,SUM(seconds) FROM minute_usage
            WHERE minute>=? AND minute<? GROUP BY 1,2""", (first+"T00:00", end+"T00:00")).fetchall()
        return days, [(hour, category, seconds/days) for hour, category, seconds in rows] if days else []

    def session_list(self, first: str, last: str):
        """(started, planned minutes, seconds, work, distraction, completed, task) per session, oldest first."""
        return self.db.execute("""SELECT started,planned,seconds,work,distraction,completed,task FROM focus_sessions
            WHERE day>=? AND day<=? ORDER BY started""", (first, last)).fetchall()

    def timeline(self, day: str):
        """For each recorded minute of `day`: (minute index 0–1439, category, app, host) of what filled most of it."""
        rows = self.db.execute("""SELECT minute,category,app,host,seconds FROM minute_usage
            WHERE minute>=? AND minute<? ORDER BY minute, seconds DESC""",
            (day+"T00:00", (datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+"T00:00")).fetchall()
        result, seen = [], set()
        for minute, category, app, host, _ in rows:
            if minute in seen:
                continue
            seen.add(minute)
            result.append((int(minute[11:13])*60+int(minute[14:16]), category, app, host))
        return result

    def totals_until(self, day: str, clock: str) -> dict | None:
        """Per-category seconds on `day` before `clock` (HH:MM), from minute records.

        Returns None when that day has no minute records (e.g. only legacy daily totals).
        """
        rows = self.db.execute("""SELECT category,SUM(seconds) FROM minute_usage WHERE minute>=? AND minute<?
            GROUP BY category""", (day+"T00:00", day+"T"+clock)).fetchall()
        if not rows and not self.db.execute("SELECT 1 FROM minute_usage WHERE substr(minute,1,10)=? LIMIT 1", (day,)).fetchone():
            return None
        result = dict.fromkeys(CATEGORIES, 0.0); result.update(dict(rows))
        return result

    def details(self, day: str):
        return self.db.execute("SELECT category,app,host,seconds FROM usage WHERE day=? ORDER BY seconds DESC", (day,)).fetchall()

    def history(self, days=7):
        since = (datetime.now().date()-timedelta(days=days-1)).isoformat()
        return self.db.execute("SELECT day,category,SUM(seconds) FROM usage WHERE day>=? GROUP BY day,category ORDER BY day DESC", (since,)).fetchall()

    def hourly(self, day):
        return self.db.execute("""SELECT substr(minute,12,2),category,SUM(seconds)
            FROM minute_usage WHERE minute>=? AND minute<? GROUP BY 1,2 ORDER BY 1""",
            (day+"T00:00", (datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+"T00:00")).fetchall()

    def reclassify(self, day, keys, target):
        """Correct selected aggregates atomically; store enough information for a precise undo.

        Target "ignore" removes the selected time; the correction record keeps it for undo.
        Away time (离开) has no program, so it moves under AWAY_LABEL; each record notes where it went.
        """
        if target not in RULE_TARGETS:
            raise ValueError("未知分类")
        drop = target == IGNORE
        source, minutes = [], []
        with self.db:
            for category, app, host in set(keys):
                if category == target:
                    continue
                # Away time has no program: it moves under AWAY_LABEL, and back to "" when set to 离开 again.
                new_app = "" if target == "idle" and app.endswith(MANUAL_MARK) else AWAY_LABEL if category == "idle" and not app else app
                row = self.db.execute("SELECT seconds FROM usage WHERE day=? AND category=? AND app=? AND host=?", (day,category,app,host)).fetchone()
                if not row:
                    continue
                source.append([category, app, host, row[0], new_app])
                self.db.execute("DELETE FROM usage WHERE day=? AND category=? AND app=? AND host=?", (day,category,app,host))
                if not drop:
                    self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day,category,app,host)
                        DO UPDATE SET seconds=seconds+excluded.seconds""", (day,target,new_app,host,row[0]))
                old_minutes = self.db.execute("SELECT minute,category,app,host,seconds FROM minute_usage WHERE substr(minute,1,10)=? AND category=? AND app=? AND host=?", (day,category,app,host)).fetchall()
                minutes.extend([*m, new_app] for m in old_minutes)
                for minute, _, _, _, seconds in old_minutes:
                    self.db.execute("DELETE FROM minute_usage WHERE minute=? AND category=? AND app=? AND host=?", (minute,category,app,host))
                    if not drop:
                        self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?) ON CONFLICT(minute,category,app,host)
                            DO UPDATE SET seconds=seconds+excluded.seconds""", (minute,target,new_app,host,seconds))
            if source:
                self.db.execute("INSERT INTO corrections(day,payload,created) VALUES (?,?,?)",
                    (day, json.dumps({"target":target,"rows":source,"minutes":minutes}), datetime.now().isoformat(timespec="seconds")))
        return len(source)

    def move_minutes(self, first: datetime, last: datetime, source: tuple, target: tuple) -> float:
        """Move minute records of (category, app, host) `source` within [first, last) to `target`,
        keeping the daily totals in step. Returns the seconds moved; moving back undoes it."""
        lo = first.replace(second=0, microsecond=0).isoformat(timespec="minutes"); hi = last.isoformat(timespec="minutes")
        moved = 0.0
        with self.db:
            rows = self.db.execute("""SELECT minute,seconds FROM minute_usage WHERE minute>=? AND minute<=?
                AND category=? AND app=? AND host=?""", (lo, hi, *source)).fetchall()
            for minute, seconds in rows:
                self.db.execute("DELETE FROM minute_usage WHERE minute=? AND category=? AND app=? AND host=?", (minute, *source))
                self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?) ON CONFLICT(minute,category,app,host)
                    DO UPDATE SET seconds=seconds+excluded.seconds""", (minute, *target, seconds))
                day = minute[:10]
                self.db.execute("UPDATE usage SET seconds=seconds-? WHERE day=? AND category=? AND app=? AND host=?", (seconds, day, *source))
                self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day,category,app,host)
                    DO UPDATE SET seconds=seconds+excluded.seconds""", (day, *target, seconds))
                moved += seconds
            self.db.execute("DELETE FROM usage WHERE seconds<0.000001")
        return moved

    def segments(self, day: str):
        """The day in order: (first minute, last minute, category, app, host, seconds) per stretch.

        Each minute belongs to whatever filled most of it; neighbouring minutes of the same thing
        join into one stretch. Minutes are indexes 0–1439, both ends included; seconds are that
        activity's own recorded seconds inside the stretch.
        """
        rows = self.db.execute("""SELECT minute,category,app,host,seconds FROM minute_usage
            WHERE minute>=? AND minute<? ORDER BY minute, seconds DESC""",
            (day+"T00:00", (datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+"T00:00")).fetchall()
        result, seen = [], None
        for minute, category, app, host, seconds in rows:
            if minute == seen:
                continue
            seen = minute; index = int(minute[11:13])*60+int(minute[14:16])
            if result and result[-1][1] == index-1 and tuple(result[-1][2:5]) == (category, app, host):
                result[-1][1] = index; result[-1][5] += seconds
            else:
                result.append([index, index, category, app, host, seconds])
        return [tuple(row) for row in result]

    def reclassify_span(self, day, spans, target):
        """Like reclassify, but only inside stretches of the day: spans are
        (first minute, last minute, category, app, host). Undone by undo() like any correction."""
        if target not in RULE_TARGETS:
            raise ValueError("未知分类")
        drop = target == IGNORE
        stamp = lambda index: f"{day}T{index//60:02d}:{index % 60:02d}"
        source, minutes = [], []
        with self.db:
            for first, last, category, app, host in sorted(set(spans)):
                if category == target:
                    continue
                new_app = "" if target == "idle" and app.endswith(MANUAL_MARK) else AWAY_LABEL if category == "idle" and not app else app
                rows = self.db.execute("""SELECT minute,seconds FROM minute_usage WHERE minute>=? AND minute<=?
                    AND category=? AND app=? AND host=?""", (stamp(first), stamp(last), category, app, host)).fetchall()
                if not rows:
                    continue
                moved = sum(seconds for _, seconds in rows)
                source.append([category, app, host, moved, new_app])
                self.db.execute("UPDATE usage SET seconds=seconds-? WHERE day=? AND category=? AND app=? AND host=?", (moved, day, category, app, host))
                if not drop:
                    self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day,category,app,host)
                        DO UPDATE SET seconds=seconds+excluded.seconds""", (day, target, new_app, host, moved))
                for minute, seconds in rows:
                    minutes.append([minute, category, app, host, seconds, new_app])
                    self.db.execute("DELETE FROM minute_usage WHERE minute=? AND category=? AND app=? AND host=?", (minute, category, app, host))
                    if not drop:
                        self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?) ON CONFLICT(minute,category,app,host)
                            DO UPDATE SET seconds=seconds+excluded.seconds""", (minute, target, new_app, host, seconds))
            self.db.execute("DELETE FROM usage WHERE seconds<0.000001")
            if source:
                self.db.execute("INSERT INTO corrections(day,payload,created) VALUES (?,?,?)",
                    (day, json.dumps({"target": target, "rows": source, "minutes": minutes}), datetime.now().isoformat(timespec="seconds")))
        return len(source)

    def add_manual(self, start: datetime, end: datetime, category: str, name: str) -> float:
        """Record [start, end) of one day after the fact (a meeting, reading on paper, a class).

        Whole minutes only. Away time inside the span is re-labelled and unrecorded time is filled
        in; time already recorded for a program is left alone. The time is stored under `name` plus
        MANUAL_MARK. Returns the seconds now counted; undo() takes all of it back.
        """
        start = start.replace(second=0, microsecond=0); end = end.replace(second=0, microsecond=0)
        if end <= start or start.date() != (end-timedelta(minutes=1)).date() or category not in CATEGORIES:
            raise ValueError("补记的时间段无效")
        day = start.date().isoformat(); app = (name.strip()[:20] or "补记").removesuffix(MANUAL_MARK)+MANUAL_MARK
        away = ("idle", "", ""); moved_minutes, extra_minutes, moved, extra = [], [], 0.0, 0.0
        with self.db:
            cursor = start
            while cursor < end:
                minute = cursor.isoformat(timespec="minutes"); cursor += timedelta(minutes=1)
                idle = self.db.execute("SELECT COALESCE(SUM(seconds),0) FROM minute_usage WHERE minute=? AND category=? AND app=? AND host=?", (minute, *away)).fetchone()[0]
                used = self.db.execute("SELECT COALESCE(SUM(seconds),0) FROM minute_usage WHERE minute=?", (minute,)).fetchone()[0]-idle
                free = max(0.0, 60.0-used); take = min(idle, free); fill = free-take
                if take > 0:
                    self.db.execute("DELETE FROM minute_usage WHERE minute=? AND category=? AND app=? AND host=?", (minute, *away))
                    if idle-take > 0.000001:
                        self.db.execute("INSERT INTO minute_usage VALUES (?,?,?,?,?)", (minute, *away, idle-take))
                    moved_minutes.append([minute, *away, take, app]); moved += take
                if fill > 0:
                    extra_minutes.append([minute, category, app, "", fill]); extra += fill
                if take+fill > 0:
                    self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?) ON CONFLICT(minute,category,app,host)
                        DO UPDATE SET seconds=seconds+excluded.seconds""", (minute, category, app, "", take+fill))
            if moved+extra <= 0:
                return 0.0
            if moved:
                self.db.execute("UPDATE usage SET seconds=seconds-? WHERE day=? AND category=? AND app=? AND host=?", (moved, day, *away))
                self.db.execute("DELETE FROM usage WHERE seconds<0.000001")
            self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day,category,app,host)
                DO UPDATE SET seconds=seconds+excluded.seconds""", (day, category, app, "", moved+extra))
            payload = {"target": category, "rows": [[*away, moved, app]] if moved else [], "minutes": moved_minutes,
                       "extra_rows": [[category, app, "", extra]] if extra else [], "extra_minutes": extra_minutes}
            self.db.execute("INSERT INTO corrections(day,payload,created) VALUES (?,?,?)",
                            (day, json.dumps(payload), datetime.now().isoformat(timespec="seconds")))
        return moved+extra

    def range_rows(self, first: str, last: str):
        """(category, app, host, seconds) summed over the days first..last."""
        return self.db.execute("""SELECT category,app,host,SUM(seconds) FROM usage WHERE day>=? AND day<=?
            GROUP BY category,app,host""", (first, last)).fetchall()

    def used_today(self, day: str, key: str) -> float:
        """Seconds on `day` for a program ("x.exe") or a website and its subdomains, across categories."""
        if key.endswith(".exe"):
            row = self.db.execute("SELECT COALESCE(SUM(seconds),0) FROM usage WHERE day=? AND app=? AND host=''", (day, key)).fetchone()
        else:
            row = self.db.execute("SELECT COALESCE(SUM(seconds),0) FROM usage WHERE day=? AND (host=? OR host LIKE ?)", (day, key, "%."+key)).fetchone()
        return float(row[0])

    def add_session(self, started: datetime, planned: int, seconds: float, work: float, distraction: float, completed: bool, task: str = ""):
        with self.db:
            self.db.execute("INSERT INTO focus_sessions(day,started,planned,seconds,work,distraction,completed,task) VALUES (?,?,?,?,?,?,?,?)",
                (started.date().isoformat(), started.isoformat(timespec="seconds"), planned, seconds, work, distraction, int(completed), task.strip()[:40]))

    def day_totals(self, first: str, last: str) -> dict:
        """{day: {category: seconds}} for every day first..last that has records."""
        result = {}
        for day, category, seconds in self.db.execute(
                "SELECT day,category,SUM(seconds) FROM usage WHERE day>=? AND day<=? GROUP BY day,category", (first, last)):
            result.setdefault(day, dict.fromkeys(CATEGORIES, 0.0))[category] = seconds
        return result

    def sessions(self, day: str):
        """(completed count, focused seconds) for one day."""
        row = self.db.execute("SELECT COALESCE(SUM(completed),0), COALESCE(SUM(seconds),0) FROM focus_sessions WHERE day=?", (day,)).fetchone()
        return int(row[0]), float(row[1])

    def can_undo(self):
        return self.db.execute("SELECT 1 FROM corrections WHERE undone=0 LIMIT 1").fetchone() is not None

    def undo(self):
        record = self.db.execute("SELECT id,day,payload FROM corrections WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not record:
            return False
        ident, day, payload = record
        data = json.loads(payload)
        target = data["target"]
        moved = target != IGNORE  # ignored time was removed, so there is nothing to subtract
        with self.db:
            for category, app, host, seconds, *moved_to in data["rows"]:  # older records have no 5th field
                if moved:
                    self.db.execute("UPDATE usage SET seconds=seconds-? WHERE day=? AND category=? AND app=? AND host=?", (seconds,day,target,moved_to[0] if moved_to else app,host))
                self.db.execute("""INSERT INTO usage VALUES (?,?,?,?,?) ON CONFLICT(day,category,app,host)
                    DO UPDATE SET seconds=seconds+excluded.seconds""", (day,category,app,host,seconds))
            for minute, category, app, host, seconds, *moved_to in data["minutes"]:
                if moved:
                    self.db.execute("UPDATE minute_usage SET seconds=seconds-? WHERE minute=? AND category=? AND app=? AND host=?", (seconds,minute,target,moved_to[0] if moved_to else app,host))
                self.db.execute("""INSERT INTO minute_usage VALUES (?,?,?,?,?) ON CONFLICT(minute,category,app,host)
                    DO UPDATE SET seconds=seconds+excluded.seconds""", (minute,category,app,host,seconds))
            # Time that add_manual() filled in (it came from nowhere, so it simply goes away again).
            for category, app, host, seconds in data.get("extra_rows", []):
                self.db.execute("UPDATE usage SET seconds=seconds-? WHERE day=? AND category=? AND app=? AND host=?", (seconds,day,category,app,host))
            for minute, category, app, host, seconds in data.get("extra_minutes", []):
                self.db.execute("UPDATE minute_usage SET seconds=seconds-? WHERE minute=? AND category=? AND app=? AND host=?", (seconds,minute,category,app,host))
            self.db.execute("DELETE FROM usage WHERE seconds<0.000001")
            self.db.execute("DELETE FROM minute_usage WHERE seconds<0.000001")
            self.db.execute("UPDATE corrections SET undone=1 WHERE id=?", (ident,))
        return True

    def close(self):
        self.flush(); self.db.close()


def streaks(work_by_day: dict, goal_seconds: float, today: str):
    """(current, best) runs of consecutive days whose work reached the goal.

    Without a goal a day counts after STREAK_FALLBACK_SECONDS of work. Today extends the
    current run once reached, but an unfinished today does not break it.
    """
    need = goal_seconds or STREAK_FALLBACK_SECONDS
    reached = sorted(day for day, seconds in work_by_day.items() if seconds >= need and day <= today)
    if not reached:
        return 0, 0
    best = run = 0; previous = None
    for day in reached:
        current = datetime.fromisoformat(day).date()
        run = run+1 if previous and (current-previous).days == 1 else 1
        best = max(best, run); previous = current
    last = datetime.fromisoformat(reached[-1]).date(); now = datetime.fromisoformat(today).date()
    return (run if (now-last).days <= 1 else 0), best


class Accountant:
    """Attribute a bounded interval to the previous observation; never charge sleep gaps."""
    def __init__(self, store: Store):
        self.store = store
        self.previous = None

    def tick(self, wall: datetime, mono: float, activity: Activity | None, idle_seconds: float = 0):
        if self.previous:
            old_wall, old_mono, old_activity = self.previous
            elapsed = mono-old_mono
            wall_elapsed = (wall-old_wall).total_seconds()
            if old_activity and 0 < elapsed <= 8 and abs(wall_elapsed-elapsed) < 2:
                # If an interval crosses the idle threshold, split it rather than count it as work.
                if activity and activity.category == "idle" and old_activity.category != "idle":
                    idle_part = min(elapsed, max(0.0, idle_seconds))
                    split = wall-timedelta(seconds=idle_part)
                    self.store.add(old_wall, split, old_activity)
                    self.store.add(split, wall, activity)
                else:
                    self.store.add(old_wall, wall, old_activity)
        self.previous = (wall, mono, activity)
