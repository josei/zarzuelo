"""Wikipedia/Wikisource vía API MediaWiki, fijada por oldid (§3.2, §3.3)."""
from __future__ import annotations

import json
import re
import sqlite3
from html.parser import HTMLParser

from ..http import Http
from ..models import Reuse
from ..store import capture, segment

_SKIP_TAGS = {"table", "style", "script", "sup", "figure", "nav"}
_SKIP_CLASS = ("reference", "mw-editsection", "navbox", "infobox", "thumb", "toc", "hatnote", "mw-references-wrap", "reflist", "metadata")
_BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "dd", "dt", "blockquote"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.buf: list[str] = []
        self.skip = 0
        self.stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        skip = tag in _SKIP_TAGS or any(c in cls for c in _SKIP_CLASS)
        if tag not in ("br", "img", "hr", "meta", "link"):
            self.stack.append(skip)
            self.skip += skip
        if tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("br", "img", "hr", "meta", "link"):
            return
        if self.stack:
            self.skip -= self.stack.pop()
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if t:
            self.out.append(t)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p._flush()
    return "\n".join(p.out)


def latest_revision(http: Http, title: str) -> tuple[int, str] | None:
    r = http.json(http.cfg["endpoint"], {"action": "query", "prop": "revisions", "titles": title, "rvprop": "ids|timestamp",
                                         "redirects": 1, "format": "json", "formatversion": 2}, use_cache=False)
    page = r["query"]["pages"][0]
    if page.get("missing"):
        return None
    rev = page["revisions"][0]
    return rev["revid"], page["title"]


def parse_params(oldid: int) -> dict:
    return {"action": "parse", "oldid": oldid, "prop": "text|displaytitle", "format": "json", "formatversion": 2, "disableeditsection": 1}


def fetch_page(http: Http, source: str, title: str, oldid: int | None = None) -> tuple[str, int, str, str] | None:
    """-> (texto plano, oldid, título canónico, respuesta cruda de la API)."""
    if oldid is None:
        lr = latest_revision(http, title)
        if not lr:
            return None
        oldid, title = lr
    raw = http.get(http.cfg["endpoint"], parse_params(oldid))
    r = json.loads(raw)
    return html_to_text(r["parse"]["text"]), oldid, r["parse"]["title"], raw


def ingest_page(con: sqlite3.Connection, http: Http, source: str, title: str, oldid: int | None = None, lang: str = "es") -> str | None:
    got = fetch_page(http, source, title, oldid)
    if not got:
        return None
    text, oldid, canon, raw = got
    host = http.cfg["endpoint"].split("/w/")[0]
    doc_id, _ = capture(con, source=source, url=f"{host}/w/index.php?title={canon.replace(' ', '_')}&oldid={oldid}",
                        stable_id=str(oldid), content=text, license=http.cfg["license"], reuse=Reuse(http.cfg["reuse"]),
                        mime="text/plain", raw=raw.encode())  # crudo = respuesta completa de la API (HTML incluido)
    segment(con, doc_id, lang=lang)
    return doc_id
