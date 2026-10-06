import datetime

from .events import IST

DAY_START = datetime.time(10)
DAY_END = datetime.time(19)
SLOT = datetime.timedelta(minutes=30)
# The CronJob runs every 5 minutes, so posts land at most every 10 minutes
MIN_GAP = datetime.timedelta(minutes=7, seconds=30)
URGENT = datetime.timedelta(days=1)


def in_window(now):
    return DAY_START <= now.astimezone(IST).time() < DAY_END


def gap(pending, now):
    """Spacing that spreads the pending events evenly over the rest of the posting day"""
    local = now.astimezone(IST)
    left = datetime.datetime.combine(local.date(), DAY_END, IST) - local
    return max(max(left, SLOT) / max(pending, 1), MIN_GAP)


def due(last, pending, now, deadline=None):
    """When the next post is due: evenly spaced, but within URGENT of the soonest event's posting deadline

    A day of urgency always spans a full posting window, wherever the deadline falls.
    """
    if last is None:
        return now
    when = last + gap(pending + 1, now)
    if deadline:
        when = min(when, max(last + MIN_GAP, deadline - URGENT))
    return when
