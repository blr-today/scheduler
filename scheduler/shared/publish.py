import datetime
from collections import defaultdict

from .corrections import changes, facts
from .events import LEAD, cancelled, parse_time, sold_out, tracked, upcoming
from .ledger import reconcile
from .pacing import due, in_window
from .text import last_call

HISTORY = datetime.timedelta(days=30)
MAX_PER_RUN = 4
MAX_CORRECTIONS = 10


class Reposter:
    """Shares the feed's posts of events on one calendar, at most cap new ones per run"""

    def __init__(self, name, calendar, now, cap=MAX_PER_RUN):
        self.name, self.calendar, self.now, self.left = name, calendar, now, cap

    def offer(self, entry, event):
        if self.name in entry["reposted"] or self.left <= 0 or event["_start"] < self.now or event not in self.calendar:
            return
        if self.share(entry["ref"]):
            self.left -= 1
            print(f"Reposted on {self.name}: {event.get('name')}")
        entry["reposted"].append(self.name)

    def share(self, ref):
        """Repost ref unless the platform shows it was already reposted; True if it reposted"""
        raise NotImplementedError


def share(reposters, entry, event):
    for reposter in reposters:
        try:
            reposter.offer(entry, event)
        except RuntimeError as e:
            print(f"warning: {reposter.name} could not repost {event['url']}: {e}")


def announce_last_calls(caller, ledger, live, now):
    """One "Last Call" reply, from a separate mutable account, per posted event ingest tagged LASTCALL"""
    sent, done = 0, caller.called(now)
    for event in live:
        entry = ledger.get(event["url"])
        if not entry or entry.get("status") != "posted" or entry.get("last_call") or "LASTCALL" not in (event.get("keywords") or []):
            continue
        if event["_start"] < now or sold_out(event):
            continue
        if event["url"] in done:
            entry["last_call"] = True
            continue
        if sent >= MAX_CORRECTIONS:
            break
        caller.reply(entry["ref"], event)
        entry["last_call"], sent = True, sent + 1
        ledger.save()
        print(f"Last call: {event.get('name')} ({last_call(event)})")


def follow(ledger, window, now):
    """The occurrence each posted URL is about: the one at its posted start time, else its next one"""
    sessions = defaultdict(list)
    for event in window:
        sessions[event["url"]].append(event)
    followed = {}
    for url, entry in ledger.posted().items():
        if not sessions.get(url):
            continue
        posted = parse_time((entry.get("facts") or {}).get("start"))
        same = next((e for e in sessions[url] if posted and e["_start"] == posted), None)
        if same or not posted or posted > now:
            followed[url] = same or sessions[url][0]
    return followed


def correct(feed, ledger, followed, now):
    corrected = 0
    for url, event in followed.items():
        entry = ledger.get(url)
        if entry["facts"] is None:
            entry["facts"] = facts(event)
            continue
        lines = changes(event, entry["facts"])
        if not lines or parse_time(entry["facts"]["start"]) <= now or corrected >= MAX_CORRECTIONS:
            continue
        feed.correct(entry["ref"], event, lines)
        entry["facts"], corrected = facts(event), corrected + 1
        ledger.save()
        print(f"Corrected {event.get('name')}: {'; '.join(lines)}")


def publish(feed, reposters, events, excluded, now, ledger, name, cap=MAX_PER_RUN, order=None, caller=None):
    """Post due events, correct changed ones and let reposters share theirs

    Posts go out between LEAD and WINDOW before an event, while corrections, reposts, Last Calls
    and calendar records follow every occurrence until it starts.
    With order (url -> rank), only those events are posted, in that order and without pacing.
    Raises LedgerError, before posting anything, when the ledger disagrees with the platform.
    """
    reconcile(ledger, feed.published(now), name)
    window = [e for e in tracked(events, now) if not any(e in c for c in excluded)]
    # Calendar records follow every occurrence and cancellations too
    feed.sync(window, now)
    followed = follow(ledger, window, now)
    correct(feed, ledger, followed, now)
    live = [e for e in followed.values() if not cancelled(e)]
    # Sold-out events still get corrections and reposts, but are never posted fresh
    pending = [e for e in upcoming(window, now) if e["url"] not in ledger and not sold_out(e) and not ledger.gave_up(e["url"])]
    if order is not None:
        pending = sorted((e for e in pending if e["url"] in order), key=lambda e: order[e["url"]])
    last, posted, open_now = ledger.last_posted(), 0, in_window(now)
    print(f"{name}: {len(window)} tracked, {len(ledger.posted())} posted, {len(pending)} pending, {'posting' if open_now else 'outside posting hours'}")
    while open_now and pending and posted < cap:
        if order is None and due(last, len(pending), now, pending[0]["_start"] - LEAD) > now:
            break
        event = pending.pop(0)
        ledger.begin(event["url"], facts(event), now)
        # A failure leaves the entry pending, and the next run settles it against the platform
        ref = feed.post(event, now)
        ledger.commit(event["url"], ref, now)
        last, posted = now, posted + 1
        print(f"{name}: posted {event.get('name')} ({event['url']})")
        share(reposters, ledger.get(event["url"]), event)
        live.append(event)
        ledger.save()
    # Catch up on reposts missed by earlier runs or newly added accounts
    for event in live:
        share(reposters, ledger.get(event["url"]), event)
    if caller and open_now:
        announce_last_calls(caller, ledger, live, now)
    ledger.save()
    return posted
