"""Biblioteca Nacional de España: datos abiertos (autoridades, partituras, grabaciones, Biblioteca Digital).

Un solo módulo parametrizado por dataset (`DATASETS`). Los ficheros (ZIP con CSV o JSON) se descargan
comprimidos en `data/bne/<dataset>/` en streaming y se leen en streaming (zlib), sin cargarlos en memoria;
solo se registran (capture/segment) los registros relevantes. Texto y metadatos sí; los escaneos, PDF o audio
a los que apuntan los registros NO se descargan: queda la URL de referencia dentro del texto del registro.

Estado de la fase 0 (2026-10-07): los ficheros viven en www.bne.es/media/datosgob/…, protegido por un reto de
Cloudflare que responde 403 a clientes automáticos; no se elude. Hasta que se pueda descargar la muestra,
las fuentes quedan `validated: false`.
"""
from __future__ import annotations

import codecs
import csv
import json
import re
import sqlite3
import zlib
from pathlib import Path
from typing import Iterator

import httpx

from .. import db as kbdb
from ..db import jdump
from ..http import Http, load_sources
from ..store import capture, segment
from ..util import norm, sha256

CKAN = "https://datosabiertos.bne.es/catalogo/dataset/"  # CKAN del portal (cadena TLS incompleta: no se usa su API)
DATOSGOB = "https://datos.gob.es/catalogo/"
TERMS = "https://www.bne.es/es/aviso-legal"
ROBOTS = "https://www.bne.es/robots.txt"
BIG = 5 * 1024 ** 3  # umbral de aviso (AGENTS.md)
SAMPLE_BYTES = 4 * 1024 * 1024
LIMITED_BYTES = 64 * 1024 * 1024  # con `limit` solo se baja este prefijo

_BASE = "https://www.bne.es/media/datosgob/"
DATASETS: dict[str, dict] = {
    "bne_autoridades": {
        "title": "Catálogo de autoridades: personas y entidades", "role": "authority",
        "files": {"persona": _BASE + "catalogo-autoridades/persona/persona-{fmt}.zip",
                  "entidad": _BASE + "catalogo-autoridades/entidad/entidad-{fmt}.zip"},
        "page": DATOSGOB + "ea0019768-catalogo-de-autoridades-personas"},
    "bne_partituras": {
        "title": "Catálogo bibliográfico: partituras", "role": "bib",
        "files": {"partituras": _BASE + "catalogo-bibliografico/partituras/musicaescrita-{fmt}.zip"},
        "page": DATOSGOB + "ea0019768-catalogo-bibliografico-partituras1"},
    "bne_grabaciones": {
        "title": "Catálogo bibliográfico: grabaciones sonoras", "role": "bib",
        "files": {"grabaciones": _BASE + "catalogo-bibliografico/grabaciones-sonoras/grabsonoras-{fmt}.zip"},
        "page": CKAN + "catalogo-bibliografico-grabaciones-sonoras1"},
    "bne_biblioteca_digital": {
        "title": "Biblioteca Digital: documentos en dominio público", "role": "bib",
        "files": {"dominiopublico": _BASE + "bdh/dominiopublico/dominiopublico_{fmt_lc}.zip"},
        "page": CKAN + "biblioteca-digital-documentos-en-dominio-publico"},
}
FORMATS = {"csv": {"fmt": "UTF8", "fmt_lc": "csv-utf8"}, "json": {"fmt": "JSON", "fmt_lc": "json"}}

# Términos de relevancia temática (§1 bis); los nombres del corpus se añaden desde la base.
TOPIC = re.compile(r"zarzuela|tonadilla|sainete l[ií]rico|g[eé]nero (chico|[ií]nfimo)|opereta|[oó]pera espa[nñ]ola|"
                   r"sarsuela|teatro l[ií]rico|revista musical|libreto", re.I)
ID_HINTS = ("identificador", "id bne", "idbne", "bne id", "control", "001", "id")
NAME_HINTS = ("encabezamiento", "heading", "nombre", "autor", "100", "name")
YEARS = re.compile(r"(\d{4})\s*-\s*(\d{4})?")


