import re

import requests

UA = {"User-Agent": "blr.today scheduler (+https://github.com/blr-today/scheduler)"}


def fetch_image(url):
    """Original image, or the blr.today 600px resize when the original is too big"""
    sources = [url]
    if url.startswith("https://") and not re.search(r"[?%#]", url):
        sources.append("https://blr.today/img/600/" + url.removeprefix("https://"))
    for source in sources:
        try:
            res = requests.get(source, headers=UA, timeout=30)
        except requests.RequestException:
            continue
        mime = res.headers.get("Content-Type", "").split(";")[0]
        if res.ok and mime.startswith("image/"):
            yield res.content, mime
