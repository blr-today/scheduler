import html
import io
import re

from PIL import Image

from ..shared.events import listing
from ..shared.text import clip, details, fit, hashtags, listed_on, place, summary

MAX_SIDE = 1280
MAX_BYTES = 400_000


def text(event):
    """Post body for an Event: Mastodon shows the name as a heading above it, so the title is left out"""
    hosts = listed_on(event)
    parts = [
        fit([(0, "")] + details(event)[1:], 1500),
        clip(summary(event), 500),
        "🔗 " + event["url"],
        "Also on " + ", ".join(hosts[1:]) if hosts[1:] else None,
        " ".join(f"#{tag}" for tag in hashtags(event)),
    ]
    return "\n\n".join(p for p in parts if p)


def event_fields(event):
    """event_* parameters for blr-today/snac2's Event publishing"""
    fields = {"event_name": clip(event.get("name"), 300), "event_start": event["_start"].isoformat(), "event_url": event["url"]}
    if event["_end"] > event["_start"]:
        fields["event_end"] = event["_end"].isoformat()
    if lines := place(event):
        fields["event_location_name"] = lines[0]
        if lines[1:]:
            fields["event_location_address"] = ", ".join(lines[1:])
    geo = next((l.get("geo") for l in listing(event.get("location")) if isinstance(l, dict) and isinstance(l.get("geo"), dict)), {})
    if geo.get("latitude") and geo.get("longitude"):
        fields["event_latitude"], fields["event_longitude"] = str(geo["latitude"]), str(geo["longitude"])
    return fields


def compress(data):
    """Downscale and re-encode as JPEG so instances don't store multi-MB posters"""
    image = Image.open(io.BytesIO(data))
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    if image.mode != "RGB":
        background = Image.new("RGB", image.size, "white")
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
        image = background
    for quality in (82, 70, 55, 40):
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        if out.tell() <= MAX_BYTES:
            break
    return out.getvalue()


def event_link(content):
    """The upstream event URL, which text() always puts after 🔗"""
    match = re.search(r'🔗\s*<a\s[^>]*href="([^"]+)"', content or "")
    return html.unescape(match.group(1)) if match else None
