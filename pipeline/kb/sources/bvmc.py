"""Biblioteca Virtual Miguel de Cervantes: catálogo RDF/FRBR con teatro breve, libretos y estudios (§3.3).

Fase 0 (verificada en vivo el 2026-10-07): el endpoint de datos enlazados documentado,
`http://data.cervantesvirtual.com/sparql`, está protegido por un **reto de Cloudflare** ("Just a moment...",
JS/cookie `cf_clearance`) para clientes automatizados — no se esquiva (nada de navegador headless ni
cookies de reto). Las páginas de recurso individuales (`data.cervantesvirtual.com/person/N`, `/work/N`…) SÍ
responden 200, pero son la plantilla HTML de "BVMC.Labs": no ofrecen el RDF real por negociación de
contenido (`Accept: application/rdf+xml` / `application/ld+json` devuelven la misma página HTML) y solo
incluyen un `<script type="application/ld+json">` genérico de SEO (`WebPage`/`BreadcrumbList`), sin los
datos FRBR/RDA reales. Extraer esos datos exigiría raspar HTML no documentado, frágil y fuera de lo que
puede verificarse como "endpoint real" en esta fase. Queda `validated: false`; si Cloudflare libera el
`/sparql` (o la biblioteca habilita una API), repetir la fase 0.
"""
from __future__ import annotations

import sqlite3

from ..http import Http
from ..util import now

DEFAULT_CFG = {
    "endpoint": "http://data.cervantesvirtual.com/sparql", "license": "sin licencia de reutilización explícita",
    "reuse": "consult_only", "rate_limit_per_s": 0.5,
}
CLOUDFLARE_MARKERS = ("just a moment", "cf-mitigated", "cf_clearance", "/cdn-cgi/challenge-platform/")


def _blocked_by_cloudflare(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in CLOUDFLARE_MARKERS)


def phase0(sample: int = 10) -> dict:
    http = Http("bvmc", cache=False, cfg=DEFAULT_CFG)
    http._wait()
    try:
        r = http.client.get(DEFAULT_CFG["endpoint"], params={
            "query": "SELECT ?s WHERE { ?s a <http://purl.org/NET/c4dm/event.owl#Event> } LIMIT 1",
        }, headers={"Accept": "application/sparql-results+json"})
        blocked = r.status_code != 200 or _blocked_by_cloudflare(r.text)
        detail = f"HTTP {r.status_code}" + (" + reto Cloudflare" if _blocked_by_cloudflare(r.text) else "")
    except Exception as e:  # noqa: BLE001
        blocked, detail = True, str(e)[:200]
    http._wait()
    res_page = http.client.get("https://data.cervantesvirtual.com/person/1234")
    res_has_rdf = res_page.status_code == 200 and not _blocked_by_cloudflare(res_page.text) and "application/ld+json" not in res_page.headers.get("content-type", "")
    return {
        "source": "bvmc", "endpoint": DEFAULT_CFG["endpoint"], "blocked": blocked, "blocked_detail": detail,
        "resource_pages_reachable": res_page.status_code == 200,
        "resource_pages_expose_rdf": False,  # solo HTML de BVMC.Labs + JSON-LD genérico de SEO; comprobado a mano
        "terms": "SPARQL documentado en data.cervantesvirtual.com/sparql, protegido por reto Cloudflare; "
                 "páginas de recurso individuales accesibles pero sin RDF real vía negociación de contenido",
        "sample_n": 0, "sample": [],
        "license": DEFAULT_CFG["license"], "reuse": DEFAULT_CFG["reuse"],
        "validated_recommendation": False, "checked_at": now(),
    }


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    """La fuente no está validada (reto Cloudflare en /sparql, ver phase0): no se hace ninguna petición de red."""
    return {"blocked": True, "documents": 0, "attempted": 0}
