"""Extracción (§4.4, §8.4 tareas 0–2): el LLM propone, el verificador literal decide."""
from __future__ import annotations

import re
import sqlite3

from .db import jdump
from .llm import LLM, LLMError, UsageCapReached
from .llm.core import load_prompt
from .db import jdump as _jd  # noqa: F401
from .models import ALLOWED_PROPERTIES
from .util import is_edtf, norm, now, sha256

CLAIMS_SCHEMA = {
    "type": "object", "required": ["claims"], "additionalProperties": False,
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "required": ["property", "value", "quote", "hedge", "status"], "additionalProperties": False,
        "properties": {"property": {"type": "string", "enum": sorted(ALLOWED_PROPERTIES)}, "value": {"type": "string"},
                       "quote": {"type": "string"}, "hedge": {"type": ["string", "null"]},
                       "status": {"type": "string", "enum": ["asserted", "attributed", "legendary"]}}}}},
}
NARR_SCHEMA = {
    "type": "object", "required": ["narratives"], "additionalProperties": False,
    "properties": {"narratives": {"type": "array", "items": {
        "type": "object", "required": ["type", "title", "summary", "quote", "status", "time"], "additionalProperties": False,
        "properties": {"type": {"type": "string", "enum": ["anécdota", "recepción", "contexto_histórico", "génesis", "leyenda", "curiosidad"]},
                       "title": {"type": "string"}, "summary": {"type": "string"}, "quote": {"type": "string"},
                       "status": {"type": "string", "enum": ["documented", "reported", "legendary"]},
                       "time": {"type": ["string", "null"]}}}}},
}
REL_SCHEMA = {"type": "object", "required": ["relevant"], "additionalProperties": False, "properties": {"relevant": {"type": "boolean"}}}

TYPE_PROPS = {
    "work": {"composer", "librettist", "premiere_date", "premiere_venue", "genre", "denomination", "acts", "based_on", "company", "revival_date", "performer", "role"},
    "person": {"birth_date", "death_date", "birth_place", "death_place"},
    "venue": {"opening_date", "closing_date", "location", "company"},
}

NAME_PROPS = {"composer", "librettist", "performer", "birth_place", "death_place", "premiere_venue", "location", "company"}
DATE_PROPS = {"premiere_date", "birth_date", "death_date", "opening_date", "closing_date", "revival_date"}


def verify_claim(c: dict, passage: str, subject_type: str | None = None) -> tuple[bool, str]:
    """Comprobaciones deterministas. -> (ok, motivo)."""
    if subject_type in TYPE_PROPS and c["property"] not in TYPE_PROPS[subject_type]:
        return False, "propiedad no aplicable al tipo de sujeto"
    q = c["quote"]
    if not q or passage.find(q) < 0:
        return False, "quote no es subcadena exacta"
    if c["property"] not in ALLOWED_PROPERTIES:
        return False, "propiedad no permitida"
    v = c["value"].strip()
    if c["property"] in NAME_PROPS and not v[:1].isupper():
        return False, "un nombre propio debe empezar por mayúscula"
    if c["property"] in DATE_PROPS:
        if not is_edtf(v):
            return False, "fecha no EDTF"
        yr = v[:3] if "X" in v[:4] else v[:4]
        if yr not in q:
            return False, "el año del valor no aparece en la cita"
        return True, ""
    if c["property"] == "acts":
        return (v.isdigit(), "") if v.isdigit() else (False, "acts no numérico")
    if norm(v) not in norm(q):
        return False, "el valor no aparece en la cita"
    return True, ""


