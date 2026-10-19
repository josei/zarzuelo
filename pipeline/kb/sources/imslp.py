"""IMSLP vía API MediaWiki (IMSLP:OldAPI): páginas de obra de la categoría de zarzuelas (§3.3).

Se descargan el texto wiki y los metadatos de la obra y de cada fichero (editor, copyright por fichero). Los PDF NO se
descargan: solo se registran nombre, descripción, editor y copyright de cada fichero con la URL de la página de la obra.
"""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import quote

from ..http import Http, load_sources
from ..models import Reuse
from ..store import capture, segment
from ._link import CorpusIndex, add_claim, edtf_from_slash, name_tokens, norm_title, robots_info

NAME = "imslp"
CATEGORY = "Category:Zarzuelas"
DEFAULT = {"endpoint": "https://imslp.org/api.php", "license": "CC-BY-SA-4.0", "reuse": "reusable", "rate_limit_per_s": 0.5}
BASE = "https://imslp.org"


def _cfg() -> dict:
    return {**DEFAULT, **load_sources().get(NAME, {})}


def _query(http: Http, **params) -> dict:
    return http.json(http.cfg["endpoint"], {"action": "query", "format": "json", **params})


def category_members(http: Http, limit: int | None = None) -> list[str]:
    out: list[str] = []
    cont: dict = {}
    while True:
        r = _query(http, list="categorymembers", cmtitle=CATEGORY, cmlimit=500, cmprop="title|type", **cont)
        out += [m["title"] for m in r["query"]["categorymembers"] if m.get("ns", 0) == 0]
        nxt = (r.get("query-continue") or {}).get("categorymembers") or (r.get("continue") if "continue" in r else None)
        if not nxt or (limit and len(out) >= limit):
            break
        cont = {k: v for k, v in nxt.items() if k != "continue"}
    return out[:limit] if limit else out


def fetch_pages(http: Http, titles: list[str]) -> list[dict]:
    """-> [{pageid,title,revid,timestamp,wikitext}] (API antigua: contenido en '*', pages indexado por id)."""
    out = []
    for i in range(0, len(titles), 20):
        r = _query(http, prop="revisions", rvprop="content|ids|timestamp", titles="|".join(titles[i:i + 20]))
        pages = r["query"]["pages"]
        for p in (pages.values() if isinstance(pages, dict) else pages):
            revs = p.get("revisions")
            if not revs or "missing" in p:
                continue
            rv = revs[0]
            out.append({"pageid": p["pageid"], "title": p["title"], "revid": rv["revid"], "timestamp": rv.get("timestamp"),
                        "wikitext": rv.get("*") or rv.get("content") or ""})
    return out


def _clean(v: str) -> str:
    v = re.sub(r"\{\{LinkLib\|([^|}]*)\|([^|}]*)(?:\|[^}]*)?\}\}", r"\1 \2", v)
    v = re.sub(r"\{\{LinkComp\|([^|}]*)\|([^|}]*)(?:\|[^}]*)?\}\}", r"\1 \2", v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\{\{[^}]*\}\}", "", v)
    return re.sub(r"\s+", " ", v).strip(" :")


def parse_work(wikitext: str) -> dict:
    """Campos de la plantilla `imslppage`: info de obra, comentarios y ficheros (sin descargarlos)."""
    info: dict[str, str] = {}
    sec = wikitext.split("*****WORK INFO*****", 1)[-1]
    sec, _, rest = sec.partition("*****COMMENTS*****")
    key = None
    for line in sec.splitlines():
        m = re.match(r"^\|\s*([^=|*]+?)\s*=(.*)$", line)
        if m:
            key, info[m.group(1)] = m.group(1), m.group(2).strip()
        elif key and line.strip() and not line.startswith(("|", "*")):
            info[key] += " " + line.strip()
    info = {k: _clean(v) for k, v in info.items() if _clean(v)}
    comments = _clean(rest.split("*****END OF TEMPLATE*****")[0]).strip(" =|*")
    files = []
    for blk in re.findall(r"\{\{#fte:imslpfile(.*?)\n\}\}", wikitext, flags=re.S):
        f: dict[str, str] = {}
        for k, v in re.findall(r"^\|\s*([^=|]+?)\s*=(.*)$", blk, flags=re.M):
            f[k.strip()] = v.strip()
        name = next((v for k, v in f.items() if k.startswith("File Name") and v), None)
        if name:
            desc = next((v for k, v in f.items() if k.startswith("File Description")), "")
            pub = re.sub(r"\{\{P\|([^|}]*)\|([^|}]*)\|([^|}]*)\|([^|}]*).*?\}\}", r"\2, \3, \4", f.get("Publisher Information", ""))
            files.append({"name": name, "desc": _clean(desc), "copyright": _clean(f.get("Copyright", "")),
                          "editor": _clean(f.get("Editor", "")), "arranger": _clean(f.get("Arranger", "")),
                          "publisher": _clean(pub), "image_type": _clean(f.get("Image Type", ""))})
    return {"info": info, "comments": comments, "files": files}


def composer_of(title: str) -> str | None:
    m = re.search(r"\(([^()]+)\)\s*$", title)
    return m.group(1) if m else None


def work_title_of(title: str) -> str:
    return re.sub(r"\s*\([^()]+\)\s*$", "", title)


