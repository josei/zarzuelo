"""Internet Archive: libretos y prensa digitalizados (colección `texts`) y discos de 78 rpm del Great 78 Project
(colección `georgeblood`) (§3.3). API de búsqueda (`advancedsearch.php`) y de metadatos (`/metadata/<id>`).

Para `texts` se descarga el OCR en texto plano (fichero derivado `*_djvu.txt`, pocas decenas de KB): es texto, no
medio. Para `georgeblood` (audio) y cualquier escaneo/imagen/PDF de un ítem no se descarga nada: solo se referencian
nombre, formato y tamaño de cada fichero original, con la URL de descarga (`/download/<id>/<nombre>`). La licencia es
por ítem (`licenseurl`/`rights` de la metadata); sin declaración explícita se trata como `consult_only`. El estado de
derechos del Great 78 Project está en litigio (demanda de las discográficas a Internet Archive, 2023): no se asume
dominio público por estar en esa colección.
"""
from __future__ import annotations

import re
import sqlite3

from ..http import Http, load_sources
from ..models import Reuse
from ..store import capture, segment
from ._link import CorpusIndex, robots_info

NAME = "internet_archive"
DEFAULT = {"endpoint": "https://archive.org", "license": "per-item", "reuse": "consult_only", "rate_limit_per_s": 1}
SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata"
DOWNLOAD = "https://archive.org/download"
FL = ["identifier", "title", "creator", "date", "language", "description", "publisher", "genre",
      "mediatype", "collection", "subject", "licenseurl", "rights"]
# (consulta, tipo de ítem esperado)
QUERIES = [
    ('mediatype:texts AND subject:"zarzuela"', "texts"),
    ('mediatype:texts AND title:"zarzuela"', "texts"),
    ('collection:georgeblood AND subject:"zarzuela"', "audio"),
    ('collection:georgeblood AND genre:"Zarzuela"', "audio"),
]
_CC = re.compile(r"creativecommons\.org/(licenses|publicdomain)|public.?domain", re.I)


def _cfg() -> dict:
    return {**DEFAULT, **load_sources().get(NAME, {})}


def search(http: Http, query: str, rows: int = 50) -> list[dict]:
    r = http.json(SEARCH, {"q": query, "fl[]": FL, "rows": rows, "page": 1, "output": "json"})
    return r["response"]["docs"]


def metadata(http: Http, identifier: str) -> dict:
    return http.json(f"{METADATA}/{identifier}")


def _str(v) -> str:
    return "; ".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


def files_of(meta: dict) -> list[dict]:
    """Ficheros originales (no derivados) con su URL de descarga -- solo referencia, nunca se descargan."""
    ident = meta.get("metadata", {}).get("identifier", "")
    out = []
    for f in meta.get("files", []):
        if f.get("source") != "original":
            continue
        out.append({"name": f["name"], "format": f.get("format", ""), "size": int(f.get("size") or 0),
                     "url": f"{DOWNLOAD}/{ident}/{f['name']}"})
    return out


def ocr_file(meta: dict) -> dict | None:
    for f in meta.get("files", []):
        if f.get("format") == "DjVuTXT" or f["name"].endswith("_djvu.txt"):
            return f
    return None


def _license_of(m: dict) -> tuple[str, Reuse]:
    lic = m.get("licenseurl") or m.get("rights")
    if lic and _CC.search(str(lic)):
        return str(lic), Reuse.reusable
    return str(lic or "no declarado (Internet Archive, por objeto)"), Reuse.consult_only


def render_metadata(meta: dict) -> str:
    m = meta["metadata"]
    lines = [f"Internet Archive: {m.get('title') or m['identifier']} [{m['identifier']}]"]
    for key, lab in (("creator", "Creator"), ("date", "Date"), ("language", "Language"),
                     ("publisher", "Publisher"), ("genre", "Genre")):
        if m.get(key):
            lines.append(f"{lab}: {_str(m[key])}")
    if m.get("description"):
        lines.append(f"Description: {_str(m['description'])}")
    if m.get("subject"):
        lines.append("Subject: " + _str(m["subject"]))
    if m.get("collection"):
        lines.append("Collection: " + _str(m["collection"]))
    lic, _ = _license_of(m)
    lines.append(f"Rights: {lic}")
    for f in files_of(meta):
        lines.append(f"File (reference only, not stored): {f['name']} [{f['format']}, {f['size']} bytes] {f['url']}")
    return "\n".join(lines)


