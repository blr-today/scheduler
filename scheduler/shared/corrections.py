from .events import cancelled, flatten, price, sold_out, tail
from .text import humanize, money, place, short_place, when

STATUS_NOTES = {"EventCancelled": "❌ Cancelled", "EventPostponed": "⏸️ Postponed", "EventRescheduled": "🔁 Rescheduled", "EventMovedOnline": "💻 Moved online", "EventScheduled": "✅ Back on schedule"}


def cheapest(event):
    prices = [p for p in map(price, flatten(event.get("offers"))) if p is not None]
    if "FREE" in (event.get("keywords") or []) or (prices and min(prices) == 0):
        return "Free"
    return money(min(prices)) if prices else None


def facts(event):
    """What a correction is posted for: when, where, cheapest price and status"""
    return {
        "start": event["_start"].isoformat(),
        "end": event["_end"].isoformat(),
        "place": short_place(place(event)) or None,
        "price": cheapest(event),
        "status": tail(event.get("eventStatus")) or "EventScheduled",
        "sold_out": sold_out(event),
    }


def changes(event, old):
    new, lines = facts(event), []
    if new["status"] != old["status"]:
        lines.append(STATUS_NOTES.get(new["status"], humanize(new["status"].removeprefix("Event"))))
    if new["sold_out"] != old.get("sold_out", False) and not cancelled(event):
        lines.append("🚫 Sold out" if new["sold_out"] else "🎟️ Tickets available again")
    if (new["start"], new["end"]) != (old["start"], old["end"]):
        lines.append("🗓️ Now " + when(event))
    if new["place"] != old["place"] and new["place"]:
        lines.append("📍 Now at " + new["place"])
    if new["price"] != old["price"] and new["price"] and not new["sold_out"]:
        lines.append("🎟️ Now free" if new["price"] == "Free" else f"🎟️ Now from {new['price']}")
    return lines
