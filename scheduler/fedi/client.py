import time

import requests

from ..shared.events import parse_time
from ..shared.http import UA


class Client:
    """Minimal Mastodon-API client for one account on a fediverse server"""

    def __init__(self, server, account, token=None):
        self.server, self.account = server.rstrip("/"), account
        self.http = requests.Session()
        self.http.headers.update(UA)
        if token:
            self.http.headers["Authorization"] = f"Bearer {token}"
            self.id = self.call("GET", "/api/v1/accounts/verify_credentials")["id"]
        else:
            self.id = self.call("GET", "/api/v1/accounts/lookup", params={"acct": account})["id"]

    def call(self, method, path, **kwargs):
        for attempt in range(4):
            res = self.http.request(method, self.server + path, timeout=60, **kwargs)
            retry = res.headers.get("Retry-After", "")
            wait = int(retry) if retry.isdigit() else 30
            if res.status_code != 429 or attempt == 3 or wait > 300:
                break
            print(f"{path} as {self.account}: rate limited, retrying in {wait}s")
            time.sleep(wait + 1)
        if not res.ok:
            raise RuntimeError(f"{method} {path} as {self.account}: HTTP {res.status_code} {res.text[:300]}")
        return res.json()

    def statuses(self, since=None):
        max_id = None
        while True:
            page = self.call("GET", f"/api/v1/accounts/{self.id}/statuses", params={"limit": 40, "max_id": max_id})
            for status in page:
                created = parse_time(status.get("created_at"))
                if since and created and created < since:
                    return
                yield status
            if not page or max_id == page[-1]["id"]:
                return
            max_id = page[-1]["id"]

    def event_url(self, object_id):
        """The upstream url of an Event, read from its ActivityPub object"""
        try:
            res = self.http.get(object_id, headers={"Accept": "application/activity+json"}, timeout=30)
            obj = res.json() if res.ok else {}
        except (requests.RequestException, ValueError):
            return None
        return obj.get("url") if obj.get("type") == "Event" and obj.get("url") != object_id else None

    def media(self, data, description):
        files = {"file": ("poster.jpg", data, "image/jpeg")}
        return self.call("POST", "/api/v2/media", files=files, data={"description": description})["id"]

    def post(self, status, media_ids=(), reply_to=None, extra=None):
        body = {"status": status, "visibility": "public", "language": "en", "media_ids[]": list(media_ids), **(extra or {})}
        if reply_to:
            body["in_reply_to_id"] = reply_to
        return self.call("POST", "/api/v1/statuses", data=body)

    def reblogged(self, status_id):
        status = self.call("GET", f"/api/v1/statuses/{status_id}")
        return (status.get("reblog") or status).get("reblogged")

    def reblog(self, status_id):
        self.call("POST", f"/api/v1/statuses/{status_id}/reblog")


class DryClient(Client):
    """Reads the real account (with its token when given) but prints every write"""

    def __init__(self, server, account, token=None):
        try:
            super().__init__(server, account, token)
        except (RuntimeError, requests.RequestException) as e:
            print(f"[dry-run] {e}, assuming an empty account")
            self.id = None

    def statuses(self, since=None):
        return super().statuses(since) if self.id else iter(())

    def media(self, data, description):
        print(f"[dry-run] {self.account} media {len(data)} bytes: {description}")
        return "dry-media"

    def post(self, status, media_ids=(), reply_to=None, extra=None):
        print(f"[dry-run] {self.account} status {extra or ''}{f' in reply to {reply_to}' if reply_to else ''}\n{status}\n")
        return {"id": f"dry{time.monotonic_ns()}", "url": None}

    def reblogged(self, status_id):
        return False if status_id.startswith("dry") else super().reblogged(status_id)

    def reblog(self, status_id):
        print(f"[dry-run] {self.account} reblogs {status_id}")
