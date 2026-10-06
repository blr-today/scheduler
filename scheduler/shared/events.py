import datetime
import json
import re
import sqlite3

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
WINDOW = datetime.timedelta(days=14)
LEAD = datetime.timedelta(hours=1)
SKIP_STATUS = {"EventCancelled"}


def parse_time(value):
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=IST)


def iso(moment):
    return moment.astimezone(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_events(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for url, raw in conn.execute("SELECT url, event_json FROM events"):
            event = json.loads(raw)
            start = parse_time(event.get("startDate"))
            if start:
                end = parse_time(event.get("endDate")) or start
                yield {**event, "url": event.get("url") or url, "_start": start, "_end": max(start, end)}
    finally:
        conn.close()


def listing(value):
    if value is None:
        return []
    return [v for item in value for v in listing(item)] if isinstance(value, list) else [value]


def tail(value):
    return value.rsplit("/", 1)[-1] if isinstance(value, str) else ""


def flatten(offers):
    if isinstance(offers, list):
        for offer in offers:
            yield from flatten(offer)
    elif isinstance(offers, dict):
        # District lists unticketed events as zero-priced AggregateOffers
        if "offers" in offers or offers.get("offerCount") in (0, "0"):
            yield from flatten(offers.get("offers"))
        else:
            yield offers


def price(offer):
    value = offer.get("price", offer.get("lowPrice"))
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    if value.strip().lower() == "free":
        return 0.0
    match = re.search(r"\d+(\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else None


def count(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def tickets_left(event):
    """Seats left, read like ingest's LASTCALL processor"""
    left = count(event.get("remainingAttendeeCapacity"))
    if left is not None:
        return left
    offers = list(flatten(event.get("offers")))
    if not offers:
        return None
    total = 0
    for offer in offers:
        if "SoldOut" in str(offer.get("availability")):
            continue
        n = count(offer.get("remainingAttendeeCapacity", offer.get("inventoryLevel")))
        if n is None:
            return None
        total += n
    return total


def cancelled(event):
    return tail(event.get("eventStatus")) in SKIP_STATUS


def sold_out(event):
    return tickets_left(event) == 0


def upcoming(events, now, include_cancelled=False):
    """Earliest occurrence of each URL that starts between LEAD and WINDOW from now"""
    first = {}
    for event in events:
        if cancelled(event) and not include_cancelled:
            continue
        if now + LEAD <= event["_start"] <= now + WINDOW:
            if event["url"] not in first or event["_start"] < first[event["url"]]["_start"]:
                first[event["url"]] = event
    return sorted(first.values(), key=lambda e: e["_start"])
