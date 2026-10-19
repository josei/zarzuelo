"""Hemeroteca Digital BNE: datasets "Hemeroteca digital. Texto completo" y "Hemeroteca digital. Listado de
títulos" (plan §3.4). Más de 2.000 cabeceras de prensa de dominio público con OCR, CC0 según el catálogo CKAN;
sería la fuente clave de crónicas de estrenos, críticas y carteleras.

Estado de la fase 0 (2026-10-07):
- El catálogo CKAN de `datosabiertos.bne.es` (`/catalogo/api/3/action/package_search`) da las URLs reales de
  los dos datasets (comprobado a mano con `curl -k`, ver `FILES`), pero la cadena TLS de ese host está
  incompleta (falta la intermedia) y un cliente que valida certificados —el nuestro, correctamente— no puede
  conectar en absoluto.
- Los ficheros en sí viven en `hemerotecadigital.bne.es`, que devuelve 403 (reto de Cloudflare) a clientes
  automáticos Y cuyo `robots.txt` declara `Disallow: /` para cualquier robot salvo Googlebot/Bingbot: doble
  bloqueo.
- Ninguna de las dos protecciones se elude (AGENTS.md): `validated: false`.

Si alguien descarga esos ficheros a mano (un navegador normal sí pasa el reto de Cloudflare) y los coloca en
`data/hemeroteca_bne/<texto_completo|listado_titulos>.csv` (o `.csv.gz` / `.zip`), `ingest()` los lee sin red,
en streaming y filtrando por relevancia temática (§1 bis), igual que `bne.py` con sus propios datasets (de
hecho reutiliza su lector de ZIP para el caso `.zip`). No se conocen los nombres de columna reales (no se ha
podido descargar ni una muestra): `render()` vuelca todas las columnas tal cual, sin asumir nombres."""
from __future__ import annotations

import csv
import gzip
import json
import re
import sqlite3
import urllib.robotparser
from pathlib import Path
from typing import Iterator

import httpx

from .. import db as kbdb
from ..http import Http, load_sources
from ..store import capture, segment
from ..util import norm, sha256
from . import bne as B

NAME = "hemeroteca_bne"
CKAN_API = "https://datosabiertos.bne.es/catalogo/api/3/action/package_search"
# Recursos reales obtenidos del CKAN (2026-10-07); el propio catálogo repite la URL de fulltext-csv en ambos
# datasets (no es una errata nuestra).
FILES = {
    "texto_completo": "https://hemerotecadigital.bne.es/hd/es/fulltext-csv",
    "listado_titulos": "https://hemerotecadigital.bne.es/hd/es/fulltext-csv",
}
HTML_PAGES = {
    "texto_completo": "https://hemerotecadigital.bne.es/hd/fulltext",
    "listado_titulos": "https://hemerotecadigital.bne.es/hd/es/fulltext",
}
ROBOTS = "https://hemerotecadigital.bne.es/robots.txt"
TERMS = "https://www.bne.es/es/aviso-legal"
AGENT = "zarzuelo-kb"
SUFFIXES = (".csv", ".csv.gz", ".zip")

TOPIC = re.compile(r"zarzuela|tonadilla|sainete l[ií]rico|g[eé]nero (chico|[ií]nfimo)|opereta|[oó]pera espa[nñ]ola|"
                   r"sarsuela|teatro l[ií]rico|revista musical|libreto|estreno|cartelera", re.I)
OCR_HINTS = ("ocr", "confianza", "confidence")
TITLE_HINTS = ("titulo", "cabecera", "title")