# --- lectura en streaming de ZIP (también de prefijos truncados) -------------------------------------------

def _zip_chunks(path: Path) -> Iterator[tuple[str, Iterator[bytes]]]:
    """Itera los miembros deflate de un ZIP leyendo las cabeceras locales; tolera ficheros truncados."""
    f = open(path, "rb")

    def member(name: str, f, buf: bytearray, state: dict):
        d = zlib.decompressobj(-15)
        while True:
            if not buf:
                buf.extend(f.read(1 << 20))
                if not buf:
                    return
            data = bytes(buf)
            buf.clear()
            out = d.decompress(data)
            if out:
                yield out
            if d.eof:
                buf.extend(d.unused_data)
                state["done"] = True
                return

    buf = bytearray()
    try:
        while True:
            while len(buf) < 30:
                more = f.read(1 << 20)
                if not more:
                    break
                buf.extend(more)
            i = buf.find(b"PK\x03\x04")
            if i < 0 or len(buf) < i + 30:
                return
            del buf[:i]
            method = int.from_bytes(buf[8:10], "little")
            nlen, elen = int.from_bytes(buf[26:28], "little"), int.from_bytes(buf[28:30], "little")
            while len(buf) < 30 + nlen + elen:
                buf.extend(f.read(1 << 20))
            name = bytes(buf[30:30 + nlen]).decode("utf-8", "replace")
            del buf[:30 + nlen + elen]
            if method != 8:
                raise ValueError(f"miembro ZIP {name!r} con método {method} no soportado")
            if name.endswith("/"):
                continue
            state: dict = {}
            yield name, member(name, f, buf, state)
    finally:
        f.close()


def iter_text(path: Path, encoding: str = "utf-8") -> Iterator[tuple[str, Iterator[str]]]:
    for name, chunks in _zip_chunks(path):
        dec = codecs.getincrementaldecoder(encoding)("replace")
        yield name, (dec.decode(c) for c in chunks)


def _lines(chunks: Iterator[str]) -> Iterator[str]:
    tail = ""
    for c in chunks:
        tail += c
        parts = tail.splitlines(keepends=True)
        if parts and not parts[-1].endswith(("\n", "\r")):
            tail = parts.pop()
        else:
            tail = ""
        yield from parts
    if tail:
        yield tail


def _json_objects(chunks: Iterator[str]) -> Iterator[dict]:
    """Objetos de un array JSON (o JSON-lines) sin cargar el documento entero."""
    dec = json.JSONDecoder()
    buf = ""
    for c in chunks:
        buf += c
        while True:
            buf = buf.lstrip(" \t\r\n,[]")
            if not buf:
                break
            try:
                obj, end = dec.raw_decode(buf)
            except json.JSONDecodeError:
                break  # objeto incompleto (o fin truncado): esperar más datos
            buf = buf[end:]
            if isinstance(obj, dict):
                yield obj


def read_records(path: Path, fmt: str = "csv") -> Iterator[dict]:
    """Registros (dict campo->texto) de un ZIP descargado. CSV UTF-8 o JSON."""
    for name, chunks in iter_text(path):
        if name.lower().endswith(".json") or fmt == "json":
            for o in _json_objects(chunks):
                yield {str(k): _flat(v) for k, v in o.items()}
        else:
            try:
                for row in csv.DictReader(_lines(chunks)):
                    yield {k: (v or "").strip() for k, v in row.items() if k}
            except csv.Error:
                return  # fichero truncado en mitad de un campo entrecomillado


def _flat(v) -> str:
    if isinstance(v, (list, tuple)):
        return " | ".join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_flat(x)}" for k, x in v.items() if _flat(x))
    return "" if v is None else str(v).strip()


# --- relevancia y render ------------------------------------------------------------------------------------

def render(rec: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in rec.items() if v)


def _col(rec: dict, hints: tuple[str, ...]) -> str | None:
    cols = {norm(k): k for k in rec}
    for h in hints:
        for nk, k in cols.items():
            if nk == h or (len(h) > 3 and h in nk):
                return k
    return None