def subject_of(con, doc_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT e.* FROM doc_subjects d JOIN entities e ON e.id=d.entity_id WHERE d.document_id=? LIMIT 1", (doc_id,)).fetchone()


def extract_claims(con, llm: LLM, doc_id: str, *, engine: str | None = None, relevance_filter: bool = False,
                   progress=None) -> dict:
    subj = subject_of(con, doc_id)
    if not subj:
        return {"passages": 0, "kept": 0, "discarded": 0}
    _, pv = load_prompt("extract_claims")
    stats = {"passages": 0, "kept": 0, "discarded": 0, "skipped": 0}
    for p in con.execute("SELECT * FROM passages WHERE document_id=? ORDER BY char_start", (doc_id,)).fetchall():
        done = con.execute("SELECT 1 FROM extractions WHERE passage_id=? AND task='extract_claims' AND prompt_version=?", (p["id"], pv)).fetchone()
        if done:
            stats["skipped"] += 1
            continue
        if len(p["text"]) < 30:
            continue
        if relevance_filter:
            r = llm.run("relevance_filter", p["text"], REL_SCHEMA)
            if not r.data.get("relevant"):
                con.execute("INSERT OR REPLACE INTO extractions VALUES(?,?,?,?,?,0,0)", (p["id"], "extract_claims", pv, r.engine, "filtered"))
                continue
        allowed = ", ".join(sorted(TYPE_PROPS.get(subj["type"], ALLOWED_PROPERTIES)))
        user = f"SUJETO: {subj['label']} (tipo: {subj['type']})\nPROPIEDADES PERMITIDAS: {allowed}\nPASAJE: {p['text']}"
        try:
            r = llm.run("extract_claims", user, CLAIMS_SCHEMA, engine=engine)
        except UsageCapReached:
            raise
        except LLMError:
            # un fallo puntual (timeout…) no tumba el lote: se registra y se reintenta en la siguiente ejecución
            stats["failed"] = stats.get("failed", 0) + 1
            continue
        kept = 0
        for c in r.data.get("claims", []):
            ok, why = verify_claim(c, p["text"], subj["type"])
            if not ok:
                stats["discarded"] += 1
                continue
            qs = p["text"].find(c["quote"])
            is_date = c["property"] in DATE_PROPS
            vt = "edtf" if is_date else ("quantity" if c["property"] == "acts" else "name")
            status = c["status"] if not c["hedge"] or c["status"] != "asserted" else "attributed"
            cid = "C" + sha256(f"{subj['id']}|{c['property']}|{c['value'].strip()}|{c['quote']}")[:16]
            con.execute(
                "INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, subj["id"], c["property"], jdump(c["value"].strip()), vt, "{}", status, c["hedge"], 0.5, "extracted",
                 jdump({"method": "llm", "engine": r.engine, "model": r.model, "prompt": pv, "date": now()}), now()))
            con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)",
                        (cid, p["id"], c["quote"], qs, qs + len(c["quote"])))
            kept += 1
        stats["passages"] += 1
        stats["kept"] += kept
        con.execute("INSERT OR REPLACE INTO extractions VALUES(?,?,?,?,?,?,?)",
                    (p["id"], "extract_claims", pv, r.engine, "ok", len(r.data.get("claims", [])), kept))
        con.commit()
        if progress:
            progress(stats)
    return stats


def extract_narratives(con, llm: LLM, doc_id: str, min_len: int = 200) -> dict:
    subj = subject_of(con, doc_id)
    if not subj:
        return {"kept": 0}
    _, pv = load_prompt("extract_narratives")
    stats = {"kept": 0, "discarded": 0}
    for p in con.execute("SELECT * FROM passages WHERE document_id=? ORDER BY char_start", (doc_id,)).fetchall():
        if len(p["text"]) < min_len:
            continue
        if con.execute("SELECT 1 FROM extractions WHERE passage_id=? AND task='extract_narratives' AND prompt_version=?", (p["id"], pv)).fetchone():
            continue
        r = llm.run("extract_narratives", f"SUJETO: {subj['label']}\nPASAJE: {p['text']}", NARR_SCHEMA)
        kept = 0
        for n in r.data.get("narratives", []):
            qs = p["text"].find(n["quote"])
            if qs < 0 or not n["summary"].strip() or (n["time"] and not is_edtf(n["time"])):
                stats["discarded"] += 1
                continue
            nid = "N" + sha256(f"{subj['id']}|{p['id']}|{n['title']}")[:16]
            con.execute("INSERT OR IGNORE INTO narratives VALUES(?,?,?,?,?,?,?,?)",
                        (nid, n["type"], n["title"], n["summary"], n["time"], None, n["status"], 0.5))
            con.execute("INSERT OR IGNORE INTO narrative_entities VALUES(?,?,?)", (nid, subj["id"], "sujeto"))
            con.execute("INSERT OR IGNORE INTO narrative_evidence VALUES(?,?,?,?,?)",
                        (nid, p["id"], n["quote"], qs, qs + len(n["quote"])))
            kept += 1
        con.execute("INSERT OR REPLACE INTO extractions VALUES(?,?,?,?,?,?,?)",
                    (p["id"], "extract_narratives", pv, r.engine, "ok", len(r.data.get("narratives", [])), kept))
        stats["kept"] += kept
        con.commit()
    return stats