def render(http: Http, meta: dict) -> str:
    """Para `texts`, antepone el OCR (texto, se descarga); el resto de ficheros (escaneos/PDF) solo se referencian."""
    head = render_metadata(meta)
    ocr = ocr_file(meta)
    if meta["metadata"].get("mediatype") != "texts" or not ocr:
        return head
    ident = meta["metadata"]["identifier"]
    body = http.get(f"{DOWNLOAD}/{ident}/{ocr['name']}")
    return head + "\n*****OCR*****\n" + body


def _link(idx: CorpusIndex, m: dict) -> str | None:
    title = m.get("title") or ""
    return idx.match_work(title, _str(m.get("creator"))) if title else None


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(NAME)
    cfg = http.cfg
    idx = CorpusIndex(con)
    stats = {"documents": 0, "linked": 0, "skipped_unlinked": 0, "texts": 0, "audio": 0, "files_referenced": 0}
    seen: set[str] = set()
    for query, kind in QUERIES:
        if limit and stats["linked"] >= limit:
            break
        for doc in search(http, query, rows=limit or 50):
            if limit and stats["linked"] >= limit:
                break
            ident = doc["identifier"]
            if ident in seen:
                continue
            seen.add(ident)
            meta = metadata(http, ident)
            m = meta.get("metadata")
            if not m:
                continue
            wid = _link(idx, m)
            if not wid:
                stats["skipped_unlinked"] += 1
                continue
            lic, reuse = _license_of(m)
            doc_id, new = capture(con, source=NAME, url=f"{DEFAULT['endpoint']}/details/{ident}", stable_id=ident,
                                  content=render(http, meta), license=lic, reuse=reuse, mime="text/plain")
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, wid))
            stats["documents"] += int(new)
            segment(con, doc_id, lang="es")
            stats["linked"] += 1
            stats[kind] += 1
            stats["files_referenced"] += len(files_of(meta))
    con.commit()
    return stats


def phase0(sample: int = 10) -> dict:
    cfg = _cfg()
    http = Http(NAME, cache=False, cfg=cfg)
    recs = []
    per_query = max(1, sample // len(QUERIES) + 1)
    for query, kind in QUERIES:
        if len(recs) >= sample:
            break
        for doc in search(http, query, rows=per_query):
            if len(recs) >= sample:
                break
            meta = metadata(http, doc["identifier"])
            m = meta.get("metadata", {})
            files = files_of(meta)
            ocr = ocr_file(meta)
            lic, reuse = _license_of(m)
            recs.append({"identifier": doc["identifier"], "kind": kind, "title": m.get("title"),
                         "mediatype": m.get("mediatype"), "license": lic, "reuse": reuse.value,
                         "files_referenced": len(files), "bytes_original_files": sum(f["size"] for f in files),
                         "ocr_bytes": int(ocr["size"]) if ocr else None})
    return {"source": NAME, "endpoint": DEFAULT["endpoint"],
            "robots": robots_info(http, DEFAULT["endpoint"], "/advancedsearch.php"),
            "terms": "https://archive.org/about/terms.php (API de búsqueda/metadatos de uso libre; robots.txt solo "
                     "veta /control/ y /report/, no /advancedsearch.php ni /metadata/); Great 78 Project (colección "
                     "georgeblood): grabaciones de 78 rpm digitalizadas por George Blood, L.P.; estado de derechos "
                     "en litigio (demanda de discográficas a Internet Archive, 2023) -- no se asume dominio público "
                     "por estar en la colección, solo por licencia explícita del ítem",
            "sample": recs, "n_sample": len(recs),
            "license_effective": "por ítem (licenseurl/rights de /metadata/<id>); sin declaración explícita -> "
                                  "consult_only. OCR de `texts` (fichero DjVuTXT) se descarga como texto; audio, "
                                  "imágenes, escaneos y PDF nunca se descargan, solo referencia (nombre, formato, "
                                  "tamaño y URL de /download/<id>/<fichero>)."}
