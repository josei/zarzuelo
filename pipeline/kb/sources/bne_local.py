"""Datasets abiertos de la BNE descargados a mano (Cloudflare bloquea la descarga automatizada).

Lee los CSV ya extraídos en `data/bne/_archive/` (estructura real del paquete: `per/`, `ent/`, `par/`, `son/`,
`dominiopublico_csv-utf8/set_acceso_libre_*.csv`, `fulltext-csv.csv`). Todo se lee en streaming y se FILTRA por relevancia
antes de escribir en la base: personas del corpus (nombre + años de vida), y partituras, grabaciones y documentos
digitalizados con temática de zarzuela/teatro lírico o autores del corpus.

Texto y metadatos se guardan; los objetos digitales (imágenes, audio, PDF) solo como enlace (`version_digital`).
Licencia: CC BY 4.0 (aviso legal de la BNE y datos.gob.es; el CKAN dice CC0: se aplica la más restrictiva).
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from ..db import DATA, jdump
from ..models import Reuse
from ..store import capture, segment
from ..util import now, norm, sha256

LICENSE = "CC-BY-4.0"
ARCHIVE = DATA / "bne" / "_archive"
CATALOG = "https://catalogo.bne.es/discovery/fulldisplay?docid=alma{id}&vid=34BNE_INST:CATALOGO"
TOPIC = re.compile(r"zarzuela|tonadilla|sainete l[ií]rico|g[eé]nero (chico|[ií]nfimo)|opereta|[oó]pera espa[nñ]ola|"
                   r"sarsuela|teatro l[ií]rico|revista musical|libreto|comedia l[ií]rica|zarzuel", re.I)
HEADING_YEARS = re.compile(r"\(\s*(?:ca\.\s*)?(\d{3,4})?\s*-\s*(\d{3,4})?\s*\)")
csv.field_size_limit(1 << 28)

FILES = {
    "persona": "per/persona-UTF8.csv",
    "entidad": "ent/entidad-UTF8.csv",
    "partituras": "par/musicaescrita-UTF8.csv",
    "grabaciones": "son/grabsonoras-UTF8.csv",
}
TOPIC_COLS = {
    "partituras": ("titulo", "titulo_normalizado", "otros_titulos", "autores", "forma_partitura", "contenido"),
    "grabaciones": ("titulo", "otros_titulos", "responsables_e_interpretes", "tipo_de_contenido", "contenido"),
}
AUTHOR_COL = {"partituras": "autores", "grabaciones": "responsables_e_interpretes"}


def _rows(path: Path) -> Iterator[dict]:
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        yield from csv.DictReader(f, delimiter=";")


def _clean(v: str | None) -> str:
    return re.sub(r"\s+", " ", (v or "").replace("﻿", "")).strip(" \"")


def flip(heading: str) -> str:
    """'Apellido, Nombre (1867-1916)' -> 'nombre apellido' normalizado."""
    h = re.sub(r"\(.*?\)", "", heading).strip().rstrip(",. ")
    if "," in h:
        a, b = h.split(",", 1)
        h = f"{b.strip()} {a.strip()}"
    return norm(h)


def heading_years(heading: str) -> tuple[int | None, int | None]:
    m = HEADING_YEARS.search(heading)
    if not m:
        return None, None
    return (int(m.group(1)) if m.group(1) else None, int(m.group(2)) if m.group(2) else None)


def render(row: dict, skip: tuple[str, ...] = ()) -> str:
    """Texto canónico de un registro: una línea `campo: valor` por campo no vacío (cada línea es un pasaje citable)."""
    lines = []
    for k, v in row.items():
        v = _clean(v) if k != "texto_OCR" else (v or "").strip()
        if v and k not in skip and k is not None:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def corpus_people(con: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, tuple[int | None, int | None]]]:
    """nombre normalizado -> QIDs, y QID -> (año de nacimiento, año de muerte) según Wikidata."""
    names: dict[str, set[str]] = {}
    for e in con.execute("SELECT id,label,labels_alt FROM entities WHERE type='person' AND id LIKE 'Q%'"):
        for n in [e["label"], *json.loads(e["labels_alt"] or "[]")]:
            names.setdefault(norm(n), set()).add(e["id"])
    years: dict[str, list] = {}
    for c in con.execute("SELECT subject_id,property,value_json FROM claims WHERE property IN ('birth_date','death_date') AND source_kind='structured' AND status!='deprecated'"):
        y = int(json.loads(c["value_json"])[:4])
        years.setdefault(c["subject_id"], [None, None])[0 if c["property"] == "birth_date" else 1] = y
    return names, {k: (v[0], v[1]) for k, v in years.items()}


def _years_agree(h: tuple[int | None, int | None], w: tuple[int | None, int | None]) -> bool:
    """Compatibles y con al menos un año comparable idéntico (evita homónimos)."""
    pairs = [(a, b) for a, b in zip(h, w) if a and b]
    return bool(pairs) and all(abs(a - b) <= 1 for a, b in pairs) and any(a == b for a, b in pairs)


def _doc(con, source: str, rid: str, text: str, url: str, reuse=Reuse.reusable) -> tuple[str, bool]:
    doc_id, new = capture(con, source=source, url=url, stable_id=rid, content=text, license=LICENSE, reuse=reuse, mime="text/plain")
    segment(con, doc_id, lang="es")
    return doc_id, new


def ingest_personas(con: sqlite3.Connection, limit: int | None = None, base: Path = ARCHIVE) -> dict:
    names, wd_years = corpus_people(con)
    st = {"scanned": 0, "name_hits": 0, "reconciled": 0, "ambiguous": 0, "claims": 0}
    for r in _rows(base / FILES["persona"]):
        st["scanned"] += 1
        heading = _clean(r.get("nombre_de_persona"))
        cands = names.get(flip(heading))
        if not cands:
            continue
        st["name_hits"] += 1
        hy = heading_years(heading)
        ok = [q for q in cands if _years_agree(hy, wd_years.get(q, (None, None)))]
        if len(ok) != 1:
            st["ambiguous"] += 1
            continue
        qid = ok[0]
        rid = _clean(r["id"])
        doc_id, _ = _doc(con, "bne_persona", rid, render(r, skip=("id_symphony",)), CATALOG.format(id=rid))
        ex = json.loads(con.execute("SELECT external_ids FROM entities WHERE id=?", (qid,)).fetchone()["external_ids"] or "{}")
        ex.setdefault("bne_alma", rid)
        alt = json.loads(con.execute("SELECT labels_alt FROM entities WHERE id=?", (qid,)).fetchone()["labels_alt"] or "[]")
        for n in (_clean(x) for x in re.split(r"/\*\*/", r.get("otros_nombres") or "")):
            if n and n not in alt:
                alt.append(n)
        con.execute("UPDATE entities SET external_ids=?, labels_alt=? WHERE id=?", (jdump(ex), jdump(alt), qid))
        st["reconciled"] += 1
        # fechas de vida del encabezamiento -> afirmaciones estructuradas con evidencia en la línea del registro
        line = f"nombre_de_persona: {heading}"
        p = con.execute("SELECT id FROM passages WHERE document_id=? AND text=?", (doc_id, line)).fetchone()
        if p:
            for prop, y in (("birth_date", hy[0]), ("death_date", hy[1])):
                if y:
                    cid = "C" + sha256(f"{qid}|{prop}|{y}|bne")[:16]
                    con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind,extracted_by_json,created_at) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (cid, qid, prop, jdump(str(y)), "edtf", "{}", "asserted", 0.7, "structured",
                                                                 jdump({"method": "bne-heading", "version": "1", "date": now()}), now()))
                    con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, p["id"], line, 0, len(line)))
                    st["claims"] += 1
        con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, qid))
        if limit and st["reconciled"] >= limit:
            break
    con.commit()
    return st


def _author_names(raw: str) -> list[str]:
    return [flip(x) for x in re.split(r"\s*(?:/\*\*/|//|;)\s*", raw or "") if x.strip()]


def ingest_catalog(con: sqlite3.Connection, name: str, limit: int | None = None, base: Path = ARCHIVE) -> dict:
    """Partituras o grabaciones: registros con temática lírica o con autores/intérpretes del corpus."""
    names, _ = corpus_people(con)
    st = {"scanned": 0, "kept": 0}
    for r in _rows(base / FILES[name]):
        st["scanned"] += 1
        topical = any(TOPIC.search(r.get(c) or "") for c in TOPIC_COLS[name])
        by_author = any(a in names for a in _author_names(r.get(AUTHOR_COL[name]) or ""))
        if not (topical or (by_author and name == "partituras")):
            continue
        rid = _clean(r["id"])
        _doc(con, f"bne_{name}", rid, render(r, skip=("id_symphony",)), CATALOG.format(id=rid))
        st["kept"] += 1
        if limit and st["kept"] >= limit:
            break
    con.commit()
    return st


def ingest_dominio_publico(con: sqlite3.Connection, limit: int | None = None, base: Path = ARCHIVE) -> dict:
    """Biblioteca Digital (dominio público): libretos y obras líricas con su texto OCR; las imágenes solo se enlazan."""
    names, _ = corpus_people(con)
    st = {"files": 0, "scanned": 0, "kept": 0, "with_ocr": 0}
    for f in sorted((base / "dominiopublico_csv-utf8").glob("set_acceso_libre_*-UTF8.csv")):
        st["files"] += 1
        for r in _rows(f):
            st["scanned"] += 1
            blob = " ".join((r.get(c) or "") for c in ("Título", "Título uniforme", "Otros Títulos", "Contenido", "Tema", "Género/Forma", "Descripción y notas"))
            by_author = any(a in names for a in _author_names(r.get("Autor Personas") or ""))
            if not (TOPIC.search(blob) or by_author):
                continue
            rid = _clean(r.get("idBNE"))
            link = _clean(r.get("version_digital")).strip("* ") or CATALOG.format(id=rid)
            ocr = (r.get("texto_OCR") or "").strip()
            _doc(con, "bne_dominiopublico", rid, render(r), link)
            st["kept"] += 1
            st["with_ocr"] += int(bool(ocr))
            if limit and st["kept"] >= limit:
                con.commit()
                return st
        con.commit()
    return st


def ingest_hemeroteca_index(con: sqlite3.Connection, base: Path = ARCHIVE) -> dict:
    """Índice de cabeceras de la Hemeroteca con enlace al ZIP de texto de cada una (el texto no se baja: robots.txt lo veta)."""
    path = base / "fulltext-csv.csv"
    n = 0
    for r in csv.DictReader(open(path, newline="", encoding="utf-8-sig"), delimiter=";"):
        issn = _clean(r.get("ISSN"))
        text = render({k: v for k, v in r.items()})
        _doc(con, "bne_hemeroteca_indice", issn or sha256(text)[:12], text, _clean(r.get("Enlace al texto completo (txt)")) or "https://hemerotecadigital.bne.es/")
        n += 1
    con.commit()
    return {"titles": n}


def phase0(sample: int = 10) -> dict:
    """Comprueba que los ficheros descargados a mano están en su sitio y devuelve una muestra real."""
    rep: dict = {"source": "bne_local", "license": LICENSE, "archive": str(ARCHIVE), "files": {}}
    for k, rel in FILES.items():
        p = ARCHIVE / rel
        rep["files"][k] = {"present": p.exists(), "size_bytes": p.stat().st_size if p.exists() else None}
    sets = list((ARCHIVE / "dominiopublico_csv-utf8").glob("set_acceso_libre_*-UTF8.csv"))
    rep["files"]["dominiopublico"] = {"present": bool(sets), "csv_files": len(sets)}
    rep["files"]["hemeroteca_indice"] = {"present": (ARCHIVE / "fulltext-csv.csv").exists()}
    if (ARCHIVE / FILES["partituras"]).exists():
        rep["sample"] = [{k: _clean(v)[:80] for k, v in list(r.items())[:6]} for _, r in zip(range(sample), _rows(ARCHIVE / FILES["partituras"]))]
    return rep


def ingest(con: sqlite3.Connection, limit: int | None = None, parts: tuple[str, ...] = ("persona", "partituras", "grabaciones", "dominiopublico", "hemeroteca")) -> dict:
    out: dict = {}
    if "persona" in parts:
        out["persona"] = ingest_personas(con, limit)
    if "partituras" in parts:
        out["partituras"] = ingest_catalog(con, "partituras", limit)
    if "grabaciones" in parts:
        out["grabaciones"] = ingest_catalog(con, "grabaciones", limit)
    if "dominiopublico" in parts:
        out["dominiopublico"] = ingest_dominio_publico(con, limit)
    if "hemeroteca" in parts:
        out["hemeroteca"] = ingest_hemeroteca_index(con)
    return out