def _claim_names(con, c) -> set[str]:
    """Textos con los que puede aparecer el valor de una afirmación (etiqueta y alias si es entidad)."""
    import json
    v = json.loads(c["value_json"])
    if c["value_type"] == "entity":
        e = con.execute("SELECT label,labels_alt FROM entities WHERE id=?", (v,)).fetchone()
        return {norm(x) for x in ([e["label"], *json.loads(e["labels_alt"] or "[]")] if e else [str(v)])}
    return {norm(str(v))}


def _same_fact(a: set[str], b: str) -> bool:
    """Mismo hecho aunque la precisión varíe ('Teatro Tívoli' ⊂ 'Teatro Tívoli de Barcelona'; '1936' ⊂ '1936-04-06')."""
    return any(x == b or (len(x) > 3 and (x in b or b in x)) for x in a)


def escalate_disputed(con, llm: LLM, max_calls: int = 100) -> dict:
    """Reextrae con Claude (haiku) los pasajes cuyas afirmaciones extraídas están en disputa (§8.4: los casos de baja confianza se escalan).
    El resultado de Claude sustituye al del modelo local en ese pasaje: lo que Claude no reproduce se retira (`deprecated`)."""
    _, pv = load_prompt("extract_claims")
    rows = con.execute(
        "SELECT DISTINCT ce.passage_id FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id "
        "WHERE c.source_kind='extracted' AND c.status='disputed' AND json_extract(c.extracted_by_json,'$.engine')='local'").fetchall()
    stats = {"passages": 0, "kept": 0, "retired": 0, "confirmed": 0, "calls": 0}
    for row in rows[:max_calls]:
        p = con.execute("SELECT p.*, d.id AS did FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?", (row["passage_id"],)).fetchone()
        subj = subject_of(con, p["did"])
        if not subj:
            continue
        allowed = ", ".join(sorted(TYPE_PROPS.get(subj["type"], ALLOWED_PROPERTIES)))
        user = f"SUJETO: {subj['label']} (tipo: {subj['type']})\nPROPIEDADES PERMITIDAS: {allowed}\nPASAJE: {p['text']}"
        try:
            r = llm.run("extract_claims", user, CLAIMS_SCHEMA, engine="claude")
        except UsageCapReached:
            break
        except LLMError:
            continue
        stats["calls"] += 1
        keep_ids, keep_keys = set(), set()
        for c in r.data.get("claims", []):
            if not verify_claim(c, p["text"], subj["type"])[0]:
                continue
            keep_keys.add((c["property"], norm(c["value"])))
            is_date = c["property"] in DATE_PROPS
            vt = "edtf" if is_date else ("quantity" if c["property"] == "acts" else "name")
            cid = "C" + sha256(f"{subj['id']}|{c['property']}|{c['value'].strip()}|{c['quote']}")[:16]
            existed = con.execute("SELECT 1 FROM claims WHERE id=?", (cid,)).fetchone()
            qs = p["text"].find(c["quote"])
            status = c["status"] if not c["hedge"] or c["status"] != "asserted" else "attributed"
            con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, subj["id"], c["property"], jdump(c["value"].strip()), vt, "{}", status, c["hedge"], 0.5, "extracted",
                         jdump({"method": "llm", "engine": r.engine, "model": r.model, "prompt": pv, "date": now()}), now()))
            con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, p["id"], c["quote"], qs, qs + len(c["quote"])))
            keep_ids.add(cid)
            stats["confirmed" if existed else "kept"] += 1
        old = con.execute("SELECT c.id,c.property,c.value_json,c.value_type FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id WHERE ce.passage_id=? "
                          "AND c.source_kind='extracted' AND json_extract(c.extracted_by_json,'$.engine')='local' AND c.status!='deprecated'", (p["id"],)).fetchall()
        for o in old:
            names = _claim_names(con, o)
            if not any(prop == o["property"] and _same_fact(names, val) for prop, val in keep_keys):   # por propiedad y valor, no por cita
                con.execute("UPDATE claims SET status='deprecated' WHERE id=?", (o["id"],))
                stats["retired"] += 1
        stats["passages"] += 1
        con.commit()
    return stats


