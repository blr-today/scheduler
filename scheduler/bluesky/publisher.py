import datetime
import json
import os
from pathlib import Path

from ..shared.calendars import account_calendar, load_calendar
from ..shared.events import iso, parse_time
from ..shared.ledger import Ledger, adopt
from ..shared.publish import HISTORY, Reposter, publish
from ..shared.text import fit
from .client import Client, DryClient
from .records import EVENT, POST, REPOST, event_key, event_record, last_call_record, post_record, reply_record

NAME = "bluesky"


def published(client, now, full=False):
    """Event URL -> (post ref, created) for the account's top-level posts"""
    posts = {}
    for record in client.records(POST, None if full else now - HISTORY):
        value = record["value"]
        uri = (value.get("embed") or {}).get("external", {}).get("uri")
        if uri and "reply" not in value:
            posts.setdefault(uri, ({"uri": record["uri"], "cid": record["cid"]}, parse_time(value.get("createdAt"))))
    return posts


def sync_calendar(client, events, now, cap=200):
    """Upsert one calendar record per event occurrence, keyed by URL and start time"""
    existing = {r["uri"].rsplit("/", 1)[-1]: r["value"] for r in client.records(EVENT)}
    writes = 0
    for event in events:
        rkey, record = event_key(event), event_record(event)
        old = existing.get(rkey)
        if old and {k: v for k, v in old.items() if k != "createdAt"} == record:
            continue
        if writes >= cap:
            break
        client.put(EVENT, rkey, {**record, "createdAt": old.get("createdAt") if old else iso(now)})
        writes += 1
    print(f"{NAME}: {writes} calendar records written, {len(existing)} existed")


class Feed:
    def __init__(self, client):
        self.client = client

    def sync(self, wanted, now):
        sync_calendar(self.client, wanted, now)

    def published(self, now, full=False):
        return published(self.client, now, full)

    def post(self, event, now):
        res = self.client.create(POST, post_record(self.client, event, now))
        return {"uri": res["uri"], "cid": res["cid"]}

    def correct(self, ref, event, lines):
        """Bluesky posts cannot be edited, so corrections are replies to the original post"""
        self.client.create(POST, reply_record(ref, fit([(0, "✏️ Update")] + [(i + 1, line) for i, line in enumerate(lines)], 300)))


class BlueskyReposter(Reposter):
    def __init__(self, client, calendar, now):
        super().__init__(client.handle, calendar, now)
        self.client = client
        self.done = {r["value"]["subject"]["uri"] for r in client.records(REPOST, now - HISTORY)}

    def share(self, ref):
        if ref["uri"] in self.done:
            return False
        self.client.create(REPOST, {"$type": REPOST, "subject": ref, "createdAt": iso(datetime.datetime.now(datetime.UTC))})
        self.done.add(ref["uri"])
        return True


class LastCaller:
    """Separate account for Last Call replies, so people can mute it or #lastcall"""

    def __init__(self, client):
        self.client = client

    def called(self, now):
        return {(r["value"].get("embed") or {}).get("external", {}).get("uri") for r in self.client.records(POST, now - HISTORY)}

    def reply(self, ref, event):
        self.client.create(POST, last_call_record(ref, event))


def ledger_path(state):
    return Path(state, "ledger", f"{NAME}.json")


def run(config, events, state, now, get, dry_run=False, adopting=False):
    passwords = json.loads(os.environ.get("BLUESKY_APP_PASSWORDS") or "{}")
    cls = DryClient if dry_run else Client

    def client(handle):
        return cls(config["pds"], handle, None if dry_run else passwords[handle])

    ledger = Ledger(ledger_path(state), readonly=dry_run)
    feed = client(config["feed"]["handle"])
    if adopting:
        return adopt(ledger, published(feed, now, full=True), NAME)
    excluded = [load_calendar(name, get) for name in config["feed"]["exclude"]]
    reposters = [BlueskyReposter(client(r["handle"]), account_calendar(r, get), now) for r in config["reposters"]]
    caller = None
    if handle := (config.get("last_call") or {}).get("handle"):
        if dry_run or handle in passwords:
            caller = LastCaller(client(handle))
        else:
            print(f"{NAME}: no app password for {handle}, skipping last calls")
    return publish(Feed(feed), reposters, events, excluded, now, ledger, NAME, caller=caller)
