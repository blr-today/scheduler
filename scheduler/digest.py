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
    "{{ $n := default (dict) $d.never }}{{ $count := 0 }}"
)
# Joins a template list like the website does: "A", "A and B", "A, B and C"
JOIN = '{{{{ if gt (len {0}) 1 }}}}{{{{ join ", " (initial {0}) }}}} and {{{{ last {0} }}}}{{{{ else }}}}{{{{ first {0} }}}}{{{{ end }}}}'


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


def explain(opts, total):
    titles = "(dict " + " ".join(f"{json.dumps(o['id'])} {json.dumps(o['title'])}" for o in opts) + ")"
    ids = "(list " + " ".join(json.dumps(o["id"]) for o in opts) + ")"
    pick = lambda var, out: (
        f"{{{{ {out} := list }}}}{{{{ range $id := {ids} }}}}{{{{ if hasKey {var} $id }}}}"
        f"{{{{ {out} = append {out} (index $t $id) }}}}{{{{ end }}}}{{{{ end }}}}"
    )
    return (
        f"{{{{ $t := {titles} }}}}{pick('$a', '$al')}{pick('$n', '$nl')}"
        f'<p style="color:#555;font-size:14px">Showing {{{{ $count }}}} of {total} events this week: the curated ones'
        f"{{{{ if $al }}}}, plus every {JOIN.format('$al')} event{{{{ end }}}}."
        f"{{{{ if $nl }}}} Leaving out {JOIN.format('$nl')} events.{{{{ end }}}}</p>"
    )


def render(events, opts, curated):
    conds = [(e, gate(matches(e, opts), e in curated)) for e in events]
    conds = [(e, c) for e, c in conds if c]
    manage = SITE + "/subscribe/#{{ .Subscriber.ID }}.{{ .Subscriber.UUID }}"
    return (
        PRELUDE
        + "".join(f"{{{{ if {c} }}}}{{{{ $count = add $count 1 }}}}{{{{ end }}}}" for _, c in conds)
        + explain(opts, len(events))
        + '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
        + "".join(f"{{{{ if {c} }}}}{item(e)}{{{{ end }}}}" for e, c in conds)
        + "</table>{{ if not $count }}<p>Nothing matched your choices this week.</p>{{ end }}"
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
    list_id = os.environ.get("LISTMONK_LIST_ID", config.get("list"))
    template = os.environ.get("LISTMONK_TEMPLATE_ID", config.get("template"))
    fields = {"name": name, "subject": subject(now), "lists": [int(list_id)], "content_type": "html", "body": body}
    if template:
        fields["template_id"] = int(template)
    camp = lm.call("PUT", f"/api/campaigns/{camp['id']}", json=fields) if camp else lm.call("POST", "/api/campaigns", json=fields)
    if send:
        lm.call("PUT", f"/api/campaigns/{camp['id']}/status", json={"status": "running"})
    print(f"digest: {name} {'sending' if send else 'saved as a draft'}")
