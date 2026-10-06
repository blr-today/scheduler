import datetime
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .events import parse_time

MAX_ATTEMPTS = 3


class LedgerError(Exception):
    """The ledger can't be trusted, so nothing may be posted until a human looks"""


class Ledger:
    """Durable record of what one platform has posted; the only thing that decides whether to post

    Each post is written ahead as pending before the platform call, so a crash leaves
    evidence that the next run reconciles against the platform instead of guessing.
    """

    def __init__(self, path, readonly=False):
        self.path, self.readonly = Path(path), readonly
        self.exists = self.path.exists()
        data = json.loads(self.path.read_text()) if self.exists else {}
        if self.exists and data.get("version") != 1:
            raise LedgerError(f"{self.path} has an unknown format")
        self.entries = data.get("entries", {})
        self.attempts = data.get("attempts", {})
        # When each unposted event first became postable, and why some never were
        self.seen = data.get("seen", {})
        self.skipped = data.get("skipped", {})

    def __contains__(self, url):
        return url in self.entries

    def get(self, url):
        return self.entries.get(url)

    def posted(self):
        return {url: e for url, e in self.entries.items() if e.get("status") == "posted"}

    def pending(self):
        return [url for url, e in self.entries.items() if e.get("status") == "pending"]

    def gave_up(self, url):
        return self.attempts.get(url, 0) >= MAX_ATTEMPTS

    def record(self, url, ref, facts, posted):
        self.entries[url] = {"status": "posted", "ref": ref, "facts": facts, "posted": posted.isoformat(), "reposted": []}

    def begin(self, url, facts, now):
        self.entries[url] = {"status": "pending", "facts": facts, "started": now.isoformat(), "reposted": []}
        self.save()

    def commit(self, url, ref, posted):
        self.entries[url].update(status="posted", ref=ref, posted=posted.isoformat())
        self.attempts.pop(url, None)
        self.save()

    def abandon(self, url):
        """A pending post that never reached the platform: count the attempt and allow a retry"""
        del self.entries[url]
        self.attempts[url] = self.attempts.get(url, 0) + 1
        self.save()

    def skip(self, url, event, reason, now):
        if url not in self.skipped:
            self.skipped[url] = {
                "reason": reason,
                "name": event.get("name"),
                "starts": event["_start"].isoformat(),
                "postable_since": self.seen.get(url),
                "noticed": now.isoformat(),
            }
            print(f"skipped, {reason}: {event.get('name')} ({url})")
        self.seen.pop(url, None)

    def forget_before(self, cutoff):
        for url in [u for u, at in self.seen.items() if parse_time(at) < cutoff]:
            del self.seen[url]
        for url in [u for u, s in self.skipped.items() if parse_time(s["noticed"]) < cutoff]:
            del self.skipped[url]

    def last_posted(self):
        return max((parse_time(e["posted"]) for e in self.posted().values()), default=None)

    def save(self):
        if self.readonly:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            state = {"version": 1, "entries": self.entries, "attempts": self.attempts, "seen": self.seen, "skipped": self.skipped}
            json.dump(state, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self.exists = True


def reconcile(ledger, posts, name):
    """Refuse to run on a missing or stale ledger, and settle posts left pending by a crash

    posts maps each event URL the platform shows in recent history to (ref, created).
    """
    if not ledger.exists:
        if posts:
            raise LedgerError(f"{name}: no ledger at {ledger.path}, but the account has {len(posts)} event posts; run `scheduler adopt {name}` once")
        print(f"{name}: first run, starting an empty ledger")
        ledger.save()
    for url in ledger.pending():
        if url in posts:
            ref, created = posts[url]
            print(f"{name}: post for {url} was made before a crash, adopting it")
            ledger.commit(url, ref, created or parse_time(ledger.get(url)["started"]))
        else:
            print(f"{name}: post for {url} never reached the platform, will retry")
            ledger.abandon(url)
    unknown = sorted(url for url in posts if url not in ledger)
    if unknown:
        raise LedgerError(f"{name}: stale ledger, the account has {len(unknown)} event posts it doesn't know, e.g. {unknown[:3]}")


def adopt(ledger, posts, name):
    """Build a ledger from the platform's full history, for a deliberate first deploy"""
    if ledger.exists:
        raise LedgerError(f"{name}: {ledger.path} already exists; adopt only builds a missing ledger")
    for url, (ref, created) in posts.items():
        ledger.record(url, ref, None, created or datetime.datetime.fromtimestamp(0, datetime.UTC))
    ledger.save()
    print(f"{name}: adopted {len(posts)} posts into {ledger.path}")


@contextmanager
def locked(state):
    """One run at a time per state directory, even if the CronJob guard fails"""
    Path(state).mkdir(parents=True, exist_ok=True)
    with open(Path(state, "lock"), "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LedgerError(f"another run holds {state}/lock")
        yield