def render(page: dict, parsed: dict) -> tuple[str, dict[str, tuple[str, str]]]:
    """Texto canónico (una línea por dato = un pasaje citable). -> (texto, {clave: línea})."""
    info = parsed["info"]
    lines = {"title": f"IMSLP: {page['title']}"}
    for key in ("Work Title", "Alternative Title", "First Performance", "Year/Date of Composition", "Year of First Publication",
                "Librettist", "Language", "Piece Style", "Instrumentation", "Tags", "Movements Header", "Dedication"):
        if info.get(key):
            lines[key] = f"{key}: {info[key]}"
    comp = composer_of(page["title"])
    if comp:
        lines["Composer"] = f"Composer: {comp}"
    if parsed["comments"]:
        lines["Comments"] = f"Comments: {parsed['comments']}"
    for i, f in enumerate(parsed["files"]):
        bits = [f"File: {f['name']}", f["desc"] and f"Description: {f['desc']}", f["copyright"] and f"Copyright: {f['copyright']}",
                f["publisher"] and f"Publisher: {f['publisher']}", f["editor"] and f"Editor: {f['editor']}",
                f["arranger"] and f"Arranger: {f['arranger']}", f["image_type"] and f"Type: {f['image_type']}"]
        lines[f"file{i}"] = " | ".join(b for b in bits if b)
    return "\n".join(lines.values()), lines


def _link_and_claims(con, idx: CorpusIndex, doc_id: str, page: dict, parsed: dict, lines: dict) -> tuple[str | None, int]:
    info = parsed["info"]
    comp = composer_of(page["title"])
    wid = idx.match_work(info.get("Work Title") or work_title_of(page["title"]), comp)
    if not wid:
        wid = idx.match_work(work_title_of(page["title"]), comp)
    if not wid:
        return None, 0
    con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, wid))
    passages = {p["text"]: p for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc_id,))}
    n = 0
    # compositor: solo si el corpus ya tiene como compositor a una persona que coincide con el nombre de IMSLP
    if comp and "Composer" in lines:
        ct = name_tokens(comp)
        for pid in idx.person_ids(comp):
            if con.execute("SELECT 1 FROM claims WHERE subject_id=? AND property='composer' AND json_extract(value_json,'$')=?", (wid, pid)).fetchone():
                add_claim(con, wid, "composer", pid, "entity", passages[lines["Composer"]], lines["Composer"], "imslp-map")
                n += 1
    fp = info.get("First Performance", "")
    date = edtf_from_slash(re.split(r"[\s,;]", fp, 1)[0]) if fp else None
    if date and "First Performance" in lines:
        add_claim(con, wid, "premiere_date", date, "edtf", passages[lines["First Performance"]], lines["First Performance"], "imslp-map")
        n += 1
    lib = info.get("Librettist")
    if lib and "Librettist" in lines:
        ids = [p for p in idx.person_ids(lib) if norm_title(idx.labels[p]) == norm_title(lib)]
        if len(ids) == 1:
            add_claim(con, wid, "librettist", ids[0], "entity", passages[lines["Librettist"]], lines["Librettist"], "imslp-map")
            n += 1
    return wid, n


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(NAME)
    cfg = http.cfg
    idx = CorpusIndex(con)
    stats = {"documents": 0, "linked": 0, "unlinked": 0, "claims": 0, "files_referenced": 0}
    for page in fetch_pages(http, category_members(http, limit)):
        parsed = parse_work(page["wikitext"])
        text, lines = render(page, parsed)
        url = f"{BASE}/wiki/{quote(page['title'].replace(' ', '_'), safe='(),_')}?oldid={page['revid']}"
        doc_id, new = capture(con, source=NAME, url=url, stable_id=f"{page['pageid']}@{page['revid']}", content=text,
                              license=cfg["license"], reuse=Reuse(cfg["reuse"]), raw=page["wikitext"].encode())
        stats["documents"] += int(new)
        segment(con, doc_id, lang="en")
        wid, n = _link_and_claims(con, idx, doc_id, page, parsed, lines)
        stats["linked" if wid else "unlinked"] += 1
        stats["claims"] += n
        stats["files_referenced"] += len(parsed["files"])
    con.commit()
    return stats


def phase0(sample: int = 10) -> dict:
    cfg = _cfg()
    http = Http(NAME, cache=False, cfg=cfg)
    members = category_members(http, sample)
    pages = fetch_pages(http, members[:sample])
    recs = []
    for p in pages:
        parsed = parse_work(p["wikitext"])
        recs.append({"title": p["title"], "revid": p["revid"], "first_performance": parsed["info"].get("First Performance"),
                     "files": len(parsed["files"]), "file_copyrights": sorted({f["copyright"] for f in parsed["files"] if f["copyright"]})})
    return {"source": NAME, "endpoint": cfg["endpoint"], "category": CATEGORY,
            "robots": robots_info(http, BASE, "/api.php"),
            "terms": "IMSLP:Copyright_Policy: texto de la wiki bajo CC BY-SA 4.0; cada partitura tiene su propio estado de derechos (campo Copyright por fichero); robots.txt declara Crawl-delay 2 y no veta /api.php",
            "sample": recs, "n_sample": len(recs),
            "license_effective": "texto wiki CC-BY-SA-4.0; ficheros: por objeto (Public Domain / Creative Commons / Copyright), solo referencia"}
