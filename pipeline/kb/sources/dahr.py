"""DAHR — Discography of American Historical Recordings (UCSB/Packard Humanities Institute): grabaciones de
78 rpm (Columbia, Victor...) con matrices, fechas e intérpretes; rica en zarzuela y cuplé (§3.1).

Fase 0 (verificada en vivo el 2026-10-07): `adp.library.ucsb.edu` devuelve **403 "Request blocked" (CloudFront)**
a nuestro User-Agent identificable (`zarzuelo-kb/0.1 ...`), incluso para `/robots.txt`. La misma petición con un
User-Agent de navegador habitual responde 200: es un bloqueo por cadena de User-Agent (WAF de borde), no una
caída puntual del servidor. Conforme a la política del proyecto, **no se suplanta un navegador para
esquivarlo**: la fuente queda `validated: false` y este conector no hace ninguna petición de red (ni siquiera
de verificación) para no insistir contra un bloqueo ya confirmado. Si UCSB habilita en el futuro acceso para
clientes automatizados identificados (export, API o excepción de IP/UA), hay que repetir la fase 0.

No se ha encontrado documentación de una API o exportación de datos (plan §3.1: "verificar si ofrece
exportación"); los términos de la web indican "free of charge for... private and institutional users...
personal, non-commercial use only; sale or other commercial... distribution... prohibited", consistente con
`consult_only` si el acceso se habilitara.
"""
from __future__ import annotations

import sqlite3

from ..util import now

DEFAULT_CFG = {
    "endpoint": "https://adp.library.ucsb.edu", "license": "sin licencia de reutilización explícita (uso personal, no comercial)",
    "reuse": "consult_only", "rate_limit_per_s": 0,
}


def phase0(sample: int = 10) -> dict:
    """No descarga nada: el bloqueo por User-Agent ya está confirmado (ver docstring) y no se reintenta sin permiso."""
    return {
        "source": "dahr", "endpoint": DEFAULT_CFG["endpoint"],
        "blocked": True, "blocked_reason": "403 CloudFront ('Request blocked') al User-Agent identificable zarzuelo-kb, "
                                           "confirmado también en /robots.txt; un User-Agent de navegador obtiene 200 "
                                           "(bloqueo por cadena de UA, no evadido)",
        "terms": "Sin API ni exportación documentada (plan §3.1). Términos de uso: solo personal/no comercial, "
                 "prohibida la distribución comercial",
        "sample_n": 0, "sample": [],
        "license": DEFAULT_CFG["license"], "reuse": DEFAULT_CFG["reuse"],
        "validated_recommendation": False, "checked_at": now(),
    }


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    """La fuente no está validada (bloqueo de acceso, ver phase0): no se hace ninguna petición de red."""
    return {"blocked": True, "documents": 0, "attempted": 0}