def corpus_people(con: sqlite3.Connection) -> list[dict]:
    """Personas de la base con id externos y años vitales (de afirmaciones birth_date/death_date si existen)."""
    out = []
    for e in con.execute("SELECT id,label,labels_alt,external_ids FROM entities WHERE type='person'"):
        yrs = {}
        for c in con.execute("SELECT property,value_json FROM claims WHERE subject_id=? AND property IN ('birth_date','death_date') AND status!='deprecated'", (e["id"],)):
            m = re.match(r'"?(\d{4})', c["value_json"])
            if m:
                yrs[c["property"]] = int(m.group(1))
        try:
            alt = json.loads(e["labels_alt"] or "[]")
        except ValueError:
            alt = []
        out.append({"id": e["id"], "names": [e["label"], *alt], "ext": json.loads(e["external_ids"] or "{}"),
                    "birth": yrs.get("birth_date"), "death": yrs.get("death_date")})
    return out


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def name_key(heading: str) -> str:
    """'Apellido, Nombre, 1864-1909' -> 'nombre apellido' normalizado (sin fechas ni calificadores)."""
    h = re.split(r",\s*\d{3,4}|\(", heading)[0]
    parts = [p.strip() for p in h.split(",") if p.strip()]
    if len(parts) >= 2:
        parts = [parts[1], parts[0], *parts[2:]]
    return norm(" ".join(parts))


def heading_years(heading: str) -> tuple[int | None, int | None]:
    m = YEARS.search(heading)
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else (None, None)


class Matcher:
    def __init__(self, people: list[dict]):
        self.by_id: dict[str, dict] = {}
        self.by_name: dict[str, list[dict]] = {}
        for p in people:
            if p["ext"].get("bne"):
                self.by_id[_digits(p["ext"]["bne"])] = p
            for n in p["names"]:
                self.by_name.setdefault(name_key(n) if "," in n else norm(n), []).append(p)
        names = sorted({k for k in self.by_name if len(k.split()) >= 2}, key=len, reverse=True)
        self.name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names[:5000]) + r")\b") if names else None

    def person(self, rec_id: str, heading: str) -> tuple[dict | None, str]:
        """Coincidencia fiable: ID BNE conocido, o nombre + fechas vitales (y única)."""
        if _digits(rec_id) and _digits(rec_id) in self.by_id:
            return self.by_id[_digits(rec_id)], "id"
        b, d = heading_years(heading)
        cands = {p["id"]: p for p in self.by_name.get(name_key(heading), [])}
        ok = [p for p in cands.values() if b and p["birth"] == b and (not d or not p["death"] or p["death"] == d)]
        return (ok[0], "name+dates") if len(ok) == 1 else (None, "")

    def mentions(self, text: str) -> bool:
        return bool(self.name_re and self.name_re.search(norm(text)))


def merge_external(con: sqlite3.Connection, ent_id: str, key: str, value: str) -> str:
    """Añade clave a external_ids sin pisar nada. -> 'added' | 'same' | 'conflict'."""
    row = con.execute("SELECT external_ids FROM entities WHERE id=?", (ent_id,)).fetchone()
    ext = json.loads(row["external_ids"] or "{}")
    if key in ext:
        return "same" if ext[key] == value else "conflict"
    ext[key] = value
    con.execute("UPDATE entities SET external_ids=? WHERE id=?", (jdump(ext), ent_id))
    return "added"


# --- descarga -----------------------------------------------------------------------------------------------

def file_url(name: str, part: str, fmt: str = "csv") -> str:
    return DATASETS[name]["files"][part].format(**FORMATS[fmt])


def _cfg(name: str) -> dict:
    return load_sources().get(name, {})


def head_size(http: Http, url: str) -> int | None:
    try:
        http._wait()
        r = http.client.head(url, follow_redirects=True)
        r.raise_for_status()
        n = r.headers.get("content-length")
        return int(n) if n else None
    except (httpx.HTTPError, ValueError):
        return None


def local_path(name: str, part: str, fmt: str, sample: bool = False) -> Path:
    return kbdb.DATA / "bne" / name / (f"{part}-{fmt}{'.sample' if sample else ''}.zip")


