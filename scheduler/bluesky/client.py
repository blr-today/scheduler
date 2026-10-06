import json
import os
import time
from pathlib import Path

import requests

from ..shared.events import parse_time
from ..shared.http import UA


class Client:
    """Minimal XRPC client for one account on an atproto PDS

    Reads are public. Writes sign in lazily and reuse a session cached in sessions/, refreshing
    it when the access token expires, since PDSes rate-limit password logins hard.
    """

    def __init__(self, pds, handle, password=None, sessions=None):
        self.pds, self.handle, self.password = pds.rstrip("/"), handle, password
        self.cache = Path(sessions, f"{handle}.json") if sessions else None
        self.http = requests.Session()
        self.http.headers.update(UA)
        self.session = None
        self.did = self.call("com.atproto.identity.resolveHandle", handle=handle)["did"]

    def login(self):
        if self.cache and self.cache.exists():
            self.use(json.loads(self.cache.read_text()))
            return
        self.use(self.call("com.atproto.server.createSession", {"identifier": self.handle, "password": self.password}), save=True)

    def refresh(self):
        """Swap an expired access token for a new one, or sign in again if the refresh token is gone too"""
        res = self.http.post(f"{self.pds}/xrpc/com.atproto.server.refreshSession", headers={"Authorization": f"Bearer {self.session['refreshJwt']}"}, timeout=30)
        if res.ok:
            self.use(res.json(), save=True)
        else:
            self.use(self.call("com.atproto.server.createSession", {"identifier": self.handle, "password": self.password}), save=True)

    def use(self, session, save=False):
        self.session = session
        self.http.headers["Authorization"] = f"Bearer {session['accessJwt']}"
        if save and self.cache:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: session[k] for k in ("accessJwt", "refreshJwt", "did")}))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.cache)

    def write(self, nsid, body=None, **kwargs):
        if not self.session:
            self.login()
        try:
            return self.call(nsid, body, **kwargs)
        except ExpiredSession:
            self.refresh()
            return self.call(nsid, body, **kwargs)

    def call(self, nsid, body=None, data=None, headers=None, **params):
        url = f"{self.pds}/xrpc/{nsid}"
        for attempt in range(4):
            if data is not None:
                res = self.http.post(url, data=data, headers=headers, timeout=60)
            elif body is None:
                res = self.http.get(url, params=params, timeout=30)
            else:
                res = self.http.post(url, json=body, timeout=30)
            wait = int(res.headers.get("Retry-After", "0") or 0)
            if res.status_code != 429 or attempt == 3 or wait > 300:
                break
            print(f"{nsid} as {self.handle}: rate limited, retrying in {wait}s")
            time.sleep(wait + 1)
        if self.session and (res.status_code == 401 or (res.status_code == 400 and "ExpiredToken" in res.text)):
            raise ExpiredSession(nsid)
        if not res.ok:
            raise RuntimeError(f"{nsid} as {self.handle}: HTTP {res.status_code} {res.text[:300]}")
        return res.json()

    def records(self, collection, since=None):
        cursor = None
        while True:
            page = self.call("com.atproto.repo.listRecords", repo=self.did, collection=collection, limit=100, cursor=cursor)
            for record in page["records"]:
                created = parse_time(record["value"].get("createdAt"))
                if since and created and created < since:
                    return
                yield record
            if not (cursor := page.get("cursor")) or not page["records"]:
                return

    def create(self, collection, record):
        return self.write("com.atproto.repo.createRecord", {"repo": self.did, "collection": collection, "record": record})

    def put(self, collection, rkey, record):
        return self.write("com.atproto.repo.putRecord", {"repo": self.did, "collection": collection, "rkey": rkey, "record": record})

    def upload(self, data, mime):
        return self.write("com.atproto.repo.uploadBlob", data=data, headers={"Content-Type": mime})["blob"]


class ExpiredSession(Exception):
    pass


class DryClient(Client):
    """Reads the real account but prints every write"""

    def __init__(self, pds, handle, password=None, sessions=None):
        try:
            super().__init__(pds, handle)
        except RuntimeError as e:
            print(f"[dry-run] {e}, assuming an empty account")
            self.did = None

    def records(self, collection, since=None):
        return super().records(collection, since) if self.did else iter(())

    def create(self, collection, record):
        print(f"[dry-run] {self.handle} {collection}\n{json.dumps(record, indent=2, ensure_ascii=False)}")
        return {"uri": f"at://{self.did}/{collection}/dry{time.monotonic_ns()}", "cid": "dry"}

    def put(self, collection, rkey, record):
        print(f"[dry-run] {self.handle} {collection}/{rkey}: {record['name']}")

    def upload(self, data, mime):
        return {"$type": "blob", "mimeType": mime, "size": len(data), "ref": {"$link": "dry"}}
