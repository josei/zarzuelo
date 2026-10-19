"""RISM Online: fuentes musicales (manuscritos, impresos), clave para tonadilla y siglo XVIII (§3.1).

Acceso real verificado en fase 0: `robots.txt` de rism.online permite `/sources/` y `/works/` (y, por
omisión, `/people/`/`/institutions/`, no mencionados), pero **prohíbe `/search?`** — que es justo la ruta
de la "Search API" documentada oficialmente (`/docs/api/search-api/`) — y `/api/`. No se esquiva: en vez de
buscar por texto libre, el descubrimiento es conservador y parte de identificadores RISM ya enlazados a
personas del corpus (Wikidata P5504, formato `people/NNN`), siguiendo `/{id}/sources` (permitido) para
listar sus fuentes y `/sources/{id}` (permitido) para el registro completo, vía negociación de contenido
JSON-LD (`Accept: application/ld+json`), tal como documenta `/docs/api/resource-api/`.

Licencia: "Reuse of RISM's own texts is permitted under CC BY-NC-SA 4.0" (rism.info, how-to-cite); por la
cláusula NC se trata como `consult_only`. `Crawl-delay: 5` en robots.txt -> 0.2 peticiones/s.
"""
from __future__ import annotations

import re
import sqlite3

from ..http import Http
from ..store import capture, segment
from ..util import now
from . import _music as M
from ._catalog import corpus_external_ids, robots_report

DEFAULT_CFG = {
    "endpoint": "https://rism.online", "license": "CC-BY-NC-SA-4.0", "reuse": "consult_only",
    "rate_limit_per_s": 0.2,
}
LD = {"Accept": "application/ld+json"}
WD_PROP = "P5504"  # Wikidata: RISM ID
XKEY = "rism"
METHOD = "rism-map"
SEED_PERSON = "people/30009629"  # Antonio Rosales (1740c-1801), compositor de tonadillas: ejemplo de verificación


def _relpath(url: str) -> str:
    return re.sub(r"^https?://rism\.online/", "", url).strip("/")


def _get(http: Http, relpath: str) -> dict | None:
    try:
        return http.json(f"{http.cfg['endpoint']}/{relpath}", None, LD)
    except Exception:  # noqa: BLE001 - 404/400 u otro fallo puntual: se omite el registro
        return None


def _label(v: dict | None, *, langs=("es", "en", "none")) -> str:
    if not v:
        return ""
    for l in langs:
        if v.get(l):
            return v[l][0]
    for vals in v.values():
        if vals:
            return vals[0]
    return ""


def _source_sample(http: Http, person_rel: str, remaining: int) -> list[str]:
    """IDs relativos ('sources/NNN') de las fuentes de una persona, vía `/{id}/sources` (permitido)."""
    coll = _get(http, f"{person_rel}/sources")
    if not coll:
        return []
    return [_relpath(it["id"]) for it in coll.get("items", [])[:remaining]]


def _fecha(contents: dict) -> str | None:
    for s in (contents or {}).get("summary", []):
        lab = s.get("label", {})
        if "Fecha" in (lab.get("es") or []) or "Fechas" in (lab.get("es") or []):
            return _label(s.get("value"))
    return None


def _render(relpath: str, rec: dict) -> tuple[str, str | None]:
    """Texto canónico (una línea = un pasaje citable). -> (texto, id relativo del compositor si lo declara)."""
    title = _label(rec.get("label"))
    lines = [f"RISM {relpath} {title}"]
    stype = rec.get("sourceTypes", {}).get("sourceType", {}).get("typeLabel")
    if stype:
        lines.append(f"tipo: {_label(stype)}")
    creator = rec.get("creator") or {}
    related = creator.get("relatedTo") or {}
    comp_rel = None
    if related.get("id"):
        comp_rel = _relpath(related["id"])
        role = _label((creator.get("role") or {}).get("label"))
        lines.append(f"compositor: {_label(related.get('label'))} [{comp_rel}]" + (f" ({role})" if role else ""))
    fecha = _fecha(rec.get("contents") or {})
    if fecha:
        lines.append(f"fecha: {fecha}")
    for h in (rec.get("exemplars") or {}).get("items", []):
        held = h.get("heldBy") or {}
        inst = _label(held.get("label")) or held.get("siglum", "")
        lines.append(f"institución: {held.get('siglum', '')} {inst}".strip())
        for s in h.get("summary", []):
            if "Shelfmark" in (s.get("label", {}).get("en") or []) or "Signatura" in (s.get("label", {}).get("es") or []):
                lines.append(f"signatura: {_label(s.get('value'))}")
    return "\n".join(lines), comp_rel


def phase0(sample: int = 10) -> dict:
    http = Http("rism", cache=False, cfg=DEFAULT_CFG)
    http._wait()
    robots = robots_report(http, DEFAULT_CFG["endpoint"], ["/sources/30022608", "/works/", "/people/30009629", "/search?q=tonadilla", "/api/"])
    rels = _source_sample(http, SEED_PERSON, sample)
    if len(rels) < sample:
        from ..db import connect
        con = connect()
        for qid, rid in corpus_external_ids(con, WD_PROP, XKEY, types=("person",)).items():
            if len(rels) >= sample:
                break
            rels += [r for r in _source_sample(http, rid, sample - len(rels)) if r not in rels]
    records = []
    for rel in rels[:sample]:
        rec = _get(http, rel)
        if rec:
            records.append({"id": rel, "label": _label(rec.get("label"))})
    return {
        "source": "rism", "endpoint": DEFAULT_CFG["endpoint"], "robots": robots,
        "terms": "https://rism.online/docs/api/resource-api/ (Resource API, JSON-LD vía Accept); "
                 "robots.txt permite /sources/ y /works/ pero prohíbe /search? (Search API oficial) y /api/: "
                 "no se usa búsqueda de texto libre, solo navegación por ID ya conocido",
        "sample_n": len(records), "sample": records,
        "license": "CC-BY-NC-SA-4.0 (textos propios de RISM; rism.info/.../how-to-cite-rism-records.html)",
        "reuse": "consult_only", "checked_at": now(),
    }


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http("rism")
    cfg = http.cfg
    stats = {"persons": 0, "sources": 0, "documents": 0, "claims": 0}
    persons = corpus_external_ids(con, WD_PROP, XKEY, types=("person",))
    for qid, person_rel in persons.items():
        if limit is not None and stats["sources"] >= limit:
            break
        remaining = (limit - stats["sources"]) if limit is not None else 50
        rels = _source_sample(http, person_rel, remaining)
        if rels:
            stats["persons"] += 1
        for rel in rels:
            rec = _get(http, rel)
            if not rec:
                continue
            text, comp_rel = _render(rel, rec)
            num = rel.rsplit("/", 1)[-1]
            sid = f"RISM:{num}"
            doc_id, new = capture(con, source="rism", url=f"{cfg['endpoint']}/{rel}", stable_id=rel, content=text,
                                  license=cfg["license"], reuse=cfg["reuse"], mime="application/ld+json")
            stats["documents"] += int(new)
            segment(con, doc_id, lang="es")
            passages = {p["text"]: p for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc_id,))}
            if comp_rel == person_rel:
                M.ensure_entity(con, sid, "other", _label(rec.get("label")), {"rism": rel})
                line = next(l for l in text.splitlines() if l.startswith("compositor:"))
                stats["claims"] += M.add_claim(con, passages, subject=sid, name="composer", value=qid,
                                               vtype="entity", line=line, method=METHOD, confidence=0.8)
                con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, qid))
            stats["sources"] += 1
    con.commit()
    return stats