BATCH_SCHEMA = {"type": "object", "required": ["items"], "additionalProperties": False,
                "properties": {"items": {"type": "array", "items": {"type": "object", "required": ["index", "claims"], "additionalProperties": False,
                                                                     "properties": {"index": {"type": "integer"},
                                                                                    "claims": CLAIMS_SCHEMA["properties"]["claims"]}}}}}


def _gap_passages(con) -> list:
    """Pasajes de Wikipedia ES de fichas no provisionales donde Qwen no conservó ningún hecho y el texto parece contener uno (año o verbo clave)."""
    import re
    pat = re.compile(r"\b(1[4-9]\d\d|20[0-2]\d)\b|\b(nació|murió|falleció|estren|compuso|escribi|libreto|música de|inaugur|cerró|fundó)", re.I)
    rows = con.execute(
        "SELECT p.id passage_id, p.text FROM passages p JOIN documents d ON d.id=p.document_id JOIN doc_subjects ds ON ds.document_id=d.id JOIN entities e ON e.id=ds.entity_id "
        "WHERE d.source='wikipedia_es' AND e.provisional=0 AND e.type IN ('work','person','venue') AND length(p.text)>=80 "
        "AND p.id IN (SELECT passage_id FROM extractions WHERE task='extract_claims' AND n_kept=0) "
        "AND p.id NOT IN (SELECT passage_id FROM extractions WHERE task='redo_haiku') GROUP BY p.id ORDER BY p.id").fetchall()
    return [r for r in rows if pat.search(r["text"])]


