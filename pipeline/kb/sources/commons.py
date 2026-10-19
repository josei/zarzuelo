"""Wikimedia Commons vía API MediaWiki: carteles, retratos y fotos de teatros (§3.3).

Se descargan solo los metadatos de cada fichero (descripción, autor, categorías, licencia por fichero). La imagen/audio NO
se descarga: el documento guarda la URL original y la licencia efectiva del fichero.
"""
from __future__ import annotations

import html
import re
import sqlite3

from ..http import Http, load_sources
from ..models import Reuse
from ..store import capture, segment
from ._link import CorpusIndex, robots_info

NAME = "commons"
DEFAULT = {"endpoint": "https://commons.wikimedia.org/w/api.php", "license": "per-file", "reuse": "reusable", "rate_limit_per_s": 1}
BASE = "https://commons.wikimedia.org"
SEEDS = ["Category:Zarzuela", "Category:Zarzuelas", "Category:Zarzuela composers"]
SEARCHES = ["zarzuela cartel", "zarzuela teatro fachada"]
IIPROP = "url|extmetadata|size|mime|timestamp"


def _cfg() -> dict:
    return {**DEFAULT, **load_sources().get(NAME, {})}


def _api(http: Http, **p) -> dict:
    return http.json(http.cfg["endpoint"], {"action": "query", "format": "json", "formatversion": 2, **p})


def subcategories(http: Http, cat: str) -> list[str]:
    out, cont = [], {}
    while True:
        r = _api(http, list="categorymembers", cmtitle=cat, cmtype="subcat", cmlimit=500, **cont)
        out += [m["title"] for m in r["query"]["categorymembers"]]
        if "continue" not in r:
            return out
        cont = r["continue"]


def category_files(http: Http, cat: str, limit: int | None = None) -> list[dict]:
    out, cont = [], {}
    while True:
        r = _api(http, generator="categorymembers", gcmtitle=cat, gcmtype="file", gcmlimit=50, prop="imageinfo",
                 iiprop=IIPROP, iiextmetadatalanguage="es", **cont)
        out += [p for p in r.get("query", {}).get("pages", []) if p.get("imageinfo")]
        if "continue" not in r or (limit and len(out) >= limit):
            return out[:limit] if limit else out
        cont = r["continue"]


def search_files(http: Http, query: str, limit: int = 30) -> list[dict]:
    r = _api(http, generator="search", gsrsearch=query, gsrnamespace=6, gsrlimit=min(limit, 50), prop="imageinfo",
             iiprop=IIPROP, iiextmetadatalanguage="es")
    return [p for p in r.get("query", {}).get("pages", []) if p.get("imageinfo")]


def _txt(v) -> str:
    v = html.unescape(re.sub(r"<[^>]+>", " ", str(v or "")))
    return re.sub(r"\s+", " ", v).strip()


def file_record(page: dict) -> dict:
    ii = page["imageinfo"][0]
    em = {k: _txt(v.get("value")) for k, v in ii.get("extmetadata", {}).items()}
    lic = em.get("LicenseShortName") or em.get("License") or ""
    return {"pageid": page["pageid"], "title": page["title"], "url": ii.get("descriptionurl"),
            "original": (ii.get("url") or "").split("?")[0], "mime": ii.get("mime"), "size": ii.get("size"),
            "width": ii.get("width"), "height": ii.get("height"), "timestamp": ii.get("timestamp"),
            "description": em.get("ImageDescription", ""), "date": em.get("DateTimeOriginal") or em.get("DateTime", ""),
            "artist": em.get("Artist", ""), "credit": em.get("Credit", ""), "categories": [c for c in em.get("Categories", "").split("|") if c],
            "license": lic, "license_url": em.get("LicenseUrl", ""), "restrictions": em.get("Restrictions", ""),
            "attribution_required": em.get("AttributionRequired", "")}


