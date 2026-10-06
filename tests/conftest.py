import datetime

import pytest

from scheduler.shared.events import IST

NOW = datetime.datetime(2026, 10, 6, 10, 0, tzinfo=IST)


def make_event(url, hours, keywords=(), **extra):
    start = NOW + datetime.timedelta(hours=hours)
    return {"url": url, "name": url.upper(), "keywords": list(keywords), "_start": start, "_end": start, **extra}


class FakeClient:
    """Stands in for a Bluesky account: remembers what it created"""

    def __init__(self, handle, records=None):
        self.handle, self.did, self.created, self.puts = handle, f"did:plc:{handle}", [], []
        self.existing = records or {}
        self.fail_next = False

    def records(self, collection, since=None):
        made = [{"uri": f"at://{self.did}/{c}/{i}", "cid": "c", "value": r} for i, (c, r) in enumerate(self.created, 1) if c == collection]
        return iter(self.existing.get(collection, []) + made)

    def put(self, collection, rkey, record):
        self.puts.append((collection, rkey, record))

    def create(self, collection, record):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.created.append((collection, record))
        return {"uri": f"at://{self.did}/{collection}/{len(self.created)}", "cid": "c"}

    def upload(self, data, mime):
        raise AssertionError("no images in tests")


@pytest.fixture
def event():
    return make_event
