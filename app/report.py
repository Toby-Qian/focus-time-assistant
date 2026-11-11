"""Exportable analysis: one self-contained HTML report and / or a set of CSV tables.

Everything is computed from the local database; nothing leaves the computer.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from html import escape
import io
from pathlib import Path

from core import CATEGORIES, EXPORT_PARTS
from charts import focus_ratio, short_time, tick_label
from insights import insights
from theme import CATEGORY_LIGHT, app_name, readable

COLORS = CATEGORY_LIGHT[0]
TEXT_COLORS = {key: readable(value, "#FFFFFF") for key, value in COLORS.items()}  # text in a category colour, 4.5:1 on white


def days_between(first: str, last: str):
    day, end = datetime.fromisoformat(first).date(), datetime.fromisoformat(last).date()
    while day <= end:
        yield day.isoformat(); day += timedelta(days=1)


def hms(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"


def collect(store, first, last, include_unknown=True):
    days = list(days_between(first, last))
    daily = {day: dict.fromkeys(CATEGORIES, 0.0) for day in days}
    for day, key, seconds in store.db.execute(
            "SELECT day,category,SUM(seconds) FROM usage WHERE day>=? AND day<=? GROUP BY day,category", (first, last)):
        if day in daily: daily[day][key] = seconds
    summed = {key: sum(d[key] for d in daily.values()) for key in CATEGORIES}
    active = [d for d in daily.values() if sum(d.values())]
    sessions = store.session_list(first, last)
    profile_days, profile = store.hour_profile(first, last)
    hours = {f"{h:02d}": dict.fromkeys(CATEGORIES, 0.0) for h in range(24)}
    for hour, key, seconds in profile: hours[hour][key] = seconds
    return {"first": first, "last": last, "days": days, "daily": daily, "summed": summed, "active": len(active),
            "focus": focus_ratio(summed, include_unknown), "apps": store.range_apps(first, last),
            "hours": hours, "profile_days": profile_days, "sessions": sessions,
            "details": {day: store.details(day) for day in days}, "insights": insights(store, first, last, include_unknown)}


# ── CSV ───────────────────────────────────────────────────────────────
def _csv(rows):
    buffer = io.StringIO(); csv.writer(buffer).writerows(rows); return buffer.getvalue()


def csv_files(data, parts, include_unknown=True):
    files = {}
    if "daily" in parts:
        rows = [["日期", *(f"{v}（秒）" for v in CATEGORIES.values()), "工作", "摸鱼", "专注率"]]
        for day, t in data["daily"].items():
            ratio = focus_ratio(t, include_unknown)
            rows.append([day, *(round(t[k]) for k in CATEGORIES),
                         hms(t["work"]), hms(t["distraction"]), f"{ratio:.1%}" if ratio is not None else ""])
        files["每日汇总.csv"] = _csv(rows)
    if "apps" in parts:
        rows = [["应用", "域名", "主要分类", "累计秒数", "累计时长", "使用天数"]]
        rows += [[app_name(a), h, CATEGORIES[c], round(s), hms(s), d] for a, h, c, s, d in data["apps"]]
        files["应用与网站排行.csv"] = _csv(rows)
    if "hours" in parts:
        rows = [["小时", *(f"{v}（日均秒）" for v in CATEGORIES.values())]]
        rows += [[f"{h}:00", *(round(t[k]) for k in CATEGORIES)] for h, t in data["hours"].items()]
        files["一天中的时段分布.csv"] = _csv(rows)
    if "sessions" in parts:
        rows = [["开始时间", "任务", "计划分钟", "实际时长", "工作", "摸鱼", "是否完成"]]
        rows += [[started, task, planned, hms(sec), hms(work), hms(play), "完成" if done else "提前结束"]
                 for started, planned, sec, work, play, done, task in data["sessions"]]
        files["专注记录.csv"] = _csv(rows)
    if "details" in parts:
        rows = [["日期", "分类", "应用", "域名", "秒数", "时长"]]
        for day, entries in data["details"].items():
            rows += [[day, CATEGORIES[c], app_name(a), h, round(s, 2), hms(s)] for c, a, h, s in entries]
        files["每日应用明细.csv"] = _csv(rows)
    return files


# ── HTML ──────────────────────────────────────────────────────────────
def _bars_svg(columns, width=860, height=230, labels_every=1):
    """Stacked bars as inline SVG; columns: [(label, {category: seconds})]."""
    left, bottom, top = 46, height-26, 10
    peak = max((sum(t.values()) for _, t in columns), default=0) or 1
    step = next((s for s in (60, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600) if s*4 >= peak), 21600)
    maximum = step*max(1, -(-int(peak)//step))
    graph = bottom-top; slot = (width-left-8)/max(1, len(columns)); bar = max(3, min(28, slot*.62))
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for i in range(int(maximum/step)+1):
        y = bottom-graph*i*step/maximum
        label = tick_label(i*step)
        parts.append(f'<line x1="{left}" x2="{width-8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{left-8}" y="{y+4:.1f}" class="axis" text-anchor="end">{label}</text>')
    for index, (label, totals) in enumerate(columns):
        x = left+slot*index+(slot-bar)/2; y = bottom
        for key in CATEGORIES:
            h = graph*totals.get(key, 0)/maximum
            if h >= .3:
                parts.append(f'<rect x="{x:.1f}" y="{y-h:.1f}" width="{bar:.1f}" height="{h:.1f}" fill="{COLORS[key]}"><title>{escape(label)} {CATEGORIES[key]} {short_time(totals[key])}</title></rect>')
                y -= h
        if index % labels_every == 0:
            parts.append(f'<text x="{x+bar/2:.1f}" y="{height-8}" class="axis" text-anchor="middle">{escape(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def html_report(data, parts, accent, include_unknown=True):
    s = data["summed"]; active = max(1, data["active"])
    completed = sum(1 for row in data["sessions"] if row[5])
    tiles = [("工作合计", short_time(s["work"])), ("有记录日平均工作", short_time(s["work"]/active) if data["active"] else "—"),
             ("摸鱼合计", short_time(s["distraction"])), ("专注率", f"{data['focus']:.0%}" if data["focus"] is not None else "—"),
             ("有记录天数", f"{data['active']} 天"), ("完成专注", f"{completed} 次")]
    out = [f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>专注时间报告 {data['first']} – {data['last']}</title><style>
:root{{--accent:{accent};--ink:#18213A;--muted:#56627A;--line:#E5E9F1;--bg:#F4F6FA}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Microsoft YaHei UI","PingFang SC",sans-serif}}
main{{max-width:980px;margin:0 auto;padding:32px 20px 60px}} h1{{margin:0;font-size:28px}} h2{{font-size:17px;margin:0 0 14px}}
.sub{{color:var(--muted);margin:4px 0 24px}} .card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px}}
.tile{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}} .tile b{{display:block;font-size:20px;font-family:"Segoe UI",sans-serif}}
.tile span{{color:var(--muted);font-size:12px}} table{{width:100%;border-collapse:collapse}} th{{text-align:left;color:var(--muted);font-weight:400;font-size:12px;border-bottom:1px solid var(--line);padding:6px 8px}}
td{{padding:7px 8px;border-bottom:1px solid #F0F2F7}} .num{{font-family:"Segoe UI",sans-serif;white-space:nowrap}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}} .bar{{height:6px;border-radius:3px;background:#EEF1F6}} .bar i{{display:block;height:6px;border-radius:3px}}
.chart{{width:100%;height:auto}} .grid{{stroke:#F0F2F6}} .axis{{fill:#667186;font-size:11px}} .insights{{margin:0;padding:0;list-style:none}} .insights li{{padding:6px 0;border-bottom:1px solid #F0F2F7}} .insights li:last-child{{border:0}} .legend span{{margin-right:14px;color:var(--muted);font-size:12px}}
details summary{{cursor:pointer;color:var(--accent)}} footer{{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}}
.accent{{height:6px;background:var(--accent);border-radius:3px;width:64px;margin-bottom:14px}}
</style></head><body><main><div class="accent"></div><h1>专注时间报告</h1>
<p class="sub">{data['first']} 至 {data['last']} · 共 {len(data['days'])} 天 · 生成于 {datetime.now():%Y-%m-%d %H:%M}</p>
<div class="tiles">{''.join(f'<div class="tile"><span>{t}</span><b>{v}</b></div>' for t, v in tiles)}</div>"""]
    if data.get("insights"):  # sentences are built by insights.py from numbers only; <b> marks the key figure
        out.append("<section class='card'><h2>要点</h2><ul class='insights'>"+"".join(f"<li>{line}</li>" for _, line in data["insights"])+"</ul></section>")
    legend = "<p class='legend'>"+"".join(f"<span><i class='dot' style='background:{COLORS[k]}'></i>{v}</span>" for k, v in CATEGORIES.items())+"</p>"
    if "daily" in parts:
        columns = [(day[5:].replace("-", "/"), t) for day, t in data["daily"].items()]
        out.append(f"<section class='card'><h2>每日时间</h2>{legend}{_bars_svg(columns, labels_every=max(1, round(len(columns)/10)))}")
        out.append("<table><tr><th>日期</th><th>工作</th><th>摸鱼</th><th>离开</th><th>待分类</th><th>专注率</th></tr>")
        for day, t in reversed(list(data["daily"].items())):
            ratio = focus_ratio(t, include_unknown)
            out.append(f"<tr><td>{day}</td>"+"".join(f"<td class='num' style='color:{TEXT_COLORS[k]}'>{hms(t[k]) if t[k] else '—'}</td>" for k in CATEGORIES)
                       + f"<td class='num'>{f'{ratio:.0%}' if ratio is not None else '—'}</td></tr>")
        out.append("</table></section>")
    if "apps" in parts:
        apps = data["apps"][:40]; top = apps[0][3] if apps else 1
        out.append("<section class='card'><h2>应用与网站排行</h2><table><tr><th>应用 / 网站</th><th>主要分类</th><th style='width:34%'></th><th>累计</th><th>天数</th></tr>")
        for app, host, cat, seconds, days in apps:
            out.append(f"<tr><td>{escape(host or app_name(app))}</td><td><i class='dot' style='background:{COLORS[cat]}'></i>{CATEGORIES[cat]}</td>"
                       f"<td><div class='bar'><i style='width:{max(2, 100*seconds/top):.1f}%;background:{COLORS[cat]}'></i></div></td>"
                       f"<td class='num'>{short_time(seconds)}</td><td class='num'>{days}</td></tr>")
        out.append("</table>" + ("" if apps else "<p class='sub'>这段时间没有记录。</p>") + "</section>")
    if "hours" in parts:
        columns = [(f"{h}", t) for h, t in data["hours"].items()]
        note = f"按有分钟记录的 {data['profile_days']} 天平均" if data["profile_days"] else "这段时间没有分钟记录"
        out.append(f"<section class='card'><h2>一天中的时段分布</h2><p class='sub' style='margin:0 0 8px'>{note}</p>{legend}{_bars_svg(columns, labels_every=2)}</section>")
    if "sessions" in parts:
        out.append("<section class='card'><h2>专注记录</h2>")
        if data["sessions"]:
            out.append("<table><tr><th>开始</th><th>任务</th><th>计划</th><th>实际</th><th>工作</th><th>摸鱼</th><th>结果</th></tr>")
            for started, planned, seconds, work, play, done, task in data["sessions"]:
                out.append(f"<tr><td>{started.replace('T', ' ')}</td><td>{escape(task) or '—'}</td><td class='num'>{planned} 分钟</td><td class='num'>{short_time(seconds)}</td>"
                           f"<td class='num'>{short_time(work)}</td><td class='num'>{short_time(play)}</td><td>{'完成' if done else '提前结束'}</td></tr>")
            out.append("</table>")
        else:
            out.append("<p class='sub'>这段时间没有专注记录。</p>")
        out.append("</section>")
    if "details" in parts:
        out.append("<section class='card'><h2>每日应用明细</h2>")
        for day, entries in reversed(list(data["details"].items())):
            if not entries: continue
            out.append(f"<details><summary>{day}（{len(entries)} 项）</summary><table><tr><th>分类</th><th>应用</th><th>域名</th><th>时长</th></tr>")
            out += [f"<tr><td><i class='dot' style='background:{COLORS[c]}'></i>{CATEGORIES[c]}</td><td>{escape(app_name(a) or '离开 / 锁屏')}</td><td>{escape(h or '—')}</td><td class='num'>{hms(s)}</td></tr>" for c, a, h, s in entries]
            out.append("</table></details>")
        out.append("</section>")
    out.append("<footer>由专注时间助手在本机生成 · 数据只来自这台电脑</footer></main></body></html>")
    return "".join(out)


def export(store, folder: Path, first, last, parts, fmt, accent, include_unknown=True, name="专注时间报告"):
    """Write the report into `folder` and return the written paths."""
    data = collect(store, first, last, include_unknown)
    folder.mkdir(parents=True, exist_ok=True); written = []
    if fmt in ("html", "both"):
        path = folder/f"{name}.html"; path.write_text(html_report(data, parts, accent, include_unknown), encoding="utf-8"); written.append(path)
    if fmt in ("csv", "both"):
        for name, content in csv_files(data, parts, include_unknown).items():
            # newline="": csv already ends rows with \r\n; translating again would add blank rows in Excel.
            path = folder/name; path.write_text(content, encoding="utf-8-sig", newline=""); written.append(path)
    return written


__all__ = ["export", "collect", "html_report", "csv_files", "EXPORT_PARTS"]
