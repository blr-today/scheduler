import datetime
import json
import os
from collections import Counter
from pathlib import Path

from .events import IST, LEAD, parse_time
from .pacing import RUN, in_window, slots, spacing

HORIZON = datetime.timedelta(days=7)
RECENT = datetime.timedelta(hours=48)
DAILY = datetime.timedelta(days=30)


def forecast(pending, next_slot, now, horizon=HORIZON):
    """When each queued event would go out if nothing new arrived, replaying the scheduler's runs"""
    queue, plan, missed, t = list(pending), [], [], now
    while queue and t < now + horizon:
        if in_window(t):
            missed += [e for e in queue if e["_start"] - LEAD < t]
            queue = [e for e in queue if e["_start"] - LEAD >= t]
            due, next_slot = slots(next_slot, spacing([e["_start"] - LEAD for e in queue], t), t, len(queue))
            plan += [(t, queue.pop(0)) for _ in due]
        t += RUN
    return plan, missed


def event_row(event, accounts):
    return {
        "name": event.get("name"),
        "url": event["url"],
        "starts": event["_start"].isoformat(),
        "deadline": (event["_start"] - LEAD).isoformat(),
        "accounts": sorted(name for name, calendar in accounts.items() if event in calendar),
    }


def stats(ledger, now):
    """Running totals from the ledger, for the website's /follow/ page and /metrics"""
    entries = ledger.posted().values()
    times = sorted(t for t in (parse_time(e["posted"]) for e in entries) if t)
    days = Counter(t.astimezone(IST).date().isoformat() for t in times if now - t <= DAILY)
    return {
        "posted": len(times),
        "since": times[0].isoformat() if times else None,
        "last_7_days": sum(1 for t in times if now - t <= datetime.timedelta(days=7)),
        "by_day": dict(sorted(days.items())),
        "reposts": dict(Counter(name for e in entries for name in e.get("reposted", [])).most_common()),
        "last_calls": sum(1 for e in entries if e.get("last_call")),
        "corrections": sum(e.get("corrections", 0) for e in entries),
        "skipped": dict(Counter(s["reason"] for s in ledger.skipped.values()).most_common()),
    }


def build(platform, outcome, ledger, accounts, now, post_link, dry_run=False):
    """Everything the /_debug/social/ page shows for one platform"""
    plan, missed = forecast(outcome.pending, ledger.next_slot, now)
    recent = []
    for url, entry in ledger.posted().items():
        posted = parse_time(entry["posted"])
        if posted and now - posted <= RECENT:
            facts = entry.get("facts") or {}
            recent.append({
                "at": posted.isoformat(), "url": url, "name": outcome.names.get(url), "starts": facts.get("start"),
                "post": post_link(entry["ref"]), "reposted": entry.get("reposted", []), "last_call": bool(entry.get("last_call")),
            })
    return {
        "platform": platform,
        "generated": now.isoformat(),
        "dry_run": dry_run,
        "gap_minutes": round(outcome.gap.total_seconds() / 60, 1) if outcome.gap is not None else None,
        "pending": len(outcome.pending),
        "stats": stats(ledger, now),
        "planned": [{"at": t.isoformat(), **event_row(e, accounts)} for t, e in plan],
        "would_miss": [event_row(e, accounts) for e in missed],
        "recent": sorted(recent, key=lambda r: r["at"], reverse=True),
        "skipped": sorted(({"url": u, **s} for u, s in ledger.skipped.items()), key=lambda r: r["noticed"], reverse=True),
    }


def write(state, platform, report):
    path = Path(state, "public", f"{platform}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    os.replace(tmp, path)
