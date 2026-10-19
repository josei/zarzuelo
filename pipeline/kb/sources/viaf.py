"""VIAF: desambiguación de personas entre catálogos nacionales (escribe `viaf` en entities.external_ids).

Estado fase 0 (2026-10-07): viaf.org responde 403 (reto de Cloudflare) a clientes automáticos, incluido
robots.txt; no se elude. Las estructuras JSON esperadas se basan en la documentación pública de VIAF y NO se
han podido contrastar con respuestas reales: de ahí `validated: false`. Mientras tanto, los IDs VIAF
llegan por Wikidata (P214), ya mapeados por el conector de Wikidata.
"""
from __future__ import annotations

import json
import re
import sqlite3

import httpx

from ..http import Http, load_sources
from ..store import capture, segment
from .bne import corpus_people, merge_external
from ..util import norm

BASE = "https://viaf.org/viaf"
TERMS = "https://viaf.org/viaf/data/"
ROBOTS = "https://viaf.org/robots.txt"
JSON_H = {"Accept": "application/json"}


def _cfg() -> dict:
    return {**load_sources().get("viaf", {}), "rate_limit_per_s": 1}


def lookup_by_source(http: Http, bne_id: str) -> dict | None:
    """Clúster VIAF que contiene el ID BNE dado (coincidencia por ID: fiable)."""
    r = http.get(f"{BASE}/sourceID/BNE%7C{bne_id}/viaf.json", headers=JSON_H)
    return json.loads(r)


def suggest(http: Http, name: str) -> list[dict]:
    r = json.loads(http.get(f"{BASE}/AutoSuggest", params={"query": name}, headers=JSON_H))
    return r.get("result") or []


def cluster(http: Http, viaf_id: str) -> dict:
    return json.loads(http.get(f"{BASE}/{viaf_id}/viaf.json", headers=JSON_H))


def cluster_years(c: dict) -> tuple[int | None, int | None]:
    def y(k):
        m = re.match(r"(\d{4})", str(c.get(k, "")))
        return int(m.group(1)) if m else None
    return y("birthDate"), y("deathDate")


def cluster_text(c: dict) -> str:
    heads = c.get("mainHeadings", {}).get("data", [])
    heads = heads if isinstance(heads, list) else [heads]
    lines = [f"VIAF {c.get('viafID', '')}"]
    lines += [f"encabezamiento: {h.get('text', '')}" for h in heads if h.get("text")]
    for k in ("birthDate", "deathDate", "nameType"):
        if c.get(k):
            lines.append(f"{k}: {c[k]}")
    return "\n".join(lines)


def phase0(sample: int = 10) -> dict:
    http = Http("viaf", cfg=_cfg())
    rep: dict = {"source": "viaf", "terms": TERMS, "endpoint": BASE,
                 "license_effective": "ODC-BY 1.0 (según la página de datos de VIAF/OCLC; no verificable desde aquí)",
                 "sample": [], "validated": False}
    try:
        rep["robots"] = http.get(ROBOTS)[:400]
        rep["sample"] = suggest(http, "Chapí, Ruperto")[:sample]
        rep["validated"] = bool(rep["sample"])
    except httpx.HTTPStatusError as e:
        rep["blocked"] = f"HTTP {e.response.status_code} ({e.response.headers.get('server', '?')}): reto anti-bot; no se elude"
    except httpx.HTTPError as e:
        rep["blocked"] = f"error de red: {e}"
    return rep


def ingest(con: sqlite3.Connection, limit: int | None = None, http: Http | None = None) -> dict:
    """Para personas con ID BNE (y sin viaf) busca su clúster; sin BNE, nombre + fechas vitales únicos."""
    http = http or Http("viaf")
    cfg = http.cfg
    stats = {"checked": 0, "reconciled": 0, "conflicts": 0, "ambiguous": 0, "not_found": 0}
    for p in corpus_people(con):
        if p["ext"].get("viaf"):
            continue
        if limit and stats["checked"] >= limit:
            break
        stats["checked"] += 1
        found = None
        if p["ext"].get("bne"):
            try:
                found = lookup_by_source(http, p["ext"]["bne"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
        elif p["birth"]:
            matches = []
            for cand in suggest(http, p["names"][0]):
                if cand.get("nametype", "personal") != "personal" or not cand.get("viafid"):
                    continue
                c = cluster(http, cand["viafid"])
                b, d = cluster_years(c)
                if b == p["birth"] and (not p["death"] or not d or d == p["death"]) and norm(p["names"][0].split(",")[0]) in norm(cand.get("term", "")):
                    matches.append(c)
            if len(matches) > 1:
                stats["ambiguous"] += 1
            elif matches:
                found = matches[0]
        if not found or not found.get("viafID"):
            stats["not_found"] += 1
            continue
        doc, _ = capture(con, source="viaf", url=f"{BASE}/{found['viafID']}", stable_id=str(found["viafID"]),
                         content=cluster_text(found), license=cfg["license"], reuse=cfg["reuse"],
                         raw=json.dumps(found, ensure_ascii=False).encode())
        segment(con, doc, lang="es")
        r = merge_external(con, p["id"], "viaf", str(found["viafID"]))
        stats["reconciled"] += r == "added"
        stats["conflicts"] += r == "conflict"
    con.commit()
    return stats
