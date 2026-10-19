"""Utilidades comunes a los conectores discográficos (MusicBrainz, Discogs): enlace con el corpus y escritura de afirmaciones."""
from __future__ import annotations

import json
import sqlite3

from ..db import jdump
from ..util import is_edtf, now, sha256


def corpus_index(con: sqlite3.Connection, xkey: str) -> dict[str, str]:
    """id externo `xkey` -> id de entidad del corpus."""
    out: dict[str, str] = {}
    for r in con.execute("SELECT id, external_ids FROM entities"):
        v = json.loads(r["external_ids"] or "{}").get(xkey)
        if v:
            out[str(v)] = r["id"]
    return out


def corpus_works(con: sqlite3.Connection) -> list[dict]:
    """Obras del corpus con sus compositores (entidades) y IDs externos."""
    works = []
    for r in con.execute("SELECT id,label,external_ids FROM entities WHERE type='work' AND provisional=0 ORDER BY id"):
        comps = [json.loads(c["value_json"]) for c in con.execute(
            "SELECT value_json FROM claims WHERE subject_id=? AND property='composer' AND status!='deprecated'", (r["id"],))]
        works.append({"id": r["id"], "label": r["label"], "xids": json.loads(r["external_ids"] or "{}"), "composers": comps})
    return works


def entity_xids(con: sqlite3.Connection, eid: str) -> dict:
    r = con.execute("SELECT external_ids FROM entities WHERE id=?", (eid,)).fetchone()
    return json.loads(r["external_ids"] or "{}") if r else {}


def add_xid(con: sqlite3.Connection, eid: str, key: str, value: str) -> None:
    x = entity_xids(con, eid)
    if x.get(key) != value:
        x[key] = value
        con.execute("UPDATE entities SET external_ids=? WHERE id=?", (jdump(x), eid))


def ensure_entity(con: sqlite3.Connection, eid: str, etype: str, label: str, xids: dict | None = None) -> None:
    """Crea la entidad provisional si no existe; nunca pisa una existente (solo fusiona IDs externos)."""
    if con.execute("SELECT 1 FROM entities WHERE id=?", (eid,)).fetchone():
        for k, v in (xids or {}).items():
            add_xid(con, eid, k, v)
        return
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES(?,?,?,?,?,1)",
                (eid, etype, label, "[]", jdump(xids or {})))


def add_claim(con: sqlite3.Connection, passages: dict, *, subject: str, name: str, value, vtype: str, line: str,
              method: str, confidence: float) -> bool:
    """Afirmación estructurada con evidencia = la línea canónica del documento (patrón de wikidata.py)."""
    p = passages.get(line)
    if not p or value is None:
        return False
    if vtype == "edtf" and not is_edtf(str(value)):
        return False
    cid = "C" + sha256(f"{subject}|{name}|{value}")[:16]
    con.execute(
        "INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind,extracted_by_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (cid, subject, name, jdump(value), vtype, "{}", "asserted", confidence, "structured",
         jdump({"method": method, "version": "1", "date": now()}), now()))
    con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, p["id"], line, 0, len(line)))
    return True