def render(rec: dict) -> str:
    lines = [f"Commons: {rec['title']}"]
    for k, lab in (("description", "Description"), ("date", "Date"), ("artist", "Author"), ("credit", "Credit")):
        if rec[k]:
            lines.append(f"{lab}: {rec[k]}")
    if rec["categories"]:
        lines.append("Categories: " + "; ".join(rec["categories"]))
    lines.append(f"License: {rec['license'] or 'unknown'}" + (f" ({rec['license_url']})" if rec["license_url"] else ""))
    if rec["attribution_required"]:
        lines.append(f"Attribution required: {rec['attribution_required']}")
    if rec["restrictions"]:
        lines.append(f"Restrictions: {rec['restrictions']}")
    lines.append(f"Media (reference only, not stored): {rec['original']} [{rec['mime']}, {rec['size']} bytes]")
    return "\n".join(lines)


def link_file(idx: CorpusIndex, rec: dict, parent: str | None) -> list[str]:
    """Entidades del corpus a las que se enlaza el fichero: categoría contenedora, categorías propias o título+compositor."""
    found: list[str] = []
    names = ([parent] if parent else []) + rec["categories"]
    for n in names:
        e = idx.match_entity_name(re.sub(r"^Category:", "", n))
        if e and e not in found:
            found.append(e)
    stem = re.sub(r"^File:|\.[A-Za-z0-9]{2,5}$", "", rec["title"])
    w = idx.match_work(stem, rec["description"] + " " + stem)
    if w and w not in found and len(stem.split()) >= 2:
        found.append(w)
    return found


def _store(con, http, rec: dict, subjects: list[str]) -> bool:
    lic = rec["license"] or "unknown"
    reuse = Reuse.reusable if rec["license"] else Reuse.consult_only
    doc_id, new = capture(con, source=NAME, url=rec["url"], stable_id=f"{rec['pageid']}@{rec['timestamp']}", content=render(rec),
                          license=lic, reuse=reuse, mime="text/plain")
    segment(con, doc_id, lang="es")
    for s in subjects:
        con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, s))
    return new


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(NAME)
    idx = CorpusIndex(con)
    stats = {"documents": 0, "linked": 0, "skipped_unlinked": 0}
    seen: set[int] = set()

    def handle(pages, parent=None):
        for p in pages:
            if limit and stats["linked"] >= limit:
                return
            if p["pageid"] in seen:
                continue
            seen.add(p["pageid"])
            rec = file_record(p)
            subs = link_file(idx, rec, parent)
            if not subs:
                stats["skipped_unlinked"] += 1
                continue
            stats["documents"] += int(_store(con, http, rec, subs))
            stats["linked"] += 1

    for seed in SEEDS:
        handle(category_files(http, seed, limit))
        for sub in subcategories(http, seed):
            if limit and stats["linked"] >= limit:
                break
            if idx.match_entity_name(sub.removeprefix("Category:")):
                handle(category_files(http, sub, limit), parent=sub)
    for q in SEARCHES:
        if limit and stats["linked"] >= limit:
            break
        handle(search_files(http, q))
    con.commit()
    return stats


def phase0(sample: int = 10) -> dict:
    cfg = _cfg()
    http = Http(NAME, cache=False, cfg=cfg)
    pages = category_files(http, SEEDS[0], sample)
    recs = [file_record(p) for p in pages[:sample]]
    return {"source": NAME, "endpoint": cfg["endpoint"], "robots": robots_info(http, BASE, "/w/api.php"),
            "terms": "Wikimedia Terms of Use + API:Etiquette (User-Agent con contacto, peticiones en serie, el API está pensado para uso automatizado aunque robots.txt vete /w/ a rastreadores genéricos); texto de las páginas de descripción CC BY-SA 4.0; cada fichero con su licencia libre propia",
            "sample": [{"title": r["title"], "license": r["license"], "mime": r["mime"], "categories": r["categories"][:4]} for r in recs],
            "n_sample": len(recs),
            "license_effective": "por fichero (LicenseShortName de extmetadata); solo se guardan metadatos y URL, nunca el medio"}