def redo_with_haiku(con, llm: LLM, max_calls: int = 8, batch: int = 8, shard: int = 0, shards: int = 1, gaps: bool = False) -> dict:
    """Rehace con Haiku la extracción de los pasajes de los que Qwen sacó datos (`batch` pasajes por llamada, las fichas más ricas primero).
    El resultado de Haiku sustituye al de Qwen en cada pasaje: lo que Haiku reproduce se confirma, lo que no reproduce se retira (`deprecated`) y
    lo que solo encuentra Haiku se añade. Mide de paso la precisión de Qwen: confirmadas / (confirmadas + retiradas)."""
    from .llm import UsageCapReached
    _, pv = load_prompt("extract_claims_batch")
    rows = _gap_passages(con) if gaps else con.execute(
        "SELECT ce.passage_id, COUNT(DISTINCT c.subject_id) s, (SELECT COUNT(*) FROM claims x WHERE x.subject_id=MIN(c.subject_id) AND x.status!='deprecated') w "
        "FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id JOIN passages p ON p.id=ce.passage_id JOIN documents d ON d.id=p.document_id "
        "WHERE c.source_kind='extracted' AND c.status!='deprecated' AND json_extract(c.extracted_by_json,'$.engine')='local' AND d.source='wikipedia_es' "
        "AND ce.passage_id NOT IN (SELECT passage_id FROM extractions WHERE task='redo_haiku') GROUP BY ce.passage_id ORDER BY w DESC, ce.passage_id").fetchall()
    rows = rows[shard::shards]          # varios procesos en paralelo se reparten la cola sin pisarse
    st = {"calls": 0, "passages": 0, "qwen_claims": 0, "confirmed": 0, "retired": 0, "new": 0, "pending_passages": len(rows)}
    for i in range(0, len(rows), batch):
        if st["calls"] >= max_calls:
            break
        group, blocks = [], []
        for r in rows[i:i + batch]:
            p = con.execute("SELECT p.*, d.id AS did FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?", (r["passage_id"],)).fetchone()
            subj = subject_of(con, p["did"])
            if not subj:
                continue
            allowed = ", ".join(sorted(TYPE_PROPS.get(subj["type"], ALLOWED_PROPERTIES)))
            blocks.append(f"PASAJE {len(group)}\nSUJETO: {subj['label']} (tipo: {subj['type']})\nPROPIEDADES PERMITIDAS: {allowed}\nTEXTO: {p['text']}")
            group.append((p, subj))
        if not group:
            continue
        try:
            r = llm.run("extract_claims_batch", "\n\n=====\n\n".join(blocks), BATCH_SCHEMA)
        except UsageCapReached:
            break
        st["calls"] += 1
        by_index = {it["index"]: it["claims"] for it in r.data["items"]}
        for k, (p, subj) in enumerate(group):
            keep_keys, new_n = set(), 0
            for c in by_index.get(k, []):
                if not verify_claim(c, p["text"], subj["type"])[0]:
                    continue
                keep_keys.add((c["property"], norm(c["value"])))
                is_date = c["property"] in DATE_PROPS
                vt = "edtf" if is_date else ("quantity" if c["property"] == "acts" else "name")
                cid = "C" + sha256(f"{subj['id']}|{c['property']}|{c['value'].strip()}|{c['quote']}")[:16]
                # «nuevo» = ningún hecho vigente con la misma propiedad y valor (aunque lo cite otra frase u otra fuente)
                existed = con.execute("SELECT 1 FROM claims WHERE id=?", (cid,)).fetchone() or any(
                    _same_fact(_claim_names(con, x), norm(c["value"])) for x in con.execute(
                        "SELECT * FROM claims WHERE subject_id=? AND property=? AND status!='deprecated'", (subj["id"], c["property"])))
                qs = p["text"].find(c["quote"])
                status = c["status"] if not c["hedge"] or c["status"] != "asserted" else "attributed"
                con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, subj["id"], c["property"], jdump(c["value"].strip()), vt, "{}", status, c["hedge"], 0.6, "extracted",
                             jdump({"method": "llm", "engine": "claude", "model": r.model, "prompt": pv, "date": now()}), now()))
                con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, p["id"], c["quote"], qs, qs + len(c["quote"])))
                if not existed:
                    new_n += 1
            old = con.execute("SELECT c.id,c.property,c.value_json,c.value_type FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id WHERE ce.passage_id=? AND c.source_kind='extracted' "
                              "AND json_extract(c.extracted_by_json,'$.engine')='local' AND c.status!='deprecated'", (p["id"],)).fetchall()
            for o in old:
                st["qwen_claims"] += 1
                names = _claim_names(con, o)
                if any(prop == o["property"] and _same_fact(names, val) for prop, val in keep_keys):
                    st["confirmed"] += 1
                else:
                    st["retired"] += 1
                    q = con.execute("SELECT qualifiers_json FROM claims WHERE id=?", (o["id"],)).fetchone()[0]
                    import json as _j
                    qq = _j.loads(q or "{}")
                    qq["rejected"] = "Haiku no reproduce este hecho a partir del pasaje"
                    con.execute("UPDATE claims SET status='deprecated', qualifiers_json=? WHERE id=?", (jdump(qq), o["id"]))
            st["new"] += new_n
            st["passages"] += 1
            con.execute("INSERT OR REPLACE INTO extractions VALUES(?,?,?,?,?,?,?)", (p["id"], "redo_haiku", pv, "claude", "ok", len(by_index.get(k, [])), len(keep_keys)))
        con.commit()
    st["pending_passages"] -= st["passages"]
    t = st["confirmed"] + st["retired"]
    st["qwen_precision_estimate"] = round(st["confirmed"] / t, 3) if t else None
    return st


NARR_BATCH_SCHEMA = {"type": "object", "required": ["items"], "additionalProperties": False,
                     "properties": {"items": {"type": "array", "items": {"type": "object", "required": ["index", "narratives"], "additionalProperties": False,
                                                                          "properties": {"index": {"type": "integer"},
                                                                                         "narratives": {"type": "array", "items": {
                                                                                             "type": "object", "required": ["type", "title", "quote", "status", "time"], "additionalProperties": False,
                                                                                             "properties": {"type": {"type": "string", "enum": ["anécdota", "recepción", "contexto_histórico", "génesis", "leyenda", "curiosidad"]},
                                                                                                            "title": {"type": "string"}, "quote": {"type": "string"},
                                                                                                            "status": {"type": "string", "enum": ["documented", "reported", "legendary"]},
                                                                                                            "time": {"type": ["string", "null"]}}}}}}}}}


