import datetime
import os

from .events import IST, parse_time

DAY_START = datetime.time(10)
DAY_END = datetime.time(19)
RUN = datetime.timedelta(minutes=5)
MAX_PER_RUN = 20


def day_end(day):
    """19:00 IST, unless SCHEDULER_DAY_END (an IST datetime) extends that one day"""
    override = parse_time(os.environ.get("SCHEDULER_DAY_END"))
    if override and override.astimezone(IST).date() == day:
        return override.astimezone(IST).time()
    return DAY_END


def in_window(now):
    local = now.astimezone(IST)
    return DAY_START <= local.time() < day_end(local.date())


def posting_time(start, end):
    """How much of the daily 10:00-19:00 IST posting hours falls between start and end"""
    total, day = datetime.timedelta(0), start.astimezone(IST).date()
    while (opens := datetime.datetime.combine(day, DAY_START, IST)) < end:
        closes = datetime.datetime.combine(day, day_end(day), IST)
        total += max(min(closes, end) - max(opens, start), datetime.timedelta(0))
        day += datetime.timedelta(days=1)
    return total


def spacing(deadlines, now):
    """The widest even gap between posts that still gets each queued event out by its deadline

    deadlines are in queue order, so the k-th event must go out within k gaps of now.
    """
    gaps = [posting_time(now, deadline) / k for k, deadline in enumerate(deadlines, 1)]
    return min(gaps, default=None)


def slots(next_slot, gap, now, available):
    """Post times due by now, each a gap after the last, so the long-run rate is exactly 1/gap

    After quiet hours the next slot is pulled up to now, so nothing piles up overnight.
    """
    if gap is None or not available:
        return [], next_slot
    if gap <= datetime.timedelta(0):
        # Out of posting time before a deadline: post as many as a run allows
        return [now] * min(available, MAX_PER_RUN), now
    slot, due = max(next_slot or now, now - RUN), []
    while slot <= now and len(due) < min(available, MAX_PER_RUN):
        due.append(slot)
        slot += gap
    return due, slot
