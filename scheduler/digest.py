"""Weekly email: one listmonk campaign where every event checks the subscriber's Always/Curated/Never choices"""

import datetime
import html
import json
import os

import requests

from .shared.calendars import load_calendar
from .shared.events import IST, cancelled
from .shared.http import UA
from .shared.text import clip, place, short_place, when

WEEK = datetime.timedelta(days=7)
SITE = "https://blr.today"
PRELUDE = (
    "{{ $d := default (dict) .Subscriber.Attribs.digest }}{{ $a := default (dict) $d.always }}"
    "{{ $n := default (dict) $d.never }}{{ $shown := 0 }}"
)


def options(get):
    return [o for g in json.loads(get("_data/digest.json"))["groups"] for o in g["options"]]


def matches(event, opts):
    keywords, kind = set(event.get("keywords") or []), event.get("@type")
    return [o["id"] for o in opts if keywords & set(o.get("tags", [])) or kind in o.get("types", [])]


def gate(keys, curated):
    """Go template condition for one event, or None when no choice can ever show it"""
    if not keys:
        return "true" if curated else None
    either = lambda var: "(or " + " ".join(f'(hasKey ${var} "{k}")' for k in keys) + ")"
    allowed = f"(not {either('n')})"
    return allowed if curated else f"(and {allowed} {either('a')})"


def literal(text):
    return html.escape(text).replace("{{", '{{"{{"}}')


def week(events, now, excluded):
    first = {}
    for e in events:
        if now <= e["_start"] < now + WEEK and not cancelled(e) and not any(e in cal for cal in excluded):
            if e["url"] not in first or e["_start"] < first[e["url"]]["_start"]:
                first[e["url"]] = e
    return sorted(first.values(), key=lambda e: e["_start"])


def item(event):
    venue = short_place(place(event)[:1])
    meta = " · ".join(literal(x) for x in (when(event), venue) if x)
    return (
        '<tr><td style="padding:8px 0;border-bottom:1px solid #e6e6e6">'
        f'<a href="{literal(event["url"])}" style="font-weight:600;color:#1f8dd6;text-decoration:none">'
        f'{literal(clip(event.get("name"), 200))}</a><br><span style="color:#555;font-size:14px">{meta}</span></td></tr>'
    )


def render(events, opts, curated):
    rows = []
    for e in events:
        cond = gate(matches(e, opts), e in curated)
        if cond:
            rows.append(f"{{{{ if {cond} }}}}{{{{ $shown = add $shown 1 }}}}{item(e)}{{{{ end }}}}")
    manage = SITE + "/subscribe/#{{ .Subscriber.ID }}.{{ .Subscriber.UUID }}"
    return (
        PRELUDE
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
        + "".join(rows)
        + "</table>{{ if not $shown }}<p>Nothing matched your choices this week.</p>{{ end }}"
        + f'<p style="color:#555;font-size:14px"><a href="{manage}">Change what you get</a> · '
        + '<a href="{{ UnsubscribeURL }}">Unsubscribe</a></p>'
    )


def subject(now):
    end = now + WEEK - datetime.timedelta(days=1)
    return f"blr.today weekly: {now.day} {now:%b} – {end.day} {end:%b}"


class Listmonk:
    def __init__(self, url, user, token):
        self.url, self.http = url.rstrip("/"), requests.Session()
        self.http.headers.update({**UA, "Authorization": f"token {user}:{token}"})

    def call(self, method, path, **kwargs):
        res = self.http.request(method, self.url + path, timeout=60, **kwargs)
        res.raise_for_status()
        return res.json()["data"]

    def find(self, name):
        found = self.call("GET", "/api/campaigns", params={"query": name, "per_page": 50})
        return next((c for c in found["results"] if c["name"] == name), None)


def run(config, events, now, get, dry_run=False, send=False):
    now = now.astimezone(IST)
    opts, curated = options(get), load_calendar("curated", get)
    excluded = [load_calendar(name, get) for name in config.get("exclude", [])]
    picked = week(events, now, excluded)
    body = render(picked, opts, curated)
    name = f"Weekly {now:%G-W%V}"
    print(f"digest: {name}: {len(picked)} events, {len(body)} bytes")
    if dry_run:
        print(body)
        return
    lm = Listmonk(config["listmonk"], os.environ["LISTMONK_API_USER"], os.environ["LISTMONK_API_TOKEN"])
    camp = lm.find(name)
    if camp and camp["status"] != "draft":
        print(f"digest: {name} is already {camp['status']}")
        return
    fields = {"name": name, "subject": subject(now), "lists": [config["list"]], "content_type": "html", "body": body}
    if config.get("template"):
        fields["template_id"] = config["template"]
    camp = lm.call("PUT", f"/api/campaigns/{camp['id']}", json=fields) if camp else lm.call("POST", "/api/campaigns", json=fields)
    if send:
        lm.call("PUT", f"/api/campaigns/{camp['id']}/status", json={"status": "running"})
    print(f"digest: {name} {'sending' if send else 'saved as a draft'}")
