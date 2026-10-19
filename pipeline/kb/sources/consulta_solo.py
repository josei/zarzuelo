"""Referencias citables de fuentes `consult_only` (fase 0, plan §3.8 y §10).

No descarga nada: solo construye la cita (URL permanente, título, fecha de consulta).
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

REUSE = "consult_only"

_FUENTES = {
    "iccmu_diccionario_zarzuela": {
        "institucion": "Instituto Complutense de Ciencias Musicales (ICCMU)",
        "obra": "Diccionario de la Zarzuela (edición digital)",
        "licencia": "sin licencia de reutilización explícita",
    },
    "juan_march_teatro_musical": {
        "institucion": "Fundación Juan March",
        "obra": "El Teatro Musical Español (colección digital)",
        "licencia": "sin licencia de reutilización explícita (uso académico y docente, con cita)",
    },
    "cdaem_musicadanza": {
        "institucion": "CDAEM (INAEM, Ministerio de Cultura)",
        "obra": "musicadanza.es, fondos documentales: programas de mano",
        "licencia": "sin licencia de reutilización explícita",
    },
    "espinos_memoria_madrid": {
        "institucion": "Biblioteca Musical Víctor Espinós / Memoria de Madrid (Ayuntamiento de Madrid)",
        "obra": "Memoria de Madrid, biblioteca digital",
        "licencia": "CC BY-NC 4.0 por objeto (según Europeana; comprobar)",
    },
}

_BASE_DZ = "https://www.diccionariodelazarzuela.es/dz"
_BASE_JM = "https://www.march.es/es/coleccion/teatro-musical-espanol"
_BASE_CDAEM = "https://www.musicadanza.es/es/fondos-documentales/colecciones/programas-de-mano"
_BASE_MM = "http://www.memoriademadrid.es/buscador.php?accion=VerFicha&id="


def _id(valor, nombre: str) -> str:
    s = str(valor or "").strip()
    if not re.fullmatch(r"\d+", s):
        raise ValueError(f"{nombre} debe ser numérico: {valor!r}")
    return s


def _url(source: str, kw: dict) -> str:
    if source == "iccmu_diccionario_zarzuela":
        if kw.get("url"):  # patrón de voz no confirmado: se acepta la URL tal cual la da el sitio
            if not str(kw["url"]).startswith(_BASE_DZ):
                raise ValueError("url fuera de diccionariodelazarzuela.es/dz")
            return kw["url"]
        if kw.get("letra"):
            letra = str(kw["letra"]).lower()
            if not re.fullmatch(r"[a-z]", letra):
                raise ValueError("letra debe ser a-z")
            return f"{_BASE_DZ}/indice?l={letra}&p={int(kw.get('pagina', 1))}"
        return _BASE_DZ
    if source == "juan_march_teatro_musical":
        return f"{_BASE_JM}/ficha/--{_id(kw.get('id'), 'id')}"
    if source == "cdaem_musicadanza":
        slug = str(kw.get("slug") or "").strip()
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError(f"slug inválido: {slug!r}")
        return f"{_BASE_CDAEM}/{slug}"
    return _BASE_MM + _id(kw.get("id"), "id")  # espinos_memoria_madrid


def cita(source: str, **kw) -> dict:
    """Construye la referencia citable de una fuente de solo consulta, sin descargar texto.

    kw comunes: `titulo` (título de la voz/ficha/inventario), `fecha_consulta` (date o ISO; por
    defecto hoy). Específicos: iccmu -> `url` o `letra`/`pagina`; juan_march/espinos -> `id`;
    cdaem -> `slug`.
    """
    if source not in _FUENTES:
        raise KeyError(f"fuente de solo consulta desconocida: {source}")
    meta = _FUENTES[source]
    fecha = kw.get("fecha_consulta") or date.today()
    fecha = fecha.isoformat() if isinstance(fecha, date) else str(fecha)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        raise ValueError(f"fecha_consulta debe ser ISO (AAAA-MM-DD): {fecha!r}")
    url = _url(source, kw)
    titulo = (kw.get("titulo") or meta["obra"]).strip()
    texto = f"{titulo}. En: {meta['obra']}. {meta['institucion']}. {url} (consultado el {fecha})."
    return {
        "source": source,
        "url": url,
        "title": titulo,
        "work": meta["obra"],
        "institution": meta["institucion"],
        "accessed": fecha,
        "reuse": REUSE,
        "license": meta["licencia"],
        "citation": texto,
    }