def phase0(sample: int = 10) -> dict:
    http = Http(NAME, cache=False, cfg={"rate_limit_per_s": 1})
    rep: dict = {"source": NAME, "terms": TERMS, "ckan_api": CKAN_API, "files": FILES, "html_pages": HTML_PAGES,
                 "validated": False, "sample": []}
    try:
        r = json.loads(http.get(CKAN_API, {"q": "hemeroteca", "rows": 5}, use_cache=False))
        rep["ckan_datasets"] = [p["name"] for p in r["result"]["results"]]
    except httpx.HTTPError as e:
        # esperado: cadena TLS incompleta en datosabiertos.bne.es (verificado a mano con `curl -k`)
        rep["ckan_blocked"] = f"{type(e).__name__}: {e}"
    try:
        txt = http.get(ROBOTS, use_cache=False)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        rep["robots_excerpt"] = txt[:300]
        rep["robots_allows"] = rp.can_fetch(AGENT, FILES["texto_completo"])
    except httpx.HTTPError as e:
        rep["robots_blocked"] = str(e)
    try:
        content = http.get(FILES["texto_completo"], use_cache=False)
        rep["sample"] = list(csv.DictReader(content.splitlines()))[:sample]
        rep["validated"] = len(rep["sample"]) > 0
    except httpx.HTTPStatusError as e:
        rep["blocked"] = f"HTTP {e.response.status_code} ({e.response.headers.get('server', '?')}): reto anti-bot (Cloudflare); no se elude"
    except httpx.HTTPError as e:
        rep["blocked"] = f"error de red: {e}"
    rep["license_effective"] = "CC0-1.0 (declarada en el catálogo CKAN de datosabiertos.bne.es)"
    rep["manual_fallback"] = f"descarga a mano -> data/hemeroteca_bne/<texto_completo|listado_titulos>{{{','.join(SUFFIXES)}}}"
    return rep


def _find_local(name: str) -> Path | None:
    base = kbdb.DATA / "hemeroteca_bne"
    for suf in SUFFIXES:
        p = base / f"{name}{suf}"
        if p.exists():
            return p
    return None


def read_records(path: Path) -> Iterator[dict]:
    """Lee el fichero descargado a mano: CSV plano, `.csv.gz` o `.zip` (reutiliza `bne.read_records` para ZIP)."""
    if path.suffix == ".zip":
        yield from B.read_records(path, "csv")
    elif path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
            yield from csv.DictReader(f)
    else:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            yield from csv.DictReader(f)


def render(rec: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in rec.items() if v)


def _col(rec: dict, hints: tuple[str, ...]) -> str | None:
    cols = {norm(k): k for k in rec if k}
    for h in hints:
        for nk, k in cols.items():
            if nk == h or (len(h) > 3 and h in nk):
                return k
    return None


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    """Lee los ficheros que haya en `data/hemeroteca_bne/` (ninguna petición de red: ver docstring del módulo)."""
    cfg = load_sources().get(NAME) or {"license": "CC0-1.0", "reuse": "consult_only"}
    stats = {"datasets_found": 0, "scanned": 0, "matched": 0, "documents": 0}
    for name in FILES:
        path = _find_local(name)
        if not path:
            continue
        stats["datasets_found"] += 1
        for rec in read_records(path):
            stats["scanned"] += 1
            text = render(rec)
            if not TOPIC.search(text):
                continue
            stats["matched"] += 1
            tcol = _col(rec, TITLE_HINTS)
            sid = (rec.get(tcol) if tcol else "") or f"{name}:{sha256(text)[:16]}"
            doc_id, new = capture(con, source=NAME, url=f"{FILES[name]}#{sid}", stable_id=f"{name}:{sid}",
                                  content=text, license=cfg["license"], reuse=cfg["reuse"], mime="text/plain",
                                  raw=json.dumps(rec, ensure_ascii=False).encode())
            stats["documents"] += int(new)
            segment(con, doc_id, lang="es")
            ocol = _col(rec, OCR_HINTS)
            if ocol and rec.get(ocol):
                try:
                    conf = float(str(rec[ocol]).replace(",", ".").rstrip("%"))
                    con.execute("UPDATE passages SET ocr_conf=? WHERE document_id=?",
                               (conf / 100 if conf > 1 else conf, doc_id))
                except ValueError:
                    pass
            if limit and stats["matched"] >= limit:
                break
        if limit and stats["matched"] >= limit:
            break
    con.commit()
    return stats
