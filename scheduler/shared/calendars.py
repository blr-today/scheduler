import json
from pathlib import Path

import requests
import yaml

from .http import UA

WEBSITE = "https://raw.githubusercontent.com/blr-today/website/main/"


class Calendar:
    """A blr.today website calendar: any of its tags, none of its excluded tags"""

    def __init__(self, tags, exclude=()):
        self.tags, self.exclude = set(tags), set(exclude)

    def __contains__(self, event):
        keywords = set(event.get("keywords") or [])
        return bool(keywords & self.tags) and not keywords & self.exclude


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
