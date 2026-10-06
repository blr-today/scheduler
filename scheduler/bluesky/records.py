import datetime
import hashlib
import re

from ..shared.events import iso, listing, tail
from ..shared.http import fetch_image
from ..shared.text import clip, details, fit, image_url, last_call, listed_on, place, summary

MAX_BLOB = 1_000_000
POST = "app.bsky.feed.post"
REPOST = "app.bsky.feed.repost"
EVENT = "community.lexicon.calendar.event"
MODES = {"OfflineEventAttendanceMode": "inperson", "OnlineEventAttendanceMode": "virtual", "MixedEventAttendanceMode": "hybrid"}
STATUSES = {"EventScheduled": "scheduled", "EventCancelled": "cancelled", "EventPostponed": "postponed", "EventRescheduled": "rescheduled"}


def text(event):
    return fit(details(event), 300)


def thumbnail(client, event):
    if not (url := image_url(event)):
        return None
    for data, mime in fetch_image(url):
        if len(data) <= MAX_BLOB:
            try:
                return client.upload(data, mime)
            except RuntimeError as e:
                # Unconfirmed accounts cannot upload blobs, so post without a thumbnail
                print(f"warning: {e}")
                return None
    return None


def post_record(client, event, now):
    external = {"uri": event["url"], "title": clip(event.get("name"), 300), "description": clip(summary(event), 300)}
    if thumb := thumbnail(client, event):
        external["thumb"] = thumb
    return {
        "$type": POST,
        "text": text(event),
        "createdAt": iso(now),
        "langs": ["en"],
        "embed": {"$type": "app.bsky.embed.external", "external": external},
    }


def reply_record(ref, text):
    return {"$type": POST, "text": text, "createdAt": iso(datetime.datetime.now(datetime.UTC)), "langs": ["en"], "reply": {"root": ref, "parent": ref}}


def hashtag_facets(text):
    """Rich-text facets so #tags in text are real hashtags, with UTF-8 byte offsets"""
    facets = []
    for match in re.finditer(r"#(\w+)", text):
        start = len(text[: match.start()].encode())
        end = start + len(match.group(0).encode())
        facets.append({"index": {"byteStart": start, "byteEnd": end}, "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": match.group(1)}]})
    return facets


def last_call_record(ref, event):
    message = last_call(event)
    record = reply_record(ref, message) | {"facets": hashtag_facets(message)}
    record["embed"] = {"$type": "app.bsky.embed.external", "external": {"uri": event["url"], "title": clip(event.get("name"), 300), "description": ""}}
    return record


def event_key(event):
    return hashlib.sha256(f"{event['url']}|{event['_start'].isoformat()}".encode()).hexdigest()[:24]


def event_record(event):
    """community.lexicon.calendar.event that points people to the upstream listing instead of RSVPs"""
    lines = place(event)
    record = {
        "$type": EVENT,
        "name": clip(event.get("name"), 300),
        "description": clip(summary(event), 3000),
        "startsAt": iso(event["_start"]),
        "rsvpExpected": False,
        "uris": [{"$type": f"{EVENT}#uri", "uri": event["url"], "name": "Details on " + (listed_on(event) or ["the host's site"])[0]}],
    }
    if event["_end"] > event["_start"]:
        record["endsAt"] = iso(event["_end"])
    if mode := MODES.get(tail(event.get("eventAttendanceMode"))):
        record["mode"] = f"{EVENT}#{mode}"
    if status := STATUSES.get(tail(event.get("eventStatus"))):
        record["status"] = f"{EVENT}#{status}"
    if lines:
        record["locations"] = [{"$type": "community.lexicon.location.address", "country": "IN", "locality": "Bengaluru", "name": lines[0], **({"street": ", ".join(lines[1:])} if lines[1:] else {})}]
        geo = next((l.get("geo") for l in listing(event.get("location")) if isinstance(l, dict) and isinstance(l.get("geo"), dict)), None)
        if geo and geo.get("latitude") and geo.get("longitude"):
            record["locations"].append({"$type": "community.lexicon.location.geo", "latitude": str(geo["latitude"]), "longitude": str(geo["longitude"]), "name": lines[0]})
    return record
