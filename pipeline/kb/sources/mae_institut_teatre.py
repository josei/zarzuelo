"""MAE — Institut del Teatre (Barcelona), repositorio escena.cdmae.cat: programas y carteles de espectáculos,
incluida zarzuela representada en Barcelona y en giras, con fecha y teatro (§3.5).

Acceso real verificado en fase 0: DSpace 7 con API REST (`/server/api/discover/search/objects`, búsqueda de
relevancia) y OAI-PMH (`/server/oai/request`, `Identify`/`ListSets` confirmados). `robots.txt` desautoriza la
interfaz web `/discover` y `/search-filter`, pero no las rutas REST/OAI usadas aquí. Se cosecha vía OAI-PMH
(`ListRecords` con `resumptionToken`, cosechador en `_catalog.oai_harvest`) el set `Programes`
(`col_20.500.12268_112880`, confirmado por `ListSets`), filtrando por `dc:subject` = Sarsuela/Zarzuela
(etiqueta de género verificada en registros reales; evita falsos positivos como fotos con "Zarzuela" solo en
el nombre del teatro). Licencia: "Accés permès amb finalitat d'estudi o recerca, citant l'autor i la font
[...] Per a qualsevol altre ús cal demanar autorizació" (dc:rights) -> `consult_only`.
"""
from __future__ import annotations

import re
import sqlite3

from ..http import Http
from ..store import capture, segment
from ..util import now, sha256
from ._catalog import link_work, oai_harvest, robots_report

DEFAULT_CFG = {
    "endpoint": "https://escena.cdmae.cat", "license": "sin licencia de reutilización explícita (estudio o investigación con cita)",
    "reuse": "consult_only", "rate_limit_per_s": 1,
}
OAI_URL = "/server/oai/request"
PROGRAMES_SET = "col_20.500.12268_112880"
GENRE_SUBJECTS = ("sarsuela", "zarzuela")
METHOD = "mae-map"


def _one(rec: dict, key: str) -> str | None:
    v = rec.get(key)
    return v[0] if v else None


def _is_zarzuela(rec: dict) -> bool:
    return any(s.lower() in GENRE_SUBJECTS for s in rec.get("subject", []))


def _title_for_linking(title: str) -> str:
    """Quita el sufijo de temporada/año ('Parsifal. 1998-1999' -> 'Parsifal'), frecuente en estos títulos."""
    return re.sub(r"[.,]?\s*\d{4}(?:[-/]\d{2,4})?\s*$", "", title).strip()


def _render(rec: dict) -> str:
    lines = [f"MAE {rec['_identifier']} {_one(rec, 'title') or ''}"]
    for s in rec.get("subject", []):
        lines.append(f"materia: {s}")
    if _one(rec, "date"):
        for d in rec.get("date", []):
            if len(d) == 4 and d.isdigit():
                lines.append(f"fecha: {d}")
    rel = _one(rec, "relation")
    if rel:
        lines.append(f"relación: {rel}")
    pub = _one(rec, "publisher")
    if pub:
        lines.append(f"editor: {pub}")
    for i in rec.get("identifier", []):
        if i.startswith("http"):
            lines.append(f"url: {i}")
        else:
            lines.append(f"identificador: {i}")
    rights = _one(rec, "rights")
    if rights:
        lines.append(f"derechos: {rights}")
    return "\n".join(lines)


def _search_sample(http: Http, query: str, size: int) -> tuple[list[dict], int | None]:
    r = http.json(f"{http.cfg['endpoint']}/server/api/discover/search/objects", {"query": query, "size": size})
    out = []
    for o in r.get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", []):
        it = o.get("_embedded", {}).get("indexableObject", {})
        md = {k: [v["value"] for v in vs] for k, vs in it.get("metadata", {}).items()}
        out.append({"uuid": it.get("uuid"), "handle": it.get("handle"), "name": it.get("name"), "metadata": md})
    return out, r.get("_embedded", {}).get("searchResult", {}).get("page", {}).get("totalElements")


def phase0(sample: int = 10) -> dict:
    http = Http("mae_institut_teatre", cache=False, cfg=DEFAULT_CFG)
    robots = robots_report(http, DEFAULT_CFG["endpoint"], ["/server/api/discover/search/objects", OAI_URL, "/discover", "/search-filter"])
    items, total = _search_sample(http, "zarzuela", sample)
    http._wait()
    oai_identify = http.get(f"{DEFAULT_CFG['endpoint']}{OAI_URL}?verb=Identify")
    recs = list(oai_harvest(http, DEFAULT_CFG["endpoint"] + OAI_URL, set_spec=PROGRAMES_SET, max_records=sample))
    zarzuela_in_programes = sum(_is_zarzuela(r) for r in recs)
    return {
        "source": "mae_institut_teatre", "endpoint": DEFAULT_CFG["endpoint"], "robots": robots,
        "terms": "DSpace 7 REST (discover) y OAI-PMH (Identify/ListSets confirmados en vivo); robots.txt "
                 "desautoriza /discover y /search-filter (interfaz web), no las rutas REST/OAI usadas aquí. "
                 "Set OAI 'Programes' = col_20.500.12268_112880 (confirmado por ListSets)",
        "rest_search_sample_n": len(items), "rest_search_total_matches": total,
        "rest_search_titles": [it["name"] for it in items],
        "oai_identify_ok": "<Identify>" in oai_identify,
        "oai_programes_sample_n": len(recs), "oai_programes_zarzuela_hits": zarzuela_in_programes,
        "license": DEFAULT_CFG["license"], "reuse": DEFAULT_CFG["reuse"], "checked_at": now(),
    }


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http("mae_institut_teatre")
    cfg = http.cfg
    stats = {"seen": 0, "matched": 0, "documents": 0, "linked": 0}
    for rec in oai_harvest(http, cfg["endpoint"] + OAI_URL, set_spec=PROGRAMES_SET):
        stats["seen"] += 1
        if not _is_zarzuela(rec):
            continue
        if limit is not None and stats["matched"] >= limit:
            break
        stats["matched"] += 1
        text = _render(rec)
        stable_id = rec["_identifier"] or "MAE" + sha256(text)[:16]
        url = next((i for i in rec.get("identifier", []) if i.startswith("http")), f"{cfg['endpoint']}{OAI_URL}?verb=GetRecord&identifier={stable_id}")
        doc_id, new = capture(con, source="mae_institut_teatre", url=url, stable_id=stable_id, content=text,
                              license=cfg["license"], reuse=cfg["reuse"], mime="text/plain")
        stats["documents"] += int(new)
        segment(con, doc_id, lang="ca")
        title = _one(rec, "title")
        wid = link_work(con, _title_for_linking(title), require_creator=False) if title else None
        if wid:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, wid))
            stats["linked"] += 1
    con.commit()
    return stats
