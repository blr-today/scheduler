import datetime
import json
import sqlite3

import pytest

from conftest import NOW, make_event
from scheduler.shared import database
from scheduler.shared.calendars import Calendar, account_calendar, load_calendar
from scheduler.shared.events import price, upcoming
from scheduler.shared.ledger import Ledger, LedgerError, adopt, locked, reconcile
from scheduler.shared.pacing import due, gap, in_window
from scheduler.shared.text import clip, details, fit, last_call, tickets


def test_calendar_needs_a_tag_and_no_excluded_tag():
    cal = Calendar(["CURATED"], ["LOW-QUALITY"])
    assert {"keywords": ["CURATED"]} in cal
    assert {"keywords": ["CURATED", "LOW-QUALITY"]} not in cal
    assert {"keywords": ["HIGHAPE"]} not in cal


def test_account_calendar_from_tags_and_types():
    cal = account_calendar({"tags": ["FREE"], "types": ["MusicEvent"], "exclude": ["PRICEY"]}, None)
    assert {"keywords": ["FREE"]} in cal and {"@type": "MusicEvent", "keywords": []} in cal
    assert {"@type": "MusicEvent", "keywords": ["PRICEY"]} not in cal and {"keywords": ["BUDGET"]} not in cal


def test_calendar_falls_back_to_default_excludes():
    files = {
        "_config.yml": "defaults:\n- scope: {path: 'cal/*.md'}\n  values: {excludeTags: '[\"NOTINBLR\"]'}\n",
        "cal/hsr.md": "---\ntags: '[\"HSR\"]'\n---\nbody",
    }
    cal = load_calendar("hsr", files.get)
    assert (cal.tags, cal.exclude) == ({"HSR"}, {"NOTINBLR"})


def test_upcoming_keeps_the_window_and_first_occurrence():
    events = [make_event("a", 72), make_event("a", 50), make_event("soon", 24), make_event("past", -2), make_event("far", 8 * 24)]
    assert [(e["url"], e["_start"]) for e in upcoming(events, NOW)] == [("a", NOW + datetime.timedelta(hours=50))]


def test_price_reads_the_first_number():
    assert [price({"price": p}) for p in ("₹1,200", "500 - 800", "Free", 99, None)] == [1200.0, 500.0, 0.0, 99.0, None]


def test_pacing():
    assert gap(9, NOW) == datetime.timedelta(hours=1)
    last = NOW.replace(hour=13)
    assert due(last, 1, last).astimezone(NOW.tzinfo).strftime("%H:%M") == "16:00"
    # The soonest event stops being postable tomorrow at 14:00, so it is due a day earlier
    assert due(last, 1, last, last + datetime.timedelta(days=1, hours=1)).astimezone(NOW.tzinfo).strftime("%H:%M") == "14:00"
    assert in_window(NOW) and not in_window(NOW.replace(hour=9, minute=59)) and not in_window(NOW.replace(hour=19))


def test_text_helpers():
    assert clip("﻿Hello​  world", 50) == "Hello world"
    offers = [{"@type": "AggregateOffer", "lowPrice": "99", "highPrice": "1499"}, {"name": "10K", "price": "1499"}, {"name": "5K", "price": "799"}]
    assert tickets({"offers": offers}) == "5K ₹799 · 10K ₹1,499"
    lines = details(make_event("w" * 140, 9, organizer={"name": "o" * 200}, offers=[{"price": "300"}], location="Venue"))
    out = fit(lines, 300)
    assert len(out) <= 300 and "🎟️ ₹300" in out and "📍 Venue" in out and "🏠" not in out
    assert last_call({"remainingAttendeeCapacity": 1}) == "Last Call: Only 1 seat left for the event #lastcall"
    assert last_call({}) == "Last Call: Only a few seats left for the event #lastcall"


REF = {"uri": "at://x/post/1", "cid": "c"}


def test_missing_ledger_is_fine_only_for_an_empty_account(tmp_path):
    first = Ledger(tmp_path / "l.json")
    reconcile(first, {}, "bluesky")
    assert first.exists and (tmp_path / "l.json").exists()
    with pytest.raises(LedgerError, match="adopt"):
        reconcile(Ledger(tmp_path / "other.json"), {"a": (REF, NOW)}, "bluesky")


def test_stale_ledger_refuses_to_run(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    ledger.record("a", REF, None, NOW)
    ledger.save()
    with pytest.raises(LedgerError, match="stale"):
        reconcile(Ledger(tmp_path / "l.json"), {"a": (REF, NOW), "b": (REF, NOW)}, "bluesky")


def test_pending_posts_are_settled_against_the_platform(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    ledger.save()
    ledger.begin("made", {}, NOW)
    ledger.begin("lost", {}, NOW)
    reconcile(Ledger(tmp_path / "l.json"), {"made": (REF, NOW)}, "bluesky")
    after = Ledger(tmp_path / "l.json")
    assert after.get("made")["status"] == "posted" and after.get("made")["ref"] == REF
    assert "lost" not in after and after.attempts == {"lost": 1}
    for _ in range(2):
        after.begin("lost", {}, NOW)
        reconcile(after, {"made": (REF, NOW)}, "bluesky")
    assert after.gave_up("lost")


def test_adopt_only_builds_a_missing_ledger(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    adopt(ledger, {"a": (REF, NOW)}, "bluesky")
    assert Ledger(tmp_path / "l.json").posted()["a"]["ref"] == REF
    with pytest.raises(LedgerError, match="already exists"):
        adopt(Ledger(tmp_path / "l.json"), {}, "bluesky")


def test_only_one_run_at_a_time(tmp_path):
    with locked(tmp_path):
        with pytest.raises(LedgerError, match="lock"):
            with locked(tmp_path):
                pass


class Response:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def sqlite_bytes(tmp_path, rows):
    path = tmp_path / "src.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE events (url TEXT, event_json TEXT)")
    conn.executemany("INSERT INTO events VALUES (?, ?)", [(f"u{i}", json.dumps({})) for i in range(rows)])
    conn.commit()
    conn.close()
    return path.read_bytes()


def test_database_is_fetched_once_and_revalidated(tmp_path, monkeypatch):
    calls = []
    good = sqlite_bytes(tmp_path, 2)
    modified = "Tue, 06 Oct 2026 04:00:00 GMT"
    replies = [Response(200, good, {"ETag": '"v1"', "Last-Modified": modified}), Response(304), Response(200, b"garbage", {"ETag": '"v2"'})]

    def get(url, headers, timeout):
        calls.append(headers)
        return replies.pop(0)

    monkeypatch.setattr(database.requests, "get", get)
    path = tmp_path / "state" / "events.db"
    path.parent.mkdir()
    database.fetch("https://x/events.db", path, NOW)
    database.fetch("https://x/events.db", path, NOW)
    assert calls[1]["If-None-Match"] == '"v1"' and calls[1]["If-Modified-Since"] == modified
    with pytest.raises(sqlite3.DatabaseError):
        database.fetch("https://x/events.db", path, NOW)
    assert path.read_bytes() == good


def test_old_database_refuses_to_run(tmp_path, monkeypatch):
    old = Response(200, sqlite_bytes(tmp_path, 1), {"Last-Modified": "Fri, 02 Oct 2026 04:00:00 GMT"})
    monkeypatch.setattr(database.requests, "get", lambda url, headers, timeout: old)
    with pytest.raises(database.StaleDatabase):
        database.fetch("https://x/events.db", tmp_path / "events.db", NOW)
