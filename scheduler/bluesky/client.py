import json
import time

import requests

from ..shared.events import parse_time
from ..shared.http import UA


class Client:
    """Minimal XRPC client for one account on an atproto PDS"""

    def __init__(self, pds, handle, password=None):
        self.pds, self.handle = pds.rstrip("/"), handle
        self.http = requests.Session()
        self.http.headers.update(UA)
        if password:
            session = self.call("com.atproto.server.createSession", {"identifier": handle, "password": password})
            self.http.headers["Authorization"] = f"Bearer {session['accessJwt']}"
            self.did = session["did"]
        else:
            self.did = self.call("com.atproto.identity.resolveHandle", handle=handle)["did"]

    def call(self, nsid, body=None, **params):
        url = f"{self.pds}/xrpc/{nsid}"
        for attempt in range(4):
            if body is None:
                res = self.http.get(url, params=params, timeout=30)
            else:
                res = self.http.post(url, json=body, timeout=30)
            wait = int(res.headers.get("Retry-After", "0") or 0)
            if res.status_code != 429 or attempt == 3 or wait > 300:
                break
            print(f"{nsid} as {self.handle}: rate limited, retrying in {wait}s")
            time.sleep(wait + 1)
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
        return self.call("com.atproto.repo.createRecord", {"repo": self.did, "collection": collection, "record": record})

    def put(self, collection, rkey, record):
        return self.call("com.atproto.repo.putRecord", {"repo": self.did, "collection": collection, "rkey": rkey, "record": record})

    def upload(self, data, mime):
        res = self.http.post(f"{self.pds}/xrpc/com.atproto.repo.uploadBlob", data=data, headers={"Content-Type": mime}, timeout=60)
        if not res.ok:
            raise RuntimeError(f"uploadBlob as {self.handle}: HTTP {res.status_code} {res.text[:300]}")
        return res.json()["blob"]


class DryClient(Client):
    """Reads the real account but prints every write"""

    def __init__(self, pds, handle, password=None):
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
