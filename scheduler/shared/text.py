import datetime
import html
import re
from urllib.parse import urlparse

from .events import IST, listing, tail, flatten, price, tickets_left

ATTENDANCE = {"OfflineEventAttendanceMode": "In person", "OnlineEventAttendanceMode": "Online", "MixedEventAttendanceMode": "In person and online"}
LANGUAGES = {"en": "English", "hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "mr": "Marathi", "bn": "Bengali", "ur": "Urdu", "de": "German", "fr": "French", "ja": "Japanese"}
NEIGHBOURHOODS = {
    "INDIRANAGAR": "Indiranagar", "KORAMANGALA": "Koramangala", "HSR": "HSRLayout", "JAYANAGAR": "Jayanagar",
    "JPNAGAR": "JPNagar", "WHITEFIELD": "Whitefield", "CBD": "CentralBengaluru", "NORTHBLR": "NorthBengaluru",
}
TYPE_TAGS = {"ChildrensEvent": "Kids", "EducationEvent": "Workshop"}
CITY_TAGS = ["Bengaluru", "Bangalore"]
LAST_CALL = "Last Call"
LAST_CALL_TAG = "#lastcall"


def name_of(value):
    if isinstance(value, dict):
        value = value.get("name")
    return clip(value, 200) if isinstance(value, str) and value.strip() else None


def clip(text, limit):
    text = " ".join(re.sub("[\ufeff\u200b-\u200d\u2060]", "", html.unescape(str(text or ""))).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def humanize(word):
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", word).lower().capitalize()


def summary(event):
    text = str(event.get("description") or "")
    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d)>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def money(amount):
    return "Free" if amount == 0 else f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"


def when(event):
    start, end = event["_start"].astimezone(IST), event["_end"].astimezone(IST)
    day = lambda d: f"{d:%a}, {d.day} {d:%b}"
    clock = lambda d: f"{d:%I:%M %p}".lstrip("0")
    all_day = (start.hour, start.minute) == (0, 0)
    text = day(start) if all_day else f"{day(start)} · {clock(start)}"
    if end <= start:
        return text
    if end.date() == start.date() and not all_day:
        return f"{text} – {clock(end)}"
    if end - start >= datetime.timedelta(days=1):
        return f"{text} – {day(end)}"
    return text if all_day else f"{text} – {day(end)} {clock(end)}"


def place(event):
    for location in listing(event.get("location")):
        if isinstance(location, str) and location.strip():
            return [clip(location, 200)]
        if isinstance(location, dict) and location.get("@type") != "VirtualLocation":
            lines = [name_of(location)]
            address = location.get("address")
            if isinstance(address, dict):
                lines += [address.get("streetAddress"), address.get("addressLocality")]
            elif isinstance(address, str):
                lines.append(address)
            out = []
            for line in filter(None, (clip(x, 200) for x in lines if isinstance(x, str) and x != "NA")):
                if not any(line.lower() in o.lower() for o in out):
                    out.append(line)
            return out
    return []


def tickets(event):
    tiers, seen = [], set()
    for offer in flatten(event.get("offers")):
        amount = price(offer)
        label = name_of(offer.get("name")) or name_of(offer.get("category"))
        low, high = price({"price": offer.get("lowPrice")}), price({"price": offer.get("highPrice")})
        if low is not None and high is not None and low != high:
            text = f"{money(low)}–{money(high)}"
        elif amount is not None:
            text = money(amount)
        else:
            continue
        key = (label, text)
        if key not in seen:
            seen.add(key)
            tiers.append((amount or 0, label, text))
    if any(label for _, label, _ in tiers):
        tiers = [t for t in tiers if t[1]]
    if not tiers:
        return "Free" if "FREE" in (event.get("keywords") or []) or event.get("isAccessibleForFree") in (True, "true") else None
    tiers.sort(key=lambda t: t[0])
    if len(tiers) == 1:
        return tiers[0][2]
    return " · ".join(f"{clip(label, 40)} {text}" if label else text for _, label, text in tiers)


def people(value):
    return ", ".join(dict.fromkeys(filter(None, map(name_of, listing(value)))))


def languages(value):
    names = (v if isinstance(v, str) else name_of(v) for v in listing(value))
    return ", ".join(dict.fromkeys(LANGUAGES.get(n.lower().split("-")[0], n) for n in names if n))


def short_place(lines):
    parts, out = ", ".join(lines[:2]).replace(": ", ", ").split(", "), []
    for part in parts:
        if out and len(", ".join(out + [part])) > 70:
            break
        out.append(part)
    return ", ".join(out)


def details(event):
    """Stable facts from the website's event modal, as (priority, line) in display order"""
    kind = event.get("@type") if isinstance(event.get("@type"), str) else "Event"
    kind = event.get("additionalType") if kind == "Event" and isinstance(event.get("additionalType"), str) else kind
    status = tail(event.get("eventStatus"))
    mode = ATTENDANCE.get(tail(event.get("eventAttendanceMode")))
    facts = [humanize(kind) if kind != "Event" else None, mode if mode != "In person" else None]
    if status and status != "EventScheduled":
        facts.insert(0, humanize(status.removeprefix("Event")).upper())
    capacity = event.get("maximumAttendeeCapacity") or event.get("maximumPhysicalAttendeeCapacity")
    audience = [a if isinstance(a, str) else (a or {}).get("audienceType") or name_of(a) for a in listing(event.get("audience"))]
    ages = "For " + ", ".join(filter(None, audience)) if any(audience) else None
    spoken = " · ".join(filter(None, [
        None if (lang := languages(event.get("inLanguage"))) == "English" else lang,
        f"Subtitles: {s}" if (s := languages(event.get("subtitleLanguage"))) else None,
    ]))
    sport = people(listing(event.get("sport")) + listing(event.get("sports")))
    works = people(listing(event.get("workPresented")) + listing(event.get("workPerformed")))
    lines = [
        (0, clip(event.get("name"), 150)),
        (4, " · ".join(filter(None, facts))),
        (1, "🗓️ " + when(event)),
        (2, "📍 " + short_place(place(event)) if place(event) else None),
        (3, f"🎟️ {t}" if (t := tickets(event)) else None),
        (5, f"👥 Capacity {capacity}" if capacity else None),
        (6, f"🏠 By {o}" if (o := people(event.get("organizer"))) else None),
        (7, f"🎤 {p}" if (p := people(event.get("performer"))) and p != people(event.get("organizer")) else None),
        (8, f"🎬 {works}" if works else None),
        (9, f"🧒 {ages}" if ages else None),
        (10, f"🗣️ {spoken}" if spoken else None),
        (11, f"🏅 {sport}" if sport else None),
    ]
    return [(p, line) for p, line in lines if line]


def fit(lines, limit):
    """Keep the most important lines that fit within limit characters, in display order"""
    lines = [(p, line if i == 0 else clip(line, max(120, limit // 3))) for i, (p, line) in enumerate(lines)]
    kept, used = set(), 0
    for i, (_, line) in sorted(enumerate(lines), key=lambda x: x[1][0]):
        if used + len(line) + 2 <= limit:
            kept.add(i)
            used += len(line) + (2 if i == 0 else 1)
    body = [line for i, (_, line) in enumerate(lines) if i in kept]
    return "\n\n".join(filter(None, [body[0], "\n".join(body[1:])])) if body else ""


def listed_on(event):
    urls = [event["url"]] + [u for u in listing(event.get("sameAs")) if isinstance(u, str)]
    hosts = dict.fromkeys(urlparse(u).hostname.removeprefix("www.") for u in urls if u.startswith("http"))
    return list(hosts)


def image_url(event):
    for image in listing(event.get("image")):
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        if isinstance(image, str) and image.startswith("http"):
            return image
    return None


def hashtags(event):
    """City, neighbourhood and event type hashtags, without the #"""
    keywords = event.get("keywords") or []
    found = CITY_TAGS + [tag for key, tag in NEIGHBOURHOODS.items() if key in keywords]
    kind = event.get("@type") if isinstance(event.get("@type"), str) else "Event"
    if kind == "Event" and isinstance(event.get("additionalType"), str):
        kind = event["additionalType"]
    if kind != "Event":
        found.append(TYPE_TAGS.get(kind, kind.removesuffix("Event")))
    return [t for t in dict.fromkeys(found) if t][:8]


def last_call(event):
    left = tickets_left(event)
    seats = f"{left} seat{'' if left == 1 else 's'}" if left else "a few seats"
    return f"{LAST_CALL}: Only {seats} left for the event {LAST_CALL_TAG}"
