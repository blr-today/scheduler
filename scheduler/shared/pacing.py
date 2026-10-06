import datetime

from .events import IST

DAY_START = datetime.time(10)
DAY_END = datetime.time(19)
RUN = datetime.timedelta(minutes=5)
MAX_PER_RUN = 20


def in_window(now):
    return DAY_START <= now.astimezone(IST).time() < DAY_END


def posting_time(start, end):
    """How much of the daily 10:00-19:00 IST posting hours falls between start and end"""
    total, day = datetime.timedelta(0), start.astimezone(IST).date()
    while (opens := datetime.datetime.combine(day, DAY_START, IST)) < end:
        closes = datetime.datetime.combine(day, DAY_END, IST)
        total += max(min(closes, end) - max(opens, start), datetime.timedelta(0))
        day += datetime.timedelta(days=1)
    return total


def spacing(deadlines, now):
    """The widest even gap between posts that still gets each queued event out by its deadline

    deadlines are in queue order, so the k-th event must go out within k gaps of now.
    """
    gaps = [posting_time(now, deadline) / k for k, deadline in enumerate(deadlines, 1)]
    return min(gaps, default=None)


def allowance(last, gap, now):
    """How many posts this run may make: none until the gap has passed, several when it is under a run"""
    if gap is None or (last and last + gap > now):
        return 0
    if gap <= datetime.timedelta(0):
        return MAX_PER_RUN
    return min(MAX_PER_RUN, max(1, int(RUN / gap)))
