import datetime

import pytest

from conftest import NOW, FakeClient, make_event
from scheduler.bluesky.publisher import BlueskyReposter, Feed, LastCaller, published
from scheduler.bluesky.records import EVENT, POST, event_record, hashtag_facets, text
from scheduler.shared.calendars import Calendar
from scheduler.shared.corrections import facts
from scheduler.shared.events import IST
from scheduler.shared.ledger import Ledger, LedgerError
from scheduler.shared.publish import publish

REF = {"uri": "at://events/post/1", "cid": "c"}


def posts(client):
    return [r for c, r in client.created if c == POST and "reply" not in r]


def run(client, events, now, ledger, reposters=(), excluded=(), caller=None):
    return publish(Feed(client), list(reposters), events, list(excluded), now, ledger, "bluesky", caller=caller)


def fresh_ledger(tmp_path, **entries):
    ledger = Ledger(tmp_path / "l.json")
    for url, (ref, event) in entries.items():
        ledger.record(url, ref, facts(event), NOW)
    ledger.save()
    return ledger


def test_post_text():
    e = make_event("x", 24 + 9.5, ["FREE"], location={"name": "Champaca"})
    assert text(e) == "X\n\n🗓️ Wed, 7 Oct · 7:30 PM\n📍 Champaca\n🎟️ Free"
    e = make_event("z", 9, maximumAttendeeCapacity=40, organizer={"name": "Courtyard"}, **{"@type": "MusicEvent"})
    e["_end"] = e["_start"] + datetime.timedelta(hours=2)
    assert text(e) == "Z\n\nMusic event\n🗓️ Tue, 6 Oct · 7:00 PM – 9:00 PM\n👥 Capacity 40\n🏠 By Courtyard"


def test_event_record_links_upstream_without_rsvps():
    e = make_event("https://www.example.com/e", 5, location={"name": "Champaca", "address": {"streetAddress": "Vasanth Nagar"}, "geo": {"latitude": 12.98, "longitude": 77.59}}, eventAttendanceMode="https://schema.org/OfflineEventAttendanceMode")
    r = event_record(e)
    assert r["rsvpExpected"] is False and r["mode"] == f"{EVENT}#inperson"
    assert r["uris"][0]["name"] == "Details on example.com"
    assert [l["$type"] for l in r["locations"]] == ["community.lexicon.location.address", "community.lexicon.location.geo"]


def test_first_run_posts_and_reposts(tmp_path):
    feed, curated, hood = FakeClient("events"), FakeClient("curated"), FakeClient("indiranagar")
    reposters = [BlueskyReposter(curated, Calendar(["CURATED"]), NOW), BlueskyReposter(hood, Calendar(["INDIRANAGAR"]), NOW)]
    events = [make_event("junk", 50, ["LOW-QUALITY"]), make_event("b", 53, ["INDIRANAGAR", "CURATED"]), make_event("c", 54, ["CURATED"]), make_event("soon", 5, ["CURATED"])]
    assert run(feed, events, NOW, Ledger(tmp_path / "l.json"), reposters, [Calendar(["LOW-QUALITY"])]).posted == 1
    assert [r["embed"]["external"]["uri"] for r in posts(feed)] == ["b"]
    assert [r["name"] for c, _, r in feed.puts if c == EVENT] == ["SOON", "B", "C"]
    assert len(curated.created) == len(hood.created) == 1
    # The next post waits for its slot, so a run five minutes later posts nothing
    assert run(feed, events, NOW + datetime.timedelta(minutes=5), Ledger(tmp_path / "l.json"), reposters).posted == 0


def test_existing_account_without_ledger_refuses_to_post(tmp_path):
    old = {"uri": "at://events/post/old", "cid": "c", "value": {"createdAt": "2026-10-05T04:00:00Z", "embed": {"external": {"uri": "a"}}}}
    feed = FakeClient("events", {POST: [old]})
    with pytest.raises(LedgerError, match="adopt"):
        run(feed, [make_event("a", 5), make_event("b", 6)], NOW, Ledger(tmp_path / "missing.json"))
    assert feed.created == [] and feed.puts == []


