"""Biblioteca Virtual de Prensa Histórica (Ministerio de Cultura): catálogo de cabeceras y números de prensa
histórica española, vía OAI-PMH 2.0 (plan §3.4, §3.8). Verificado a mano el 2026-10-07: el endpoint real es
`/oai/oai.do` (no está enlazado desde la web; `Identify`/`ListSets`/`ListRecords` responden con XML válido).
No hay `robots.txt` (404): sin restricciones declaradas. El aviso legal dice que la mayoría de las obras se
ofrecen en CC BY 4.0, pero la licencia es por registro (`dc:rights`); se usa tal cual, con `consult_only` por
defecto si no se declara una licencia abierta.

Solo se guardan los metadatos catalográficos (título, fecha, lugar, editor, derechos) y el enlace a la ficha;
las imágenes de las páginas y su OCR son medios y NO se descargan (política de §4/AGENTS.md). Los registros
OAI de la BVPH son a nivel de cabecera/número, no de artículo: no hay enlace fiable a entidades del corpus
desde aquí, así que no se generan afirmaciones, solo documentos de referencia (igual que el `bne.py` para
catálogos: capturar + segmentar, sin inventar vínculos)."""
from __future__ import annotations

import json
import sqlite3
import urllib.robotparser

import httpx

from ..http import Http
from ..store import capture, segment
from ..util import now
from . import _oai as O

NAME = "bvph"
BASE = "https://prensahistorica.mcu.es/oai/oai.do"
DEFAULT_CFG = {"endpoint": BASE, "license": "por registro (dc:rights); mayoría CC BY 4.0 según aviso legal",
               "reuse": "consult_only", "rate_limit_per_s": 1}
TERMS = "https://prensahistorica.mcu.es/es/contenido/aviso-legal.do"
ROBOTS = "https://prensahistorica.mcu.es/robots.txt"
AGENT = "zarzuelo-kb"
# ListSets reales (2026-10-07); se excluyen los de prueba del propio repositorio (prueba_recoleccion, Pueba_1registro)
SETS = ["prensahistorica1", "prensahistorica2", "prensahistorica3", "prensahistorica4", "prensahistorica5",
        "prensa_revistas", "culturales_articulos", "americanae", "filipinas"]


def phase0(sample: int = 10) -> dict:
    http = Http(NAME, cache=False, cfg=DEFAULT_CFG)
    rep: dict = {"source": NAME, "endpoint": BASE, "terms": TERMS, "checked_at": now()}
    try:
        txt = http.get(ROBOTS, use_cache=False)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        rep["robots_allows"] = rp.can_fetch(AGENT, BASE)
    except httpx.HTTPStatusError as e:
        rep["robots"] = f"HTTP {e.response.status_code}: no hay robots.txt declarado (sin restricciones)"
    rep["identify"] = O.identify(http, BASE)
    rep["sets_n"] = len(O.list_sets(http, BASE))
    recs = [r for r in O.list_records(http, BASE, "oai_dc", set_spec="prensahistorica5", limit=sample * 2)
            if not r["deleted"]][:sample]
    rights: dict[str, int] = {}
    for r in recs:
        for v in r["dc"].get("rights", ["sin declarar"]):
            rights[v] = rights.get(v, 0) + 1
    rep["sample_n"] = len(recs)
    rep["sample"] = [{"identifier": r["identifier"], "title": (r["dc"].get("title") or ["?"])[0],
                      "date": (r["dc"].get("date") or [""])[0], "rights": r["dc"].get("rights")} for r in recs]
    rep["rights_in_sample"] = rights
    rep["license_effective"] = ("por registro (campo dc:rights); el aviso legal declara que la mayoría de obras "
                                "se ofrece en CC BY 4.0 (Creative Commons Atribución 4.0), con excepciones objeto "
                                "a objeto")
    rep["validated"] = len(recs) > 0
    return rep


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(NAME)
    stats = {"scanned": 0, "documents": 0, "reusable": 0, "consult_only": 0, "deleted": 0}
    seen: set[str] = set()
    for s in SETS:
        if limit is not None and stats["scanned"] >= limit:
            break
        remaining = (limit - stats["scanned"]) if limit is not None else None
        for rec in O.list_records(http, BASE, "oai_dc", set_spec=s, limit=remaining):
            if rec["identifier"] in seen:
                continue
            seen.add(rec["identifier"])
            if rec["deleted"]:
                stats["deleted"] += 1
                continue
            text = O.render_dc(rec)
            reuse, rights = O.reuse_for(rec["dc"].get("rights"))
            url = (rec["dc"].get("identifier") or [f"{BASE}#{rec['identifier']}"])[0]
            doc_id, new = capture(con, source=NAME, url=url, stable_id=rec["identifier"], content=text,
                                  license=rights, reuse=reuse, mime="text/plain",
                                  raw=json.dumps(rec, ensure_ascii=False).encode())
            stats["scanned"] += 1
            stats["documents"] += int(new)
            stats[reuse.value] += 1
            segment(con, doc_id, lang="es")
            if limit is not None and stats["scanned"] >= limit:
                break
    con.commit()
    return stats
