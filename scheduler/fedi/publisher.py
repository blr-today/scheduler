import json
import os
from pathlib import Path

from ..shared import report
from ..shared.calendars import account_calendar, load_calendar
from ..shared.events import parse_time
from ..shared.http import fetch_image
from ..shared.ledger import Ledger, adopt
from ..shared.publish import HISTORY, Reposter, publish
from ..shared.text import clip, image_url, last_call
from .client import Client, DryClient
from .posts import compress, event_fields, event_link, text

NAME = "fedi"


def ref_of(status):
    """The API id to act on, and the stable ActivityPub id, which is also the post's web page on snac"""
    return {"id": status["id"], "url": status.get("uri") or status.get("url")}


def published(client, now, full=False, known=None):
    """Event URL -> (status ref, created) for the account's own top-level statuses

    known maps a status's ActivityPub id to the event URL the ledger has for it; any other status
    is looked up in its Event object, whose url is the upstream listing.
    """
    posts, known = {}, known or {}
    for status in client.statuses(None if full else now - HISTORY):
        # Boosts of the feed's statuses show up as wrappers around the original
        original = status.get("reblog") or status
        if str((original.get("account") or {}).get("id", client.id)) != str(client.id) or original.get("in_reply_to_id"):
            continue
        ref = ref_of(original)
        if url := known.get(ref["url"]) or event_link(original.get("content")) or client.event_url(ref["url"]):
            posts.setdefault(url, (ref, parse_time(original.get("created_at"))))
    return posts


class Feed:
    def __init__(self, client, known=None):
        self.client, self.known = client, known or {}

    def sync(self, wanted, now):
        """The posts themselves are ActivityPub Events, so there are no separate calendar records"""

    def published(self, now, full=False):
        return published(self.client, now, full, self.known)

    def post(self, event, now):
        media = []
        if url := image_url(event):
            for data, _ in fetch_image(url):
                try:
                    media.append(self.client.media(compress(data), clip(f"Poster for {event.get('name')}", 1500)))
                    break
                except (OSError, RuntimeError) as e:
                    print(f"warning: image for {event['url']}: {e}")
        return ref_of(self.client.post(text(event), media, extra=event_fields(event)))

    def correct(self, ref, event, lines):
        """Corrections are replies, as on Bluesky, since snac edits of older posts are unreliable"""
        self.client.post("✏️ Update\n\n" + "\n".join(lines), reply_to=ref["id"])


class FediReposter(Reposter):
    def __init__(self, client, calendar, now):
        super().__init__(client.account, calendar, now)
        self.client = client

    def share(self, ref):
        if self.client.reblogged(ref["id"]):
            return False
        self.client.reblog(ref["id"])
        return True


class LastCaller:
    """Separate account for Last Call replies, so people can mute it or #lastcall"""

    def __init__(self, client):
        self.client = client

    def called(self, now):
        return {event_link(s.get("content")) for s in self.client.statuses(now - HISTORY)}

    def reply(self, ref, event):
        self.client.post(f"{last_call(event)}\n\n🔗 {event['url']}", reply_to=ref["id"])


def mirror_order(state, now):
    """Rank of each event URL the Bluesky firehose posted recently, oldest first, read from its ledger"""
    path = Path(state, "ledger", "bluesky.json")
    if not path.exists():
        return {}
    posted = Ledger(path, readonly=True).posted()
    recent = sorted((e["posted"], url) for url, e in posted.items() if now - parse_time(e["posted"]) <= HISTORY)
    return {url: rank for rank, (_, url) in enumerate(recent)}


def run(config, events, state, now, get, dry_run=False, adopting=False, limit=None):
    tokens = json.loads(os.environ.get("FEDI_TOKENS") or "{}")
    if not tokens and not dry_run:
        print(f"{NAME}: FEDI_TOKENS not set, skipping")
        return None
    cls = DryClient if dry_run else Client

    def client(account):
        return cls(config["server"], account, tokens.get(account) if dry_run else tokens[account])

    ledger = Ledger(Path(state, "ledger", f"{NAME}.json"), readonly=dry_run)
    feed = client(config["feed"]["account"])
    if adopting:
        return adopt(ledger, published(feed, now, full=True), NAME)
    excluded = [load_calendar(name, get) for name in config["feed"]["exclude"]]
    accounts = {r["account"]: account_calendar(r, get) for r in config["reposters"]}
    reposters = [FediReposter(client(account), calendar, now) for account, calendar in accounts.items()]
    caller = LastCaller(client(config["last_call"]["account"])) if config.get("last_call") else None
    # Replicate what the Bluesky firehose posted instead of pacing independently
    order = mirror_order(state, now) if config.get("mirror") == "bluesky" else None
    known = {e["ref"]["url"]: url for url, e in ledger.posted().items()}
    outcome = publish(Feed(feed, known), reposters, events, excluded, now, ledger, NAME, order=order, caller=caller, limit=limit)
    report.write(state, NAME, report.build(NAME, outcome, ledger, accounts, now, lambda ref: ref.get("url"), dry_run))
    return outcome
