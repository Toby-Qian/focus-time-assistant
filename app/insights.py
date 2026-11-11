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


def work_rhythm(store, first, last):
    """How work time was spread, from the per-minute records of days first..last.

    A work stretch continues across gaps of at most two minutes (a quick look at something else);
    its length counts work minutes only. Switches are changes of program / website between
    neighbouring active minutes, per active hour.
    """
    day, end = datetime.fromisoformat(first).date(), datetime.fromisoformat(last).date()
    runs, switches, active, starts, ends = [], 0, 0, [], []
    while day <= end:
        rows = store.timeline(day.isoformat())
        busy = [r for r in rows if r[1] != "idle"]
        if busy:
            starts.append(busy[0][0]); ends.append(busy[-1][0]+1); active += len(busy)
        previous = None
        for minute, _, app, host in busy:
            if previous and (app, host) != previous[1] and minute-previous[0] <= 2: switches += 1
            previous = (minute, (app, host))
        begin = last_minute = None; count = 0
        for minute, category, _, _ in rows:
            if category != "work": continue
            if last_minute is not None and minute-last_minute <= 3: count += 1
            else:
                if count: runs.append((count, day, begin))
                begin, count = minute, 1
            last_minute = minute
        if count: runs.append((count, day, begin))
        day += timedelta(days=1)
    deep = [r for r in runs if r[0] >= 25]
    return {"days": len(starts), "longest": max(runs, default=None), "deep": (len(deep), sum(r[0] for r in deep)),
            "average": sum(r[0] for r in runs)/len(runs) if runs else None, "runs": len(runs), "work": sum(r[0] for r in runs),
            "switches": switches/(active/60) if active >= 30 else None,
            "start": sum(starts)/len(starts) if starts else None, "end": sum(ends)/len(ends) if ends else None}


def insights(store, first, last, include_unknown=True, limit=6):
    found = []
    one = first == last  # a single day: no "平均每天", compare with the day before
    profile_days, profile = store.hour_profile(first, last)
    if profile_days:
        work, play = {}, {}
        for hour, category, seconds in profile:
            if category == "work": work[int(hour)] = seconds
            elif category == "distraction": play[int(hour)] = seconds
        if work and max(work.values()) >= 5*60:
            hour = max(work, key=work.get)
            found.append(("target", f"最专注的时段是 <b>{hour}–{hour+1} 点</b>，{'这一小时' if one else '平均每天这一小时'}工作 {short_time(work[hour])}。"))
        if play and max(play.values()) >= 3*60:
            hour = max(play, key=play.get)
            found.append(("distraction", f"<b>{hour}–{hour+1} 点</b>最容易分心，{'这一小时' if one else '平均每天'}摸鱼 {short_time(play[hour])}。"))

    rows = store.db.execute("""SELECT app,host,SUM(seconds) FROM usage WHERE day>=? AND day<=? AND category='distraction'
        GROUP BY app,host ORDER BY 3 DESC""", (first, last)).fetchall()
    total_play = sum(r[2] for r in rows)
    if rows and total_play >= 10*60:
        app, host, seconds = rows[0]
        from theme import label
        name = label(app, host)
        found.append(("rank", f"最大的分心来源是 <b>{name}</b>，占摸鱼时间的 {seconds/total_play:.0%}（{short_time(seconds)}）。"))

    span = _days(first, last)
    average, totals = _average_work(store, first, last)
    before_last = (datetime.fromisoformat(first)-timedelta(days=1)).date().isoformat()
    before_first = (datetime.fromisoformat(first)-timedelta(days=span)).date().isoformat()
    previous, previous_totals = _average_work(store, before_first, before_last)
    lead, before = ("当天工作", "前一天") if one else ("有记录的日子平均工作", f"前 {span} 天")
    if average and previous:
        change = average/previous-1
        if abs(change) < .05:
            found.append(("calendar", f"{lead} <b>{short_time(average)}</b>，和{before}基本持平。"))
        else:
            found.append(("calendar", f"{lead} <b>{short_time(average)}</b>，比{before}{'多' if change > 0 else '少'} {abs(change):.0%}。"))
        now = focus_ratio({k: sum(t[k] for t in totals.values()) for k in ("work", "distraction", "unknown")}, include_unknown)
        then = focus_ratio({k: sum(t[k] for t in previous_totals.values()) for k in ("work", "distraction", "unknown")}, include_unknown)
        if now is not None and then is not None and abs(now-then) >= .03:
            found.append(("focus", f"专注率 <b>{now:.0%}</b>，比{before}{'提高' if now > then else '下降'}了 {abs(now-then)*100:.0f} 个百分点。"))

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