def test_post_on_the_account_missing_from_the_ledger_refuses_to_post(tmp_path):
    ledger = fresh_ledger(tmp_path)
    stray = {"uri": "at://events/post/9", "cid": "c", "value": {"createdAt": "2026-10-06T03:00:00Z", "embed": {"external": {"uri": "b"}}}}
    feed = FakeClient("events", {POST: [stray]})
    with pytest.raises(LedgerError, match="stale"):
        run(feed, [make_event("a", 5)], NOW, ledger)
    assert feed.created == []


def test_crash_mid_post_is_settled_without_a_duplicate(tmp_path):
    feed = FakeClient("events")
    feed.fail_next = True
    with pytest.raises(RuntimeError):
        run(feed, [make_event("a", 53)], NOW, Ledger(tmp_path / "l.json"))
    assert Ledger(tmp_path / "l.json").pending() == ["a"]
    # Nothing reached the platform, so the next run retries it once
    run(feed, [make_event("a", 53)], NOW + datetime.timedelta(minutes=5), Ledger(tmp_path / "l.json"))
    assert len(posts(feed)) == 1 and Ledger(tmp_path / "l.json").get("a")["status"] == "posted"


def test_crash_after_the_platform_accepted_adopts_the_post(tmp_path):
    ledger = fresh_ledger(tmp_path)
    ledger.begin("a", facts(make_event("a", 5)), NOW)
    made = {"uri": "at://events/post/7", "cid": "c", "value": {"createdAt": "2026-10-06T04:30:00Z", "embed": {"external": {"uri": "a"}}}}
    feed = FakeClient("events", {POST: [made]})
    run(feed, [make_event("a", 5)], NOW + datetime.timedelta(minutes=5), Ledger(tmp_path / "l.json"))
    assert posts(feed) == [] and Ledger(tmp_path / "l.json").get("a")["ref"]["uri"] == "at://events/post/7"


def test_nothing_is_posted_outside_posting_hours(tmp_path):
    feed = FakeClient("events")
    run(feed, [make_event("a", 53)], NOW.replace(hour=8), Ledger(tmp_path / "l.json"))
    assert posts(feed) == []
    run(feed, [make_event("a", 53)], NOW, Ledger(tmp_path / "l.json"))
    assert len(posts(feed)) == 1


def test_events_less_than_two_days_away_are_never_posted_fresh(tmp_path):
    feed = FakeClient("events")
    run(feed, [make_event("a", 47), make_event("b", 7 * 24 + 1)], NOW, Ledger(tmp_path / "l.json"))
    assert posts(feed) == []


def simulate(tmp_path, events, days):
    feed, path = FakeClient("events"), tmp_path / "l.json"
    for minute in range(0, days * 24 * 60, 5):
        run(feed, events, NOW + datetime.timedelta(minutes=minute), Ledger(path))
    return {r["embed"]["external"]["uri"]: datetime.datetime.fromisoformat(r["createdAt"]).astimezone(IST) for r in posts(feed)}


def test_a_few_events_are_spread_evenly_until_their_deadline(tmp_path):
    events = [make_event(str(i), 96 + i) for i in range(7)]
    posted = simulate(tmp_path, events, 3)
    times = sorted(posted.values())
    gaps = {round((b - a).total_seconds() / 3600, 1) for a, b in zip(times, times[1:]) if a.date() == b.date()}
    assert len(posted) == 7 and max(gaps) - min(gaps) <= 0.5, gaps
    assert all(posted[e["url"]] <= e["_start"] - datetime.timedelta(days=2) for e in events)


def test_a_flood_of_events_still_all_get_out_in_time(tmp_path):
    events = [make_event(f"bms{i}", 50 + (i % 100)) for i in range(400)]
    posted = simulate(tmp_path, events, 5)
    assert len(posted) == 400
    assert all(posted[e["url"]] <= e["_start"] - datetime.timedelta(days=2) for e in events)


def test_changes_get_one_reply_and_cancelled_records_follow(tmp_path):
    ledger = fresh_ledger(tmp_path, a=(REF, make_event("a", 5, location="Old Venue")))
    moved = make_event("a", 7, location="New Venue")
    feed = FakeClient("events")
    for _ in range(2):
        run(feed, [moved], NOW, Ledger(tmp_path / "l.json"))
    replies = [r for c, r in feed.created if "reply" in r]
    assert [r["text"] for r in replies] == ["✏️ Update\n\n🗓️ Now Tue, 6 Oct · 5:00 PM\n📍 Now at New Venue"]
    gone = make_event("a", 7, location="New Venue", eventStatus="https://schema.org/EventCancelled")
    run(feed, [gone], NOW, Ledger(tmp_path / "l.json"))
    assert feed.created[-1][1]["text"] == "✏️ Update\n\n❌ Cancelled"
    assert feed.puts[-1][2]["status"] == f"{EVENT}#cancelled"


