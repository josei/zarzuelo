"""Ibero-Amerikanisches Institut (Berlín), colección Zarzuela: visor Goobi con >1.600 libretos de los siglos
XIX-XX; las portadas dan denominación de género, fecha y teatro de estreno (§3.3).

Fase 0 (verificada en vivo el 2026-10-07): el visor Goobi expone OAI-PMH en `/viewer/oai` (`Identify`
responde con normalidad: `repositoryName=OAI Frontend`), que sería el mecanismo natural de cosecha masiva —
pero `robots.txt` lo **prohíbe explícitamente** (`Disallow: /viewer/oai`), igual que `/viewer/search/` (la
única vía de filtrar por colección). No se esquiva ninguna de las dos. Sí están permitidos por robots.txt
(comprobado con `robots_report`, sin pedir las rutas prohibidas) los endpoints de un registro ya identificado:
`/viewer/metsresolver?id=PI` (METS/MODS completo) y la API IIIF (`/viewer/api/v1/records/{PI}/manifest`). El
enlace de la colección citado por la propia página del proyecto del instituto
(`/viewer/collections/zarzuela/`) da 404 (el slug no resuelve así) y la portada del visor no enlaza la
colección en el HTML plano; sin `/viewer/search` no hay forma, dentro de robots.txt, de listar qué PI
pertenecen a la colección Zarzuela.

Por eso esta fuente queda `validated: false`: no hay una vía de **descubrimiento** masivo compatible con
robots.txt, solo de **lectura** de un registro ya conocido. El conector soporta una lista `known_pids`
(config) curada a mano -por ejemplo, cruzando con catálogos externos que ya citen el PI concreto- para
ingerir esas citas puntuales vía `metsresolver` (permitido), sin tocar `/viewer/oai` ni `/viewer/search`.
Licencia: no se ha encontrado una declaración de derechos para la colección digitalizada (ver notas en
`config/sources.d/iai_zarzuela.yaml`); se trata como `consult_only` hasta confirmarla.
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET

from ..http import Http
from ..store import capture, segment
from ..util import now
from ._catalog import link_work, robots_report

DEFAULT_CFG = {
    "endpoint": "https://digital.iai.spk-berlin.de/viewer", "license": "derechos no declarados por el repositorio (por confirmar)",
    "reuse": "consult_only", "rate_limit_per_s": 0.1, "known_pids": [],
}
MODS_NS = {"mods": "http://www.loc.gov/mods/v3"}
PROBE_PATHS = ["/viewer/oai", "/viewer/search/", "/viewer/metsresolver", "/viewer/api/v1/records/0/manifest", "/viewer/collections/zarzuela/"]


def _mets_record(http: Http, pid: str) -> dict | None:
    try:
        xml_text = http.get(f"{http.cfg['endpoint']}/metsresolver", {"id": pid})
    except Exception:  # noqa: BLE001
        return None
    root = ET.fromstring(xml_text)
    mods = root.find(".//mods:mods", MODS_NS)
    if mods is None:
        return None
    return {
        "titles": [t.text for t in mods.findall("mods:titleInfo/mods:title", MODS_NS) if t.text],
        "genres": [g.text for g in mods.findall("mods:genre", MODS_NS) if g.text],
        "dates": [d.text for d in mods.findall(".//mods:dateIssued", MODS_NS) if d.text],
        "names": [" ".join(filter(None, (n.findtext("mods:namePart[@type='given']", "", MODS_NS),
                                         n.findtext("mods:namePart[@type='family']", "", MODS_NS))))
                  for n in mods.findall("mods:name", MODS_NS)],
    }


def _render(pid: str, rec: dict) -> str:
    lines = [f"IAI {pid} {rec['titles'][0] if rec['titles'] else ''}"]
    for t in rec["titles"][1:]:
        lines.append(f"título alternativo: {t}")
    for g in rec["genres"]:
        lines.append(f"género: {g}")
    for d in rec["dates"]:
        lines.append(f"fecha: {d}")
    for n in rec["names"]:
        if n.strip():
            lines.append(f"nombre: {n.strip()}")
    return "\n".join(lines)


def phase0(sample: int = 10) -> dict:
    http = Http("iai_zarzuela", cache=False, cfg=DEFAULT_CFG)
    robots = robots_report(http, "https://digital.iai.spk-berlin.de", PROBE_PATHS)
    known = DEFAULT_CFG["known_pids"][:sample]
    records = []
    for pid in known:
        rec = _mets_record(http, pid)
        if rec:
            records.append({"pid": pid, **rec})
    return {
        "source": "iai_zarzuela", "endpoint": DEFAULT_CFG["endpoint"], "robots": robots,
        "terms": "robots.txt prohíbe /viewer/oai (OAI-PMH, Identify responde con normalidad si se consulta) y "
                 "/viewer/search/ (única vía de filtrar por colección); sin esquivarlos no hay descubrimiento "
                 "automatizable de la colección Zarzuela. /viewer/metsresolver y la API IIIF (permitidos) "
                 "sirven el registro completo (METS/MODS) de un PID ya conocido",
        "sample_n": len(records), "sample": records,
        "no_discovery_path": not robots["allowed"].get("/viewer/oai") and not robots["allowed"].get("/viewer/search/"),
        "license": DEFAULT_CFG["license"], "reuse": DEFAULT_CFG["reuse"],
        "validated_recommendation": False, "checked_at": now(),
    }


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    """Sin descubrimiento automatizable (ver phase0): solo ingiere los `known_pids` curados a mano en la config,
    vía `metsresolver` (permitido por robots.txt). Sin `known_pids`, no hace ninguna petición de red."""
    http = Http("iai_zarzuela")
    cfg = http.cfg
    pids = cfg.get("known_pids", [])
    if limit is not None:
        pids = pids[:limit]
    stats = {"documents": 0, "linked": 0, "not_found": 0}
    for pid in pids:
        rec = _mets_record(http, pid)
        if not rec:
            stats["not_found"] += 1
            continue
        text = _render(pid, rec)
        doc_id, new = capture(con, source="iai_zarzuela", url=f"{cfg['endpoint']}/{pid}/", stable_id=pid, content=text,
                              license=cfg["license"], reuse=cfg["reuse"], mime="text/plain")
        stats["documents"] += int(new)
        segment(con, doc_id, lang="es")
        title = rec["titles"][0] if rec["titles"] else None
        creators = [n for n in rec["names"] if n.strip()]
        wid = link_work(con, re.sub(r"\s*\(.*\)\s*$", "", title), creators) if title else None
        if wid:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, wid))
            stats["linked"] += 1
    con.commit()
    return stats
