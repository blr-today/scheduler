import json
from pathlib import Path

import requests
import yaml

from .http import UA

WEBSITE = "https://raw.githubusercontent.com/blr-today/website/main/"


class Calendar:
    """Events with any of the tags or schema.org types, and none of the excluded tags"""

    def __init__(self, tags, exclude=(), types=()):
        self.tags, self.exclude, self.types = set(tags), set(exclude), set(types)

    def __contains__(self, event):
        keywords = set(event.get("keywords") or [])
        matches = keywords & self.tags or event.get("@type") in self.types
        return bool(matches) and not keywords & self.exclude


def website(root=None):
    if root:
        return lambda path: Path(root, path).read_text()

    def get(path):
        res = requests.get(WEBSITE + path, headers=UA, timeout=30)
        res.raise_for_status()
        return res.text

    return get


def load_calendar(name, get):
    config = yaml.safe_load(get("_config.yml"))
    defaults = next(d["values"] for d in config["defaults"] if d["scope"]["path"] == "cal/*.md")
    page = yaml.safe_load(get(f"cal/{name}.md").split("---")[1])
    return Calendar(json.loads(page["tags"]), json.loads(page.get("excludeTags", defaults["excludeTags"])))


def account_calendar(entry, get):
    """An account's calendar: a website calendar page, or tags and types listed in scheduler.yml"""
    if "calendar" in entry:
        return load_calendar(entry["calendar"], get)
    return Calendar(entry.get("tags", []), entry.get("exclude", []), entry.get("types", []))
