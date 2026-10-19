"""OAI-PMH 2.0: utilidades mínimas para cosechar `Identify`/`ListSets`/`ListRecords` (usadas por bvph.py y
prensa_catalana.py). Sin dependencias nuevas: `xml.etree.ElementTree` no resuelve entidades externas, así que
es seguro frente a XML de terceros. El manejo de `resumptionToken` sigue la especificación: en la petición de
continuación solo se envían `verb` y `resumptionToken` (ningún otro parámetro)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..models import Reuse

NS = "{http://www.openarchives.org/OAI/2.0/}"

# Términos de relevancia para teatro lírico/zarzuela (plan §1 bis), aplicados sobre los campos Dublin Core
# renderizados de un registro. Cubre castellano y catalán (sarsuela, líric/lírich).
TOPIC = re.compile(
    r"zarzuela|sarsuela|tonadilla|sainete\s+l[ií]ric|g[eé]nero\s+(chico|[ií]nfimo)|opereta|"
    r"[oó]pera\s+espa[nñ]ola|teatro?\s+l[ií]ric|c[oó]mic[oa]?-l[ií]ric|revista\s+musical|llibret|libreto",
    re.I,
)

# Derechos declarados que permiten reutilización (resto: solo consulta). Incluye los vocabularios más comunes
# en registros Dublin Core de bibliotecas digitales españolas/catalanas (Creative Commons y rightsstatements.org).
OPEN_RIGHTS = ("publicdomain/mark", "publicdomain/zero", "licenses/by/", "licenses/by-sa/", "cc0", "cc-by", "cc by")


class OAIError(RuntimeError):
    """Error devuelto por el repositorio OAI-PMH (elemento <error code=...>), distinto de `noRecordsMatch`."""


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def request(http, base: str, **params) -> ET.Element:
    clean = {k: v for k, v in params.items() if v is not None}
    xml = http.get(base, clean)
    root = ET.fromstring(xml)
    err = root.find(f"{NS}error")
    if err is not None and err.get("code") != "noRecordsMatch":
        raise OAIError(f"{err.get('code', '?')}: {_text(err)}")
    return root


def identify(http, base: str) -> dict:
    root = request(http, base, verb="Identify")
    idn = root.find(f"{NS}Identify")
    return {child.tag.split("}")[-1]: _text(child) for child in idn} if idn is not None else {}


def list_sets(http, base: str) -> list[dict]:
    out: list[dict] = []
    token = None
    while True:
        root = request(http, base, verb="ListSets", resumptionToken=token) if token else request(http, base, verb="ListSets")
        ls = root.find(f"{NS}ListSets")
        if ls is None:
            return out
        for s in ls.findall(f"{NS}set"):
            out.append({"setSpec": _text(s.find(f"{NS}setSpec")), "setName": _text(s.find(f"{NS}setName"))})
        rt = ls.find(f"{NS}resumptionToken")
        token = _text(rt) if rt is not None else None
        if not token:
            return out


def _dc_fields(metadata_el) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    dc = next(iter(metadata_el), None) if metadata_el is not None else None  # <oai_dc:dc>, único hijo de <metadata>
    if dc is None:
        return out
    for child in dc:
        key, val = child.tag.split("}")[-1], _text(child)
        if val:
            out.setdefault(key, []).append(val)
    return out


def list_records(http, base: str, metadata_prefix: str = "oai_dc", set_spec: str | None = None,
                  limit: int | None = None):
    """Itera registros siguiendo `resumptionToken`. Cada registro: {identifier, datestamp, deleted, sets, dc}."""
    n = 0
    first = True
    token = None
    while True:
        if first:
            root = request(http, base, verb="ListRecords", metadataPrefix=metadata_prefix, set=set_spec)
            first = False
        else:
            root = request(http, base, verb="ListRecords", resumptionToken=token)
        lr = root.find(f"{NS}ListRecords")
        if lr is None:
            return
        for rec in lr.findall(f"{NS}record"):
            if limit is not None and n >= limit:
                return
            header = rec.find(f"{NS}header")
            deleted = header.get("status") == "deleted"
            n += 1
            yield {
                "identifier": _text(header.find(f"{NS}identifier")),
                "datestamp": _text(header.find(f"{NS}datestamp")),
                "deleted": deleted,
                "sets": [_text(s) for s in header.findall(f"{NS}setSpec")],
                "dc": {} if deleted else _dc_fields(rec.find(f"{NS}metadata")),
            }
        rt = lr.find(f"{NS}resumptionToken")
        token = _text(rt) if rt is not None else None
        if not token:
            return


def render_dc(rec: dict) -> str:
    """Texto canónico del registro: una línea por campo Dublin Core (cada línea es un pasaje citable)."""
    lines = [f"OAI {rec['identifier']}"]
    for key, vals in rec["dc"].items():
        for v in vals:
            lines.append(f"{key}: {v}")
    return "\n".join(lines)


def reuse_for(rights: list[str] | None) -> tuple[Reuse, str]:
    """Reutilizable solo con dominio público/CC0/CC BY/CC BY-SA declarados en `dc:rights`; si no, solo consulta.
    -> (Reuse, cadena de derechos usada como `license` del documento)."""
    for r in rights or []:
        if any(p in r.lower() for p in OPEN_RIGHTS):
            return Reuse.reusable, r
    return Reuse.consult_only, ((rights or [None])[0] or "sin declarar")


def matches_topic(rec: dict) -> bool:
    text = " ".join(v for vals in rec["dc"].values() for v in vals)
    return bool(TOPIC.search(text))
