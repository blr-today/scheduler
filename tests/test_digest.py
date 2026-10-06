import json

from conftest import NOW, make_event
from scheduler.digest import explain, gate, literal, matches, render, week
from scheduler.shared.calendars import Calendar

OPTS = [
    {"id": "indiranagar", "title": "Indiranagar", "tags": ["INDIRANAGAR"]},
    {"id": "music", "title": "Music", "types": ["MusicEvent"]},
    {"id": "pricey", "title": "Pricey", "tags": ["PRICEY"]},
]


def test_events_match_choices_by_tag_or_type():
    e = make_event("a", 1, ["INDIRANAGAR", "PRICEY"], **{"@type": "MusicEvent"})
    assert matches(e, OPTS) == ["indiranagar", "music", "pricey"]
    assert matches(make_event("b", 1, ["HSR"]), OPTS) == []


def test_curated_events_only_check_never():
    assert gate([], True) == "true" and gate([], False) is None
    assert gate(["free"], True) == '(not (or (hasKey $n "free")))'
    assert gate(["free", "hsr"], False) == '(and (not (or (hasKey $n "free") (hasKey $n "hsr"))) (or (hasKey $a "free") (hasKey $a "hsr")))'


def test_titles_cannot_inject_template_actions():
    assert literal("{{ .Subscriber.Email }} <b>") == '{{"{{"}} .Subscriber.Email }} &lt;b&gt;'


def test_week_dedupes_and_drops_excluded():
    events = [make_event("a", 30), make_event("a", 5), make_event("junk", 2, ["NOTINBLR"]), make_event("late", 8 * 24)]
    picked = week(events, NOW, [Calendar(["NOTINBLR"])])
    assert [(e["url"], e["_start"]) for e in picked] == [("a", events[1]["_start"])]


def test_uncurated_events_without_choices_are_left_out():
    curated = Calendar(["CURATED"])
    body = render([make_event("https://x/c", 3, ["CURATED"]), make_event("https://x/u", 4, ["HIGHAPE"])], OPTS, curated)
    assert "https://x/c" in body and "https://x/u" not in body
    assert "{{ UnsubscribeURL }}" in body and "{{ .Subscriber.UUID }}" in body


def test_the_email_explains_the_choices_like_the_page():
    text = explain(OPTS, 9)
    assert '(dict "indiranagar" "Indiranagar" "music" "Music" "pricey" "Pricey")' in text
    assert "Here are {{ $count }} of this week's 9 events: our picks" in text
    assert "plus all the" in text and "We left out anything" in text and " or {{ last $nl }}" in text