def test_sold_out_gets_an_update_and_is_never_posted_fresh(tmp_path):
    ledger = fresh_ledger(tmp_path, a=(REF, make_event("a", 5)))
    gone = [{"price": "500", "availability": "https://schema.org/SoldOut"}]
    feed = FakeClient("events")
    run(feed, [make_event("a", 5, ["LASTCALL"], offers=gone), make_event("b", 6, offers=gone)], NOW, ledger)
    assert [r["text"] for c, r in feed.created] == ["✏️ Update\n\n🚫 Sold out"]


def test_last_call_comes_once_from_its_own_account(tmp_path):
    ledger = fresh_ledger(tmp_path, a=(REF, make_event("a", 5)))
    hot = make_event("a", 5, ["LASTCALL"], remainingAttendeeCapacity=4)
    feed, lastcall = FakeClient("events"), FakeClient("lastcall")
    for _ in range(2):
        run(feed, [hot], NOW, Ledger(tmp_path / "l.json"), caller=LastCaller(lastcall))
    assert feed.created == [] and len(lastcall.created) == 1
    reply = lastcall.created[0][1]
    assert reply["text"] == "Last Call: Only 4 seats left for the event #lastcall" and reply["reply"]["parent"] == REF
    tag = reply["facets"][0]
    assert reply["text"].encode()[tag["index"]["byteStart"]:tag["index"]["byteEnd"]] == b"#lastcall"
    assert hashtag_facets("🎟️ #x")[0]["index"] == {"byteStart": 8, "byteEnd": 10}


def test_published_skips_replies_and_keeps_creation_time():
    records = [
        {"uri": "at://e/p/1", "cid": "c", "value": {"createdAt": "2026-10-06T04:00:00Z", "embed": {"external": {"uri": "a"}}}},
        {"uri": "at://e/p/2", "cid": "c", "value": {"createdAt": "2026-10-06T05:00:00Z", "reply": {}, "embed": {"external": {"uri": "b"}}}},
    ]
    found = published(FakeClient("events", {POST: records}), NOW)
    assert list(found) == ["a"] and found["a"][1].hour == 4


def test_next_session_of_a_posted_weekly_url_is_not_a_correction(tmp_path):
    ledger = fresh_ledger(tmp_path, weekly=(REF, make_event("weekly", -7 * 24 + 5)))
    feed = FakeClient("events")
    run(feed, [make_event("weekly", 5), make_event("weekly", 7 * 24 + 5)], NOW, ledger)
    assert feed.created == []


def test_every_session_of_a_repeated_url_gets_a_calendar_record(tmp_path):
    feed = FakeClient("events")
    run(feed, [make_event("series", 5), make_event("series", 29), make_event("series", 53)], NOW, Ledger(tmp_path / "l.json"))
    assert len({rkey for c, rkey, _ in feed.puts if c == EVENT}) == 3


def test_priority_calendar_goes_first_within_a_deadline_day(tmp_path):
    events = [make_event("plain", 50), make_event("picked", 53, ["CURATED"]), make_event("later", 100, ["CURATED"])]
    feed = FakeClient("events")
    publish(Feed(feed), [], events, [], NOW, Ledger(tmp_path / "l.json"), "bluesky", priority=[Calendar(["CURATED"])])
    # A later deadline day never jumps ahead, even for a priority calendar
    assert [r["embed"]["external"]["uri"] for r in posts(feed)] == ["picked"]


