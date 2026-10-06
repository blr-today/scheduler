import datetime
import email.utils
import json
import os
import sqlite3
from pathlib import Path

import requests

from .http import UA

MAX_AGE = datetime.timedelta(hours=48)


class StaleDatabase(Exception):
    """Too old to trust for announcing events"""


def verify(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError(f"{path} failed the integrity check")
        if not conn.execute("SELECT count(*) FROM events").fetchone()[0]:
            raise ValueError(f"{path} has no events")
    finally:
        conn.close()


def fetch(url, path, now):
    """Download events.db only when it changed, keeping the last good copy

    Conditional requests use the ETag and Last-Modified of the copy on disk, and a new
    file only replaces it after it passes an integrity check.
    """
    path, meta_path = Path(path), Path(path).with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if path.exists() and meta_path.exists() else {}
    headers = dict(UA)
    if meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    if meta.get("last_modified"):
        headers["If-Modified-Since"] = meta["last_modified"]
    res = requests.get(url, headers=headers, timeout=120)
    if res.status_code == 304:
        print(f"events.db unchanged since {meta.get('last_modified')}")
    else:
        res.raise_for_status()
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(res.content)
        verify(tmp)
        os.replace(tmp, path)
        meta = {"etag": res.headers.get("ETag"), "last_modified": res.headers.get("Last-Modified"), "fetched": now.isoformat()}
        meta_path.write_text(json.dumps(meta))
        print(f"events.db updated, last modified {meta['last_modified']}")
    modified = email.utils.parsedate_to_datetime(meta["last_modified"]) if meta.get("last_modified") else None
    if modified and now - modified > MAX_AGE:
        raise StaleDatabase(f"events.db was last modified {modified:%Y-%m-%d %H:%M} UTC, more than {MAX_AGE} ago")
    return path
