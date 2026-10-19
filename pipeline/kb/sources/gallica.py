"""Gallica (BnF): partituras y libretos editados en París, grabaciones y prensa de compañías españolas
de gira (§3.3, §3.6).

API SRU (búsqueda, Dublin Core) e IIIF (manifiesto; solo referencia, igual que el resto de medios: la
página, la imagen y el audio NUNCA se descargan, solo se enlazan). Derechos por objeto: `dc:rights` da
'domaine public'/'public domain' para lo reutilizable; cualquier otro valor (o su ausencia) se trata
como solo consulta -- igual de conservador que `europeana.py`, que es el patrón que sigue este módulo
(incluido el enlace al corpus vía `_objlink.Index`, pensado para registros de catálogo con título y
creador en texto libre, no afirmaciones estructuradas).
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET

from .. import db as kbdb
from ..http import Http
from ..models import Reuse
from ..store import capture, segment
from ..util import now
from ._objlink import Index

NAME = "gallica"
BASE = "https://gallica.bnf.fr"
DEFAULTS = {
    "endpoint": f"{BASE}/SRU", "license": "per-object (dc:rights)", "reuse": "consult_only",
    "rate_limit_per_s": 0.2,  # robots.txt: Crawl-delay 5 para User-agent * (mismo host que la API)
    "queries": ['gallica adj "zarzuela"', 'gallica adj "género chico"', 'gallica adj "teatro lírico español"'],
    "page_size": 50,  # SRU recorta a 50 aunque se pida más (comprobado)
}
NS = {"srw": "http://www.loc.gov/zing/srw/", "dc": "http://purl.org/dc/elements/1.1/"}
IIIF = BASE + "/iiif/ark:/12148/{ark}/manifest.json"
ARK_RE = re.compile(r"ark:/12148/(\w+)")
OPEN_RIGHTS = ("domaine public", "public domain")
DC_FIELDS = ("title", "creator", "date", "description", "language", "rights", "source", "subject", "type",
             "identifier", "relation")


def _cfg() -> dict:
    from ..http import load_sources  # import local: resoluble en pruebas (ver require_source en ingest)
    return {**DEFAULTS, **load_sources().get(NAME, {})}


def reuse_for(rights: list[str]) -> Reuse:
    """Reutilizable solo si `dc:rights` indica dominio público; cualquier otro valor -> solo consulta."""
    low = {r.lower() for r in rights}
    return Reuse.reusable if low & set(OPEN_RIGHTS) else Reuse.consult_only


def _texts(dc_el: ET.Element, field: str) -> list[str]:
    return [(e.text or "").strip() for e in dc_el.findall(f"dc:{field}", NS) if (e.text or "").strip()]


def _dc(record_el: ET.Element) -> dict[str, list[str]]:
    dc_el = record_el.find("srw:recordData/{http://www.openarchives.org/OAI/2.0/oai_dc/}dc", NS)
    if dc_el is None:
        return {f: [] for f in DC_FIELDS}
    return {f: _texts(dc_el, f) for f in DC_FIELDS}


def ark_of(identifiers: list[str]) -> str | None:
    """Ark estable del registro a partir de sus `dc:identifier` (el primero con forma ark:/12148/...)."""
    for i in identifiers:
        m = ARK_RE.search(i)
        if m:
            return m.group(1)
    return None


def _search(http: Http, query: str, start: int, rows: int) -> tuple[str, ET.Element]:
    txt = http.get(http.cfg["endpoint"],
                    {"operation": "searchRetrieve", "version": "1.2", "query": query,
                     "startRecord": start, "maximumRecords": rows}, use_cache=False)
    return txt, ET.fromstring(txt)


def _iiif(http: Http, ark: str) -> str | None:
    """Manifiesto IIIF solo si existe (comprobado con una petición HEAD; no todo lo de Gallica lo tiene)."""
    url = IIIF.format(ark=ark)
    try:
        http._wait()
        return url if http.client.head(url).status_code == 200 else None
    except Exception:
        return None


def _canon_url(ark: str) -> str:
    return f"{BASE}/ark:/12148/{ark}"


def render(dc: dict[str, list[str]], ark: str, iiif: str | None) -> str:
    """Texto canónico del registro: una línea por campo (cada línea es un pasaje citable)."""
    lines = [f"Gallica {ark}"]
    for t in dc["title"][:2]:
        lines.append(f"Título: {t}")
    for c in dc["creator"]:
        lines.append(f"Creador: {c}")
    for d in dc["date"]:
        lines.append(f"Fecha: {d}")
    for t in dc["type"]:
        lines.append(f"Tipo: {t}")
    for l in dc["language"]:
        lines.append(f"Idioma: {l}")
    for d in dc["description"]:
        lines.append(f"Descripción: {d}")
    for s in dc["subject"]:
        lines.append(f"Materia: {s}")
    for s in dc["source"]:
        lines.append(f"Fondo: {s}")
    for r in dc["relation"]:
        lines.append(f"Catálogo: {r}")
    lines.append(f"Derechos: {', '.join(dc['rights']) or 'desconocido'}")
    lines.append(f"Ver en origen: {_canon_url(ark)}")
    if iiif:
        lines.append(f"IIIF (manifiesto, solo referencia): {iiif}")
    return "\n".join(lines)


def _count_reuse(rights_by_ark: dict[str, list[str]]) -> dict:
    out: dict = {}
    for rr in rights_by_ark.values():
        out[reuse_for(rr).value] = out.get(reuse_for(rr).value, 0) + 1
    return out


def _robots(http: Http) -> dict:
    """robots.txt real de gallica.bnf.fr (§3.8.1). Mismo host para la web y la API SRU/IIIF: se
    comprueba de forma literal (no con `urllib.robotparser`, que no interpreta los comodines '*' que
    usa este robots.txt y daría una respuesta 'permitido' engañosa) que las rutas /SRU e /iiif/ que
    pedimos no están entre las vetadas, y que solo /services/engine/search/sru (un alias distinto,
    que no usamos) lo está."""
    http._wait()
    txt = http.client.get(BASE + "/robots.txt").text
    return {
        "url": BASE + "/robots.txt", "crawl_delay": 5 if "Crawl-delay: 5" in txt else None,
        "disallow_services_sru": "Disallow: /services/engine/search/sru?*" in txt,
        "sru_path_used_is_disallowed": "Disallow: /SRU" in txt,
        "iiif_path_disallowed": "Disallow: /iiif" in txt,
        "note": "la API SRU real está en /SRU (no en /services/engine/search/sru, que sí está vetado y no se usa); "
                "/iiif/... no aparece vetado; se respeta igualmente el Crawl-delay declarado como límite de peticiones",
    }


def phase0(sample: int = 10) -> dict:
    cfg = _cfg()
    http = Http(NAME, cache=False, cfg=cfg)
    robots = _robots(http)
    txt, root = _search(http, cfg["queries"][0], 1, sample)
    total = int(root.findtext("srw:numberOfRecords", default="0", namespaces=NS) or 0)
    records = root.findall("srw:records/srw:record", NS)
    dcs = [_dc(r) for r in records]
    arks = [ark_of(dc["identifier"]) for dc in dcs]
    rights_by_ark = {a: dc["rights"] for a, dc in zip(arks, dcs) if a}
    kbdb.DATA.joinpath("phase0").mkdir(parents=True, exist_ok=True)
    kbdb.DATA.joinpath("phase0", "gallica_sample.xml").write_text(txt)
    iiif = None
    if arks and arks[0]:
        iiif_url = IIIF.format(ark=arks[0])
        http._wait()
        iiif = http.client.head(iiif_url).status_code
    return {"source": NAME, "date": now(), "endpoint": cfg["endpoint"], "total_results": total,
            "sample": len(records), "sample_titles": [dc["title"][0] if dc["title"] else "?" for dc in dcs],
            "sample_arks": arks, "rights_in_sample": {a: r for a, r in rights_by_ark.items()},
            "reuse_in_sample": _count_reuse(rights_by_ark), "iiif_manifest_status_first_item": iiif,
            "robots": robots,
            "terms": "Gallica es de acceso libre y gratuito; metadatos descriptivos bajo licencia abierta del "
                     "Estado francés (Licence Ouverte/Open Licence, 2014); derechos de reutilización del propio "
                     "documento digitalizado por objeto (dc:rights: 'domaine public'/'public domain' para lo "
                     "reutilizable)",
            "effective_license": "por objeto (dc:rights); reutilizable solo con 'domaine public'/'public domain'"}


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    from ..http import require_source
    cfg = {**DEFAULTS, **require_source(NAME)}
    http = Http(NAME, cache=False, cfg=cfg)
    idx = Index(con)
    stats = {"documents": 0, "seen": 0, "no_ark": 0, "linked_objects": 0, "links": 0, "reusable": 0,
             "consult_only": 0, "iiif": 0}
    seen: set[str] = set()
    for q in cfg["queries"]:
        start, total = 1, None
        while total is None or start <= total:
            if limit is not None and stats["seen"] >= limit:
                break
            rows = min(cfg["page_size"], (limit - stats["seen"]) if limit is not None else cfg["page_size"])
            _, root = _search(http, q, start, rows)
            if total is None:
                total = int(root.findtext("srw:numberOfRecords", default="0", namespaces=NS) or 0)
            records = root.findall("srw:records/srw:record", NS)
            if not records:
                break
            for rec_el in records:
                if limit is not None and stats["seen"] >= limit:
                    break
                dc = _dc(rec_el)
                ark = ark_of(dc["identifier"])
                if not ark:
                    stats["no_ark"] += 1
                    continue
                if ark in seen:
                    continue
                seen.add(ark)
                stats["seen"] += 1
                reuse = reuse_for(dc["rights"])
                iiif = _iiif(http, ark)
                stats["iiif"] += bool(iiif)
                text = render(dc, ark, iiif)
                doc_id, new = capture(con, source=NAME, url=_canon_url(ark), stable_id=ark, content=text,
                                      license=", ".join(dc["rights"]) or "unknown", reuse=reuse, mime="text/plain",
                                      raw=ET.tostring(rec_el, encoding="unicode").encode())
                stats["documents"] += int(new)
                stats[reuse.value] += 1
                lang = (dc["language"][0][:2] if dc["language"] else None)
                segment(con, doc_id, lang=lang)
                links = idx.link(dc["title"], dc["creator"])
                for e in links:
                    con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, e))
                stats["links"] += len(links)
                stats["linked_objects"] += bool(links)
            start += len(records)
    con.commit()
    return stats