def test_sold_out_and_expired_events_are_tracked_as_skipped(tmp_path):
    path, feed = tmp_path / "l.json", FakeClient("events")
    hot, quiet = make_event("hot", 50), make_event("quiet", 49)
    run(feed, [hot, quiet], NOW.replace(hour=8), Ledger(path))
    gone = dict(hot, offers=[{"price": "500", "availability": "https://schema.org/SoldOut"}])
    # quiet's posting window closes at 11:00 while the posting hours haven't opened for it
    run(feed, [gone, quiet], NOW.replace(hour=9, minute=55) + datetime.timedelta(hours=1, minutes=10), Ledger(path))
    skipped = Ledger(path).skipped
    assert skipped["hot"]["reason"] == "sold out before posting"
    assert skipped["hot"]["postable_since"] == NOW.replace(hour=8).isoformat()
    assert skipped["quiet"]["reason"] == "posting window closed"


def test_report_shows_plan_recent_posts_and_skips(tmp_path):
    from scheduler.bluesky.publisher import post_link
    from scheduler.shared import report
    path, feed = tmp_path / "l.json", FakeClient("events")
    events = [make_event("a", 53, ["CURATED"]), make_event("b", 60)]
    outcome = run(feed, events, NOW, Ledger(path))
    built = report.build("bluesky", outcome, Ledger(path), {"curated.blr.today": Calendar(["CURATED"])}, NOW, post_link)
    assert [r["url"] for r in built["recent"]] == ["a"] and built["recent"][0]["post"].startswith("https://bsky.app/profile/did:plc:events/post/")
    assert [p["url"] for p in built["planned"]] == ["b"] and built["planned"][0]["accounts"] == []
    report.write(tmp_path, "bluesky", built)
    assert (tmp_path / "public" / "bluesky.json").exists()


class FakeHTTP:
    """Answers XRPC calls like a PDS whose access tokens expire after one use"""

    def __init__(self):
        self.headers, self.logins, self.refreshes, self.valid = {}, 0, 0, set()

    def response(self, status, body):
        import json as _json
        r = type("R", (), {})()
        r.status_code, r.ok, r.headers, r.text = status, status < 400, {}, _json.dumps(body)
        r.json = lambda: body
        return r

    def get(self, url, params=None, timeout=None):
        return self.response(200, {"did": "did:plc:x"})

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        if url.endswith("createSession"):
            self.logins += 1
            return self.issue(f"a{self.logins}")
        if url.endswith("refreshSession"):
            self.refreshes += 1
            return self.issue(f"r{self.refreshes}")
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.valid:
            return self.response(400, {"error": "ExpiredToken"})
        self.valid.discard(token)
        return self.response(200, {"uri": "at://did:plc:x/p/1", "cid": "c"})

    def issue(self, token):
        self.valid.add(token)
        return self.response(200, {"accessJwt": token, "refreshJwt": "refresh", "did": "did:plc:x"})


def test_sessions_are_cached_and_refreshed_instead_of_logging_in(tmp_path, monkeypatch):
    from scheduler.bluesky import client as module
    http = FakeHTTP()
    monkeypatch.setattr(module.requests, "Session", lambda: http)
    first = module.Client("https://pds", "events.blr.today", "pw", tmp_path)
    assert http.logins == 0
    first.create(POST, {})
    second = module.Client("https://pds", "events.blr.today", "pw", tmp_path)
    second.create(POST, {})
    assert (http.logins, http.refreshes) == (1, 1)
    assert oct((tmp_path / "events.blr.today.json").stat().st_mode)[-3:] == "600"


def test_neighbourhood_and_type_go_in_the_tags_field_only(tmp_path):
    from scheduler.bluesky.records import tags
    city = ["Bengaluru", "Bangalore"]
    assert tags(make_event("a", 53, ["INDIRANAGAR", "FREE"], **{"@type": "SportsEvent"})) == city + ["Indiranagar", "Sports"]
    assert tags(make_event("b", 53, ["CBD", "HSR"], **{"@type": "ChildrensEvent"})) == city + ["HSRLayout", "CentralBengaluru", "Kids"]
    assert tags(make_event("c", 53, [], **{"@type": "Event", "additionalType": "TheaterEvent"})) == city + ["Theater"]
    assert tags(make_event("d", 53)) == city
    feed = FakeClient("events")
    run(feed, [make_event("e", 53, ["KORAMANGALA"], **{"@type": "MusicEvent"})], NOW, Ledger(tmp_path / "l.json"))
    post = posts(feed)[0]
    assert post["tags"] == ["Bengaluru", "Bangalore", "Koramangala", "Music"] and "#" not in post["text"]
