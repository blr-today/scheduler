import datetime
import io

from PIL import Image

from conftest import NOW, make_event
from scheduler.fedi.posts import MAX_BYTES, MAX_SIDE, compress, event_fields, event_link, text
from scheduler.fedi.publisher import FediReposter, Feed, LastCaller, mirror_order, published
from scheduler.shared.calendars import Calendar
from scheduler.shared.ledger import Ledger
from scheduler.shared.publish import publish


class FakeMastodon:
    """Stands in for a Mastodon-API account: remembers statuses and boosts"""

    def __init__(self, account, statuses=()):
        self.account, self.id, self.posted, self.boosted = account, account, [], []
        self.existing = list(statuses)

    def statuses(self, since=None):
        mine = [{"id": s["id"], "url": s["url"], "account": {"id": self.id}, "created_at": "2026-10-06T04:30:00Z",
                 "content": f'🔗 <a href="{s["link"]}">x</a>', "in_reply_to_id": s["reply_to"]} for s in self.posted]
        return iter(self.existing + mine)

    def media(self, data, description):
        raise AssertionError("no images in tests")

    def post(self, status, media_ids=(), reply_to=None, extra=None):
        link = status.split("🔗 ")[1].split("\n")[0] if "🔗 " in status else None
        n = len(self.posted) + 1
        self.posted.append({"id": f"{self.account}{n}", "url": f"https://fedi/{self.account}/{n}", "status": status,
                            "extra": extra or {}, "reply_to": reply_to, "link": link})
        return {"id": f"{self.account}{n}", "url": f"https://fedi/{self.account}/{n}"}

    def reblogged(self, status_id):
        return status_id in self.boosted

    def reblog(self, status_id):
        self.boosted.append(status_id)


def test_text_skips_the_title_mastodon_shows_as_a_heading():
    e = make_event("https://a.example/e", 9, ["INDIRANAGAR"], sameAs=["https://b.example/e"], description="<p>Fun</p>", **{"@type": "MusicEvent"})
    assert text(e) == ("Music event\n🗓️ Tue, 6 Oct · 7:00 PM\n\nFun\n\n🔗 https://a.example/e\n\nAlso on b.example\n\n"
                       "#Bengaluru #Bangalore #Indiranagar #Music")


def test_event_fields_carry_times_place_and_upstream_link():
    e = make_event("https://a.example/e", 9, location={"name": "BFlat", "address": {"streetAddress": "Indiranagar"}, "geo": {"latitude": 12.97, "longitude": 77.64}})
    e["_end"] = e["_start"] + datetime.timedelta(hours=2)
    assert event_fields(e) == {
        "event_name": "HTTPS://A.EXAMPLE/E", "event_start": "2026-10-06T19:00:00+05:30", "event_end": "2026-10-06T21:00:00+05:30",
        "event_url": "https://a.example/e", "event_location_name": "BFlat", "event_location_address": "Indiranagar",
        "event_latitude": "12.97", "event_longitude": "77.64",
    }


def test_compress_downscales_and_flattens_to_jpeg():
    raw = io.BytesIO()
    Image.effect_noise((3000, 2000), 80).convert("RGBA").save(raw, "PNG")
    out = Image.open(io.BytesIO(data := compress(raw.getvalue())))
    assert out.format == "JPEG" and max(out.size) == MAX_SIDE and len(data) <= MAX_BYTES


def test_published_reads_boost_wrappers_and_skips_replies_and_others():
    mine = {"id": "1", "url": "u1", "account": {"id": "events"}, "created_at": "2026-10-06T04:00:00Z", "content": '🔗 <a href="https://a/e">e</a>'}
    boosted = {"id": "9", "account": {"id": "curated"}, "content": "", "reblog": {**mine, "id": "2", "url": "u2", "content": '🔗 <a href="https://b/e">e</a>'}}
    reply = {**mine, "id": "3", "in_reply_to_id": "1", "content": '🔗 <a href="https://c/e">e</a>'}
    found = published(FakeMastodon("events", [mine, boosted, reply]), NOW)
    assert {u: r["id"] for u, (r, _) in found.items()} == {"https://a/e": "1", "https://b/e": "2"}
    assert event_link('x 🔗 <a href="https://a/e?x=1&amp;y=2">') == "https://a/e?x=1&y=2"


def test_mirror_order_follows_the_bluesky_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger" / "bluesky.json")
    ledger.record("b", {}, None, NOW - datetime.timedelta(minutes=5))
    ledger.record("a", {}, None, NOW - datetime.timedelta(minutes=10))
    ledger.record("old", {}, None, NOW - datetime.timedelta(days=40))
    ledger.save()
    assert mirror_order(tmp_path, NOW) == {"a": 0, "b": 1}
    assert mirror_order(tmp_path / "nowhere", NOW) == {}


def test_mirror_run_posts_events_boosts_and_last_calls(tmp_path):
    feed, curated, lastcall = FakeMastodon("events"), FakeMastodon("curated"), FakeMastodon("lastcall")
    events = [make_event("https://x/a", 53, ["CURATED", "LASTCALL"], remainingAttendeeCapacity=3), make_event("https://x/b", 54), make_event("https://x/skip", 55)]
    order = {"https://x/b": 0, "https://x/a": 1}
    ledger = Ledger(tmp_path / "fedi.json")
    for _ in range(2):
        publish(Feed(feed), [FediReposter(curated, Calendar(["CURATED"]), NOW)], events, [], NOW, Ledger(tmp_path / "fedi.json"), "fedi", order=order, caller=LastCaller(lastcall))
    assert [p["link"] for p in feed.posted] == ["https://x/b", "https://x/a"]
    assert feed.posted[1]["extra"]["event_start"] == "2026-10-08T15:00:00+05:30"
    assert curated.boosted == ["events2"]
    assert len(lastcall.posted) == 1 and lastcall.posted[0]["reply_to"] == "events2"