def phase0(name: str = "bne_autoridades", sample: int = 10, fmt: str = "csv") -> dict:
    """Términos/robots, URL real, tamaño (HEAD), muestra de registros reales y licencia efectiva."""
    ds = DATASETS[name]
    http = Http(name, cfg={**_cfg(name), "rate_limit_per_s": 1})
    rep: dict = {"source": name, "title": ds["title"], "terms": TERMS, "catalog_page": ds["page"],
                 "license_effective": "CC-BY-4.0 (datos.gob.es y aviso legal BNE; el CKAN del portal indica CC0: se aplica la más restrictiva)",
                 "files": {}, "sample": [], "validated": False}
    try:
        rep["robots"] = http.get(ROBOTS)[:400]
    except httpx.HTTPError as e:
        rep["robots"] = f"error: {e}"
    for part in ds["files"]:
        url = file_url(name, part, fmt)
        size = head_size(http, url)
        rep["files"][part] = {"url": url, "size_bytes": size, "over_5gb": bool(size and size > BIG)}
    part = next(iter(ds["files"]))
    dest = local_path(name, part, fmt, sample=True)
    try:
        http.download(file_url(name, part, fmt), dest, max_bytes=SAMPLE_BYTES)
        for rec in read_records(dest, fmt):
            rep["sample"].append(rec)
            if len(rep["sample"]) >= sample:
                break
        rep["validated"] = len(rep["sample"]) > 0
    except httpx.HTTPStatusError as e:
        rep["blocked"] = (f"HTTP {e.response.status_code} desde {e.request.url.host} "
                          f"({e.response.headers.get('server', '?')}): reto anti-bot; no se elude")
    except httpx.HTTPError as e:
        rep["blocked"] = f"error de red: {e}"
    return rep


# --- ingesta ------------------------------------------------------------------------------------------------

def ingest(con: sqlite3.Connection, limit: int | None = None, name: str = "bne_autoridades", fmt: str = "csv",
           allow_large: bool = False, http: Http | None = None) -> dict:
    """Descarga (prefijo de 64 MB si hay `limit`; entero si no) y registra solo los registros relevantes."""
    ds = DATASETS[name]
    http = http or Http(name)
    cfg = http.cfg
    stats = {"scanned": 0, "matched": 0, "documents": 0, "reconciled": 0, "conflicts": 0, "skipped_large": []}
    matcher = Matcher(corpus_people(con))
    for part in ds["files"]:
        url = file_url(name, part, fmt)
        size = head_size(http, url)
        if size and size > BIG and not allow_large and not limit:
            stats["skipped_large"].append({"url": url, "size_bytes": size})
            continue
        dest = local_path(name, part, fmt, sample=bool(limit))
        http.download(url, dest, max_bytes=LIMITED_BYTES if limit else None)
        for rec in read_records(dest, fmt):
            stats["scanned"] += 1
            text = render(rec)
            rid = rec.get(_col(rec, ID_HINTS) or "", "")
            heading = rec.get(_col(rec, NAME_HINTS) or "", "")
            person, how = (matcher.person(rid, heading) if ds["role"] == "authority" and part == "persona" else (None, ""))
            if not (person or TOPIC.search(text) or matcher.mentions(text)):
                continue
            stats["matched"] += 1
            sid = rid or f"{part}:{sha256(text)[:16]}"
            doc_id, new = capture(con, source=name, url=f"{url}#{sid}", stable_id=f"{part}:{sid}", content=text,
                                  license=cfg["license"], reuse=cfg["reuse"], mime="text/plain",
                                  raw=json.dumps(rec, ensure_ascii=False).encode())
            stats["documents"] += int(new)
            segment(con, doc_id, lang="es")
            if person and rid:
                r = merge_external(con, person["id"], "bne", rid)
                stats["reconciled"] += r == "added"
                stats["conflicts"] += r == "conflict"
            if limit and stats["matched"] >= limit:
                break
        if limit and stats["matched"] >= limit:
            break
    con.commit()
    return stats
