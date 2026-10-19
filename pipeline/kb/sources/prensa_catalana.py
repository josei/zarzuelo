"""Prensa catalana (plan §3.4): el plan apunta a Catalònica y ARCA (Biblioteca de Catalunya) y, vía OAI-PMH, a
la Memòria Digital de Catalunya (MDC, CSUC). Verificado a mano el 2026-10-07:

- `catalonica.bnc.cat` y `arca.bnc.cat` declaran `Disallow: /` en su `robots.txt` para cualquier robot no
  listado expresamente (solo permiten Googlebot, Bingbot, DuckDuckBot, archive.org_bot y CCBot); nuestro
  User-Agent no es ninguno de esos, así que quedan bloqueados. Tampoco exponen un endpoint `/oai/` propio
  (404 en ambos). No se elude: `validated: false` para estos dos.
- `mdc.csuc.cat` SÍ permite `/oai/` en su `robots.txt` (solo bloquea rutas `/digital/*/search/`) y su OAI-PMH
  2.0 (`/oai/oai.php`) funciona (`Identify`, `ListSets` con 237 colecciones, `ListRecords`).

MDC no es un fondo de prensa histórica: es el agregador de fondos digitalizados de la Biblioteca de Catalunya
y otras instituciones catalanas (no hay ninguna colección general de diarios/revistas con OCR). Entre sus
colecciones hay varias con material de teatro líric/sarsuela catalana genuino (comprobado a mano, no
inventado): `felippedrell` (fondo Felip Pedrell, con zarzuelas y tragicomedias líricas), `fulletsblUB`
("Fullets de teatre" de la UB, con piezas explícitamente etiquetadas "sarsuela" o "cómich-lírich"),
`partiturBC` (partituras, con piezas "cómico-líricas"), `enricmorera`/`egranados`/`fonsalbeniz`/`jmarticrist`
(fondos de compositores). `Operistica` ("Llibrets operístics 1820-1900") y `discografic` están en MDC pero sus
~200 registros han sido *todos* borrados (`status="deleted"` en OAI): se excluyen. `jribot` (Josep Ribot i
Calpe) es un fondo de dibujos botánicos sin relación: se excluye.

Solo se cosechan estas colecciones acotadas, filtrando cada registro por `_oai.TOPIC` (igual criterio que
`bne.py`): no se vuelca MDC entero. Metadatos y texto solamente; las imágenes a las que apuntan los registros
no se descargan."""
from __future__ import annotations

import json
import sqlite3

from ..http import Http
from ..store import capture, segment
from ..util import now
from . import _catalog as C
from . import _oai as O

NAME = "prensa_catalana"
MDC_BASE = "https://mdc.csuc.cat/oai/oai.php"
CATALONICA = "https://catalonica.bnc.cat"
ARCA = "https://arca.bnc.cat"
DEFAULT_CFG = {"endpoint": MDC_BASE, "license": "por registro (dc:rights): PublicDomainMark/CC/InC",
               "reuse": "consult_only", "rate_limit_per_s": 1}
# Colecciones de MDC con teatro líric/sarsuela catalana confirmadas a mano (ver docstring); Operistica y
# discografic quedan fuera por estar enteramente borradas, jribot por no tener relación temática.
SETS = ["felippedrell", "enricmorera", "egranados", "fonsalbeniz", "jmarticrist", "fulletsblUB", "partiturBC"]


def phase0(sample: int = 10) -> dict:
    http = Http(NAME, cache=False, cfg=DEFAULT_CFG)
    rep: dict = {"source": NAME, "checked_at": now(), "endpoints": {}}
    for name, base in (("catalonica", CATALONICA), ("arca", ARCA)):
        r = C.robots_report(http, base, ["/", "/oai/oai.php"])
        r["blocked_for_us"] = not r["allowed"].get("/", True)
        rep["endpoints"][name] = r
    ident = O.identify(http, MDC_BASE)
    recs = []
    for s in SETS:
        for r in O.list_records(http, MDC_BASE, "oai_dc", set_spec=s, limit=max(sample * 3, 30)):
            if not r["deleted"] and O.matches_topic(r):
                recs.append(r)
            if len(recs) >= sample:
                break
        if len(recs) >= sample:
            break
    rep["endpoints"]["mdc"] = {
        "endpoint": MDC_BASE, "identify": ident, "sets_checked": SETS, "sample_n": len(recs),
        "sample": [{"identifier": r["identifier"], "sets": r["sets"], "title": (r["dc"].get("title") or ["?"])[0],
                    "rights": r["dc"].get("rights")} for r in recs],
    }
    rep["validated"] = len(recs) > 0
    rep["license_effective"] = "por registro (dc:rights de MDC): PublicDomainMark, Creative Commons, o rightsstatements.org/InC"
    rep["terms"] = ("catalonica.bnc.cat y arca.bnc.cat: robots.txt Disallow: / (salvo buscadores listados), sin "
                    "endpoint OAI propio; mdc.csuc.cat: robots.txt permite /oai/ (solo bloquea /digital/*/search/)")
    return rep


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(NAME)
    stats = {"scanned": 0, "matched": 0, "documents": 0, "reusable": 0, "consult_only": 0}
    for s in SETS:
        if limit is not None and stats["matched"] >= limit:
            break
        for rec in O.list_records(http, MDC_BASE, "oai_dc", set_spec=s):
            stats["scanned"] += 1
            if rec["deleted"] or not O.matches_topic(rec):
                continue
            text = O.render_dc(rec)
            reuse, rights = O.reuse_for(rec["dc"].get("rights"))
            url = (rec["dc"].get("identifier") or [f"{MDC_BASE}#{rec['identifier']}"])[0]
            doc_id, new = capture(con, source=NAME, url=url, stable_id=rec["identifier"], content=text,
                                  license=rights, reuse=reuse, mime="text/plain",
                                  raw=json.dumps(rec, ensure_ascii=False).encode())
            stats["matched"] += 1
            stats["documents"] += int(new)
            stats[reuse.value] += 1
            segment(con, doc_id, lang="ca")
            if limit is not None and stats["matched"] >= limit:
                break
    con.commit()
    return stats
