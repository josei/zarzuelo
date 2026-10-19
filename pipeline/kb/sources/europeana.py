"""Europeana (incluye Hispana, filtrando país España): metadatos de carteles, programas, partituras, libretos y fotos.

Política: se guardan metadatos y texto; imágenes/escaneos/PDF/audio solo como referencia (URL + IIIF + derechos por objeto).
La clave (EUROPEANA_API_KEY) viaja en cabecera X-Api-Key, nunca se registra y se elimina de cualquier respuesta guardada
(la API la repite en `apikey`, `link` y `guid`). Sin caché en disco por la misma razón.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from .. import db as kbdb
from ..http import Http
from ..models import Reuse
from ..store import capture, segment
from ..util import now
from ._objlink import Index

NAME = "europeana"
DEFAULTS = {"endpoint": "https://api.europeana.eu/record/v2/search.json", "license": "per-object (rights)",
            "reuse": "consult_only", "rate_limit_per_s": 2,
            "queries": ["zarzuela", '"género chico"', '"teatro lírico"'], "country": "Spain"}
OPEN_RIGHTS = ("publicdomain/mark/", "publicdomain/zero/", "licenses/by/", "licenses/by-sa/")
IIIF = "https://iiif.europeana.eu/presentation{id}/manifest"


def _cfg() -> dict:
    from ..http import load_sources  # import local: resoluble en pruebas (ver require_source en ingest)
    return {**DEFAULTS, **load_sources().get(NAME, {})}


def _key() -> str:
    k = os.environ.get("EUROPEANA_API_KEY", "")
    if not k:
        raise RuntimeError("falta EUROPEANA_API_KEY en .env")
    return k


def scrub(text: str, key: str) -> str:
    return text.replace(key, "REDACTED") if key else text


def reuse_for(rights: str | None) -> Reuse:
    """Reutilizable solo con dominio público/CC0/CC BY/CC BY-SA; NC, ND, InC, RS, NoC… -> solo consulta."""
    r = (rights or "").lower()
    return Reuse.reusable if any(p in r for p in OPEN_RIGHTS) else Reuse.consult_only


def _search(http: Http, key: str, query: str, rows: int, cursor: str = "*") -> dict:
    cfg = http.cfg
    params = {"query": query, "rows": rows, "profile": "standard", "cursor": cursor,
              "qf": f"COUNTRY:{cfg.get('country', 'Spain')}"}
    r = http.json(cfg["endpoint"], params, {"X-Api-Key": key}, use_cache=False)
    r.pop("apikey", None)
    return r


def _lang(item: dict, field: str) -> list[str]:
    """Valores del campo, preferentemente es/def sin duplicados (conserva orden)."""
    la = item.get(field + "LangAware") or {}
    vals = (la.get("es") or []) + (la.get("def") or []) + (la.get("ca") or [])
    vals = vals or item.get(field) or []
    out: list[str] = []
    for v in vals:
        if v not in out:
            out.append(v)
    return out


def resolve_agents(http: Http, key: str, item: dict) -> dict[str, list[str]]:
    """URI de agente -> etiquetas (es/en/ca) leídas del registro completo (la búsqueda solo trae URIs)."""
    uris = [c for c in _lang(item, "dcCreator") if c.startswith("http")]
    if not uris:
        return {}
    try:
        rec = http.json(f"https://api.europeana.eu/record/v2{item['id']}.json", None, {"X-Api-Key": key}, use_cache=False)
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for a in rec.get("object", {}).get("agents", []):
        pl = a.get("prefLabel") or {}
        labels = [v for l in ("es", "en", "ca") for v in pl.get(l, [])]
        if labels:
            out[a["about"]] = list(dict.fromkeys(labels))
    return out


def render(item: dict, key: str, agents: dict[str, list[str]] | None = None, iiif: str | None = None) -> str:
    """Texto canónico del objeto: una línea por campo (cada línea es un pasaje citable)."""
    rights = (item.get("rights") or [""])[0]
    lines = [f"Europeana {item['id']}"]
    for t in _lang(item, "title")[:4]:
        lines.append(f"Título: {t}")
    for c in _lang(item, "dcCreator"):
        lab = (agents or {}).get(c)
        lines.append(f"Creador: {lab[0]} <{c}>" if lab else f"Creador: {c}")
    for y in item.get("year", []):
        lines.append(f"Fecha: {y}")
    lines.append(f"Tipo: {item.get('type', '')}")
    for d in _lang(item, "dcDescription"):
        lines.append(f"Descripción: {d}")
    lines.append(f"Institución: {', '.join(item.get('dataProvider', []))}")
    lines.append(f"Agregador: {', '.join(item.get('provider', []))}")
    lines.append(f"Derechos: {rights}")
    for u in item.get("edmIsShownAt", []):
        lines.append(f"Ver en origen: {u}")
    for u in item.get("edmIsShownBy", []):
        lines.append(f"Medio (solo referencia): {u}")
    if iiif:
        lines.append(f"IIIF (manifiesto, solo referencia): {iiif}")
    return scrub("\n".join(lines), key)


def _iiif(http: Http, key: str, item: dict) -> str | None:
    """Manifiesto IIIF solo si existe (comprobado con una petición)."""
    u = IIIF.format(id=item["id"])
    try:
        http._wait()
        return u if http.client.head(u, headers={"X-Api-Key": key}).status_code == 200 else None
    except Exception:
        return None


def _canon_url(item: dict) -> str:
    return re.sub(r"\?.*$", "", item["guid"]) if item.get("guid") else f"https://www.europeana.eu/item{item['id']}"


def _count_reuse(rights: dict) -> dict:
    out: dict = {}
    for rr, n in rights.items():
        out[reuse_for(rr).value] = out.get(reuse_for(rr).value, 0) + n
    return out


def _fetch_robots(http: Http, base: str) -> str:
    http._wait()
    return http.client.get(base.rstrip("/") + "/robots.txt").text


def _robots(http: Http) -> dict:
    """robots.txt real de los hosts implicados (§3.8.1). No se usa `urllib.robotparser` para decidir
    `allowed`: su coincidencia es un simple prefijo literal y no interpreta comodines '*', que es
    justo lo que usan estas reglas (p. ej. 'Disallow: /api/*') -- un 'permitido' suyo sería engañoso.
    Se guarda el texto real y se razona sobre las líneas literales."""
    www = _fetch_robots(http, "https://www.europeana.eu")
    iiif_txt = _fetch_robots(http, "https://iiif.europeana.eu")
    return {
        "www.europeana.eu": {"url": "https://www.europeana.eu/robots.txt",
                              "disallow_api": "Disallow: /api/*" in www, "disallow_iiif": "Disallow: /iiif/*" in www},
        "iiif.europeana.eu": {"url": "https://iiif.europeana.eu/robots.txt", "text": iiif_txt.strip()},
        "note": "www.europeana.eu veta /api/* e /iiif/* a rastreadores genéricos (por eso se usa la API "
                "documentada con clave, no rastreo web); api.europeana.eu no sirve robots.txt propio "
                "(redirige a una página); iiif.europeana.eu permite todo ('Disallow:' vacío)",
    }


def phase0(sample: int = 10) -> dict:
    key = _key()
    cfg = _cfg()
    http = Http(NAME, cache=False, cfg=cfg)
    robots = _robots(http)
    r = _search(http, key, cfg["queries"][0], sample)
    items = r.get("items", [])
    kbdb.DATA.joinpath("phase0").mkdir(parents=True, exist_ok=True)
    kbdb.DATA.joinpath("phase0", "europeana_sample.json").write_text(scrub(json.dumps(items, ensure_ascii=False, indent=1), key))
    rights = {}
    for i in items:
        rr = (i.get("rights") or ["?"])[0]
        rights[rr] = rights.get(rr, 0) + 1
    iiif = None
    if items:
        http._wait()
        iiif = http.client.head(IIIF.format(id=items[0]["id"]), headers={"X-Api-Key": key}).status_code
    return {"source": NAME, "date": now(), "endpoint": cfg["endpoint"], "total_results": r.get("totalResults"),
            "sample": len(items),
            "sample_titles": [(i.get("title") or ["?"])[0] for i in items], "rights_in_sample": rights,
            "reuse_in_sample": _count_reuse(rights),
            "providers": sorted({p for i in items for p in i.get("provider", [])}),
            "iiif_manifest_status_first_item": iiif,
            "robots": robots,
            "terms": "API con clave gratuita (Europeana API Terms of Use); metadatos CC0; robots.txt de www.europeana.eu "
                     "prohíbe rastrear /api e /iiif (se usa la API con clave, no rastreo web); derechos por objeto",
            "effective_license": "por objeto (campo rights); reutilizable solo PDM/CC0/CC BY/CC BY-SA"}


def ingest(con: sqlite3.Connection, limit: int | None) -> dict:
    from ..http import require_source
    cfg = require_source(NAME)
    cfg = {**DEFAULTS, **cfg}
    key = _key()
    http = Http(NAME, cache=False, cfg=cfg)
    idx = Index(con)
    stats = {"documents": 0, "seen": 0, "linked_objects": 0, "links": 0, "reusable": 0, "consult_only": 0, "iiif": 0}
    seen: set[str] = set()
    for q in cfg["queries"]:
        cursor = "*"
        while cursor and (limit is None or stats["seen"] < limit):
            r = _search(http, key, q, min(100, (limit or 100) - stats["seen"]) if limit else 100, cursor)
            for it in r.get("items", []):
                if it["id"] in seen or (limit is not None and stats["seen"] >= limit):
                    continue
                seen.add(it["id"])
                stats["seen"] += 1
                rights = (it.get("rights") or [""])[0] or "unknown"
                reuse = reuse_for(rights)
                agents = resolve_agents(http, key, it)
                iiif = _iiif(http, key, it)
                stats["iiif"] += bool(iiif)
                text = render(it, key, agents, iiif)
                raw = scrub(json.dumps(it, ensure_ascii=False), key).encode()
                doc_id, new = capture(con, source=NAME, url=_canon_url(it), stable_id=it["id"], content=text,
                                      license=rights, reuse=reuse, mime="text/plain", raw=raw)
                stats["documents"] += int(new)
                stats[reuse.value] += 1
                segment(con, doc_id, lang="es")
                cr = [x for c in _lang(it, "dcCreator") for x in (c, *agents.get(c, []))]
                links = idx.link(_lang(it, "title"), cr)
                for e in links:
                    con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, e))
                stats["links"] += len(links)
                stats["linked_objects"] += bool(links)
            cursor = r.get("nextCursor")
            if not r.get("items"):
                break
    con.commit()
    return stats
