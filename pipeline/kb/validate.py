"""Validadores: integridad de afirmaciones (§4.6) y segmentos generados (§6)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from .models import Segment
from .util import edtf_year, is_edtf, norm

# --- afirmaciones --------------------------------------------------------------

def validate_claims(con: sqlite3.Connection) -> list[dict]:
    issues: list[dict] = []

    def add(cid, rule, msg):
        issues.append({"claim": cid, "rule": rule, "message": msg})

    for c in con.execute("SELECT * FROM claims WHERE status!='deprecated'"):
        ev = con.execute("SELECT ce.*, p.text, p.char_start FROM claim_evidence ce JOIN passages p ON p.id=ce.passage_id WHERE claim_id=?", (c["id"],)).fetchall()
        if not ev:
            add(c["id"], "evidence", "afirmación sin evidencia")
        for e in ev:
            if c["source_kind"] == "extracted" and e["text"][e["quote_start"]:e["quote_end"]] != e["quote"]:
                add(c["id"], "quote", "la cita no coincide con el pasaje en sus offsets")
        if c["value_type"] == "edtf":
            v = json.loads(c["value_json"])
            if not is_edtf(v):
                add(c["id"], "edtf", f"fecha no válida: {v}")
            elif not (1000 <= (edtf_year(v) or 0) <= date.today().year):
                add(c["id"], "plausible", f"fecha implausible: {v}")
    # coherencias entre fechas de una misma persona / obra
    for s in con.execute("SELECT DISTINCT subject_id FROM claims WHERE property IN ('birth_date','death_date')"):
        b = _year(con, s["subject_id"], "birth_date")
        d = _year(con, s["subject_id"], "death_date")
        if b and d and d < b:
            add(None, "life", f"{s['subject_id']}: muerte ({d}) anterior al nacimiento ({b})")
    for w in con.execute("SELECT c.id,c.subject_id,c.hedge,c.status,c.value_json FROM claims c WHERE c.property='premiere_date' AND c.status!='deprecated'"):
        py = edtf_year(json.loads(w["value_json"]))
        for comp in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='composer' AND status!='deprecated'", (w["subject_id"],)):
            cid = json.loads(comp["value_json"])
            dy = _year(con, cid, "death_date")
            by = _year(con, cid, "birth_date")
            if py and by and py < by:
                add(w["id"], "premiere", f"estreno ({py}) anterior al nacimiento del compositor {cid} ({by})")
            if py and dy and py > dy and not (w["hedge"] or w["status"] in ("attributed", "legendary")):
                add(w["id"], "posthumous", f"estreno póstumo sin marca: {py} > muerte de {cid} ({dy})")
    return issues


def _year(con, subject, prop):
    r = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property=? AND status!='deprecated' ORDER BY confidence DESC LIMIT 1", (subject, prop)).fetchone()
    return edtf_year(json.loads(r["value_json"])) if r else None


# --- segmentos -----------------------------------------------------------------
_NUM = re.compile(r"\b\d{3,4}(?:[-/]\d{2}(?:[-/]\d{2})?)?\b")
_PROPER = re.compile(r"(?<![.!?¿¡]\s)(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}(?:\s+(?:de|del|la|los|las|y)?\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+)*")
_HEDGE = {"legendary": ("según la tradición", "se cuenta", "cuenta la leyenda", "leyenda", "se dice"),
          "attributed": ("se atribuye", "atribuid", "según", "probablemente", "se dice", "parece"),
          "disputed": ("según", "disput", "frente a", "otra fuente", "otras fuentes", "no coinciden", "discrep", "difier",
                       "mientras que", "en desacuerdo", "contradic", "varias fuentes", "no hay acuerdo")}


def material_text(con, ids: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Resuelve ids citables a su texto y a su estado. -> (textos, estados)."""
    texts, states = {}, {}
    for i in ids:
        if i.startswith("C"):
            c = con.execute("SELECT * FROM claims WHERE id=?", (i,)).fetchone()
            if not c:
                continue
            v = json.loads(c["value_json"])
            if c["value_type"] == "entity":
                e = con.execute("SELECT label FROM entities WHERE id=?", (v,)).fetchone()
                v = e["label"] if e else v
            s = con.execute("SELECT label FROM entities WHERE id=?", (c["subject_id"],)).fetchone()
            texts[i] = f"{s['label'] if s else c['subject_id']} · {c['property']}: {v}" + (f" ({c['hedge']})" if c["hedge"] else "")
            states[i] = c["status"]
        elif i.startswith("N"):
            n = con.execute("SELECT * FROM narratives WHERE id=?", (i,)).fetchone()
            if n:
                texts[i] = f"{n['title']}. {n['summary']}"
                states[i] = {"legendary": "legendary", "reported": "attributed"}.get(n["status"], "asserted")
        elif i.startswith("P"):
            p = con.execute("SELECT text FROM passages WHERE id=?", (i,)).fetchone()
            if p:
                texts[i] = p["text"]
                states[i] = "asserted"
    return texts, states


def validate_segment(con, seg: Segment, provided: set[str], judge=None) -> dict:
    """Existencia, literales exactos, matices y (opcional) juez semántico. -> {ok, failures}."""
    fails: list[str] = []
    cites = seg.claim_ids + seg.narrative_ids + seg.passage_ids
    if not cites:
        return {"ok": False, "failures": ["sin cita"]}
    texts, states = material_text(con, cites)
    for i in cites:
        if i not in texts:
            fails.append(f"id inexistente: {i}")
        elif i not in provided:
            fails.append(f"id no entregado al redactor: {i}")
    blob = norm(" ".join(texts.values()))
    for n in _NUM.findall(seg.text):
        if norm(n) not in blob:
            fails.append(f"cifra/fecha ausente del material: {n}")
    names: set[str] = set()
    for m in _PROPER.finditer(seg.text):
        # "Federico Romero y Guillermo Fernández-Shaw" son dos nombres: se comprueba cada uno por separado
        names.update(x.strip() for x in re.split(r"\s+(?:y|e)\s+", m.group(0)) if x.strip())
    for pn in names:
        if norm(pn) not in blob:
            fails.append(f"nombre propio ausente del material: {pn}")
    low = norm(seg.text)
    for st in ("legendary", "attributed", "disputed"):
        if any(states.get(i) == st for i in cites):
            marks = _HEDGE.get(st, ("según", "disput", "frente a", "otra fuente"))
            if not any(norm(m) in low for m in marks):
                fails.append(f"se pierde el matiz ({st})")
    if judge and not fails:
        v = judge(seg.text, list(texts.values()))
        if v != "yes":
            fails.append(f"el juez responde {v}")
    return {"ok": not fails, "failures": fails}