def narratives_with_haiku(con, llm: LLM, max_calls: int = 8, batch: int = 8, entities: int = 60, per_entity: int = 8, shard: int = 0, shards: int = 1) -> dict:
    """Narrativas (anécdotas, génesis, recepción…) de las fichas más ricas, por lotes con Haiku. Se guarda la CITA LITERAL verificada como texto de la
    narrativa (no una reescritura): solo el título y el tipo los pone el modelo. Se omite el párrafo inicial, que ya sale como resumen y biografía."""
    from .llm import UsageCapReached
    _, pv = load_prompt("extract_narratives_batch")
    ents = con.execute(
        "SELECT e.id, e.label, e.type, (SELECT COUNT(*) FROM claims c WHERE c.subject_id=e.id AND c.status!='deprecated') w FROM entities e WHERE e.provisional=0 AND e.type IN ('work','person','venue') "
        "AND e.id NOT IN (SELECT entity_id FROM corpus WHERE included=0) ORDER BY w DESC, e.id LIMIT ?", (entities,)).fetchall()
    todo = []
    for e in ents:
        d = con.execute("SELECT d.id FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es' ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (e["id"],)).fetchone()
        if not d:
            continue
        got = 0
        lead_skipped = False
        for p in con.execute("SELECT id,text FROM passages WHERE document_id=? ORDER BY char_start", (d["id"],)):
            t = p["text"]
            if len(t) < 110 or not t[0].isupper():
                continue
            if not lead_skipped:
                lead_skipped = True      # el primer párrafo de prosa es el inicial
                continue
            if len(t) < 250 or got >= per_entity or con.execute("SELECT 1 FROM extractions WHERE passage_id=? AND task='narratives_haiku'", (p["id"],)).fetchone():
                continue
            todo.append((p, e))
            got += 1
    todo = todo[shard::shards]
    st = {"calls": 0, "passages": 0, "narratives": 0, "discarded": 0, "pending_passages": len(todo)}
    for i in range(0, len(todo), batch):
        if st["calls"] >= max_calls:
            break
        group = todo[i:i + batch]
        blocks = [f"PASAJE {k}\nSUJETO: {e['label']} (tipo: {e['type']})\nTEXTO: {p['text']}" for k, (p, e) in enumerate(group)]
        try:
            r = llm.run("extract_narratives_batch", "\n\n=====\n\n".join(blocks), NARR_BATCH_SCHEMA)
        except UsageCapReached:
            break
        st["calls"] += 1
        by_index = {it["index"]: it["narratives"] for it in r.data["items"]}
        for k, (p, e) in enumerate(group):
            kept = 0
            for n in by_index.get(k, [])[:2]:
                q = n["quote"].strip()
                qs = p["text"].find(q)
                if qs < 0 or not (60 <= len(q) <= 450) or not n["title"].strip() or (n["time"] and not is_edtf(n["time"])):
                    st["discarded"] += 1
                    continue
                nid = "N" + sha256(f"{e['id']}|{p['id']}|{q[:60]}")[:16]
                con.execute("INSERT OR IGNORE INTO narratives VALUES(?,?,?,?,?,?,?,?)", (nid, n["type"], n["title"].strip(), q, n["time"], None, n["status"], 0.7))
                con.execute("INSERT OR IGNORE INTO narrative_entities VALUES(?,?,?)", (nid, e["id"], "sujeto"))
                con.execute("INSERT OR IGNORE INTO narrative_evidence VALUES(?,?,?,?,?)", (nid, p["id"], q, qs, qs + len(q)))
                kept += 1
            st["narratives"] += kept
            st["passages"] += 1
            con.execute("INSERT OR REPLACE INTO extractions VALUES(?,?,?,?,?,?,?)", (p["id"], "narratives_haiku", pv, "claude", "ok", len(by_index.get(k, [])), kept))
        con.commit()
    st["pending_passages"] -= st["passages"]
    return st
