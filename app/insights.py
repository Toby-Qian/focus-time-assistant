"""Plain-language findings for a date range: when focus is best, what distracts most, how it compares.

Each finding is (icon kind, sentence); the key figure is wrapped in <b> so the analysis page and the
HTML report can emphasise it. Findings are only produced when the data supports them.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from charts import focus_ratio, short_time
from core import site_for


def _days(first, last):
    return (datetime.fromisoformat(last)-datetime.fromisoformat(first)).days+1


def _average_work(store, first, last):
    totals = store.day_totals(first, last)
    active = [t for t in totals.values() if sum(t.values())]
    return (sum(t["work"] for t in active)/len(active) if active else None), totals


def insights(store, first, last, include_unknown=True, limit=6):
    found = []
    profile_days, profile = store.hour_profile(first, last)
    if profile_days:
        work, play = {}, {}
        for hour, category, seconds in profile:
            if category == "work": work[int(hour)] = seconds
            elif category == "distraction": play[int(hour)] = seconds
        if work and max(work.values()) >= 5*60:
            hour = max(work, key=work.get)
            found.append(("target", f"最专注的时段是 <b>{hour}–{hour+1} 点</b>，平均每天这一小时工作 {short_time(work[hour])}。"))
        if play and max(play.values()) >= 3*60:
            hour = max(play, key=play.get)
            found.append(("distraction", f"<b>{hour}–{hour+1} 点</b>最容易分心，平均每天摸鱼 {short_time(play[hour])}。"))

    rows = store.db.execute("""SELECT app,host,SUM(seconds) FROM usage WHERE day>=? AND day<=? AND category='distraction'
        GROUP BY app,host ORDER BY 3 DESC""", (first, last)).fetchall()
    total_play = sum(r[2] for r in rows)
    if rows and total_play >= 10*60:
        app, host, seconds = rows[0]
        from theme import app_name
        name = site_for(host) or host or app_name(app)
        found.append(("rank", f"最大的分心来源是 <b>{name}</b>，占摸鱼时间的 {seconds/total_play:.0%}（{short_time(seconds)}）。"))

    span = _days(first, last)
    average, totals = _average_work(store, first, last)
    before_last = (datetime.fromisoformat(first)-timedelta(days=1)).date().isoformat()
    before_first = (datetime.fromisoformat(first)-timedelta(days=span)).date().isoformat()
    previous, previous_totals = _average_work(store, before_first, before_last)
    if average and previous:
        change = average/previous-1
        if abs(change) < .05:
            found.append(("calendar", f"有记录的日子平均工作 <b>{short_time(average)}</b>，和前 {span} 天基本持平。"))
        else:
            found.append(("calendar", f"有记录的日子平均工作 <b>{short_time(average)}</b>，比前 {span} 天{'多' if change > 0 else '少'} {abs(change):.0%}。"))
        now = focus_ratio({k: sum(t[k] for t in totals.values()) for k in ("work", "distraction", "unknown")}, include_unknown)
        then = focus_ratio({k: sum(t[k] for t in previous_totals.values()) for k in ("work", "distraction", "unknown")}, include_unknown)
        if now is not None and then is not None and abs(now-then) >= .03:
            found.append(("focus", f"专注率 <b>{now:.0%}</b>，比前 {span} 天{'提高' if now > then else '下降'}了 {abs(now-then)*100:.0f} 个百分点。"))

    sessions = store.session_list(first, last)
    if sessions:
        done = sum(1 for row in sessions if row[5])
        tasks = {}
        for row in sessions:
            if row[6]: tasks[row[6]] = tasks.get(row[6], 0)+row[2]
        sentence = f"完成专注 <b>{done} / {len(sessions)}</b> 次"
        if tasks:
            task = max(tasks, key=tasks.get)
            sentence += f"，投入最多的任务是「{task}」（{short_time(tasks[task])}）"
        found.append(("flame", sentence+"。"))
    return found[:limit]
