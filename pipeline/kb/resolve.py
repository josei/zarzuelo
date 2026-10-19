"""Resolver (§4.5): entidades por nombre, conflictos, confianza y clasificaciones."""
from __future__ import annotations

import re
import sqlite3

from .db import jdump
from .util import edtf_interval, norm, sha256, source_family

SINGLE_VALUED = {"premiere_date", "birth_date", "death_date", "opening_date", "closing_date", "birth_place", "death_place"}


def _index(con) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    import json
    for e in con.execute("SELECT id,label,labels_alt FROM entities WHERE id NOT LIKE 'kb:%'"):  # incluye valores enlazados de Wikidata (lugares, géneros…)
        for n in [e["label"], *json.loads(e["labels_alt"] or "[]")]:
            idx.setdefault(norm(n), []).append(e["id"])
    return idx


_PREFIX = re.compile(r"^(?:la\s+ciudad\s+de|la\s+villa\s+de|el\s+pueblo\s+de|el\s+municipio\s+de|el\s+maestro|don|doña|d\.)\s+", re.I)


def clean_name(name: str, prop: str) -> list[str]:
    """Variantes de búsqueda de una mención, de más a menos específica (no cambia el valor guardado)."""
    out = [name]
    n = _PREFIX.sub("", name).strip()
    if n != name:
        out.append(n)
    if prop in ("birth_place", "death_place", "location", "premiere_venue") and "," in n:
        out.append(n.split(",")[0].strip())
    return out


def _partial_match(con, subject_id: str, prop: str, name: str) -> str | None:
    """Mención parcial ("Arrieta", "Maestro Boccherini"): si sus palabras están todas en el nombre de UNA entidad que ya es valor de esa
    propiedad para el mismo sujeto (p. ej. el compositor ya resuelto de la obra), es esa entidad."""
    import json
    toks = set(norm(_PREFIX.sub("", name)).split())
    if not toks:
        return None
    hit = []
    for r in con.execute("SELECT DISTINCT e.id,e.label FROM claims c JOIN entities e ON c.value_json='\"'||e.id||'\"' WHERE c.subject_id=? AND c.property=? AND c.value_type='entity' AND e.id NOT LIKE 'kb:%'", (subject_id, prop)):
        if toks <= set(norm(r["label"]).split()):
            hit.append(r["id"])
    return hit[0] if len(hit) == 1 else None


def merge_provisional_people(con) -> int:
    """Entidades provisionales de tipo persona (p. ej. artistas de MusicBrainz) con la misma forma normalizada (sin tildes ni mayúsculas)
    que UNA persona no provisional: son la misma. Se reapuntan sus afirmaciones y se conserva su ID externo."""
    import json
    people: dict[str, list[str]] = {}
    for e in con.execute("SELECT id,label FROM entities WHERE type='person' AND provisional=0"):
        people.setdefault(norm(e["label"]), []).append(e["id"])
    n = 0
    for e in con.execute("SELECT id,label FROM entities WHERE type='person' AND provisional=1").fetchall():
        tgt = people.get(norm(e["label"]), [])
        if len(tgt) != 1:
            continue
        con.execute("UPDATE claims SET value_json=? WHERE value_json=? AND value_type='entity'", (jdump(tgt[0]), jdump(e["id"])))
        con.execute("UPDATE claims SET subject_id=? WHERE subject_id=?", (tgt[0], e["id"]))
        ex = json.loads(con.execute("SELECT external_ids FROM entities WHERE id=?", (tgt[0],)).fetchone()[0] or "{}")
        key = "musicbrainz_artist" if e["id"].startswith("MBA:") else "merged_from"
        ex.setdefault(key, e["id"].split(":", 1)[-1])
        con.execute("UPDATE entities SET external_ids=? WHERE id=?", (jdump(ex), tgt[0]))
        con.execute("DELETE FROM entities WHERE id=?", (e["id"],))
        n += 1
    con.commit()
    return n


def drop_invalid_names(con) -> int:
    """Retira afirmaciones extraídas cuyo valor-nombre empieza por minúscula (p. ej. «perdida» como compositor)."""
    n = 0
    for c in con.execute("SELECT c.id FROM claims c JOIN entities e ON c.value_json='\"'||e.id||'\"' WHERE c.source_kind='extracted' AND c.value_type='entity' "
                         "AND c.property IN ('composer','librettist','performer','birth_place','death_place','premiere_venue','location','company') "
                         "AND e.id LIKE 'kb:%' AND substr(e.label,1,1) != upper(substr(e.label,1,1))").fetchall():
        con.execute("UPDATE claims SET status='deprecated' WHERE id=?", (c["id"],))
        n += 1
    con.commit()
    return n


def resolve_names(con: sqlite3.Connection, llm=None) -> dict:
    """claims con value_type='name' -> 'entity'. Coincidencia única por nombre normalizado; si no, entidad provisional.
    Con varios candidatos y `llm`, desempata Claude (tarea 4); `null` => provisional."""
    # re-resolver: las menciones que quedaron como provisionales vuelven a intentarse (el índice puede haber crecido)
    for c in con.execute("SELECT c.id,e.label FROM claims c JOIN entities e ON c.value_json='\"'||e.id||'\"' WHERE c.value_type='entity' AND e.id LIKE 'kb:%'").fetchall():
        con.execute("UPDATE claims SET value_json=?, value_type='name' WHERE id=?", (jdump(c["label"]), c["id"]))
    con.execute("DELETE FROM entities WHERE id LIKE 'kb:%' AND id NOT IN (SELECT json_extract(value_json,'$') FROM claims WHERE value_type='entity')")
    idx = _index(con)
    stats = {"resolved": 0, "provisional": 0}
    import json
    import re
    # autorías conjuntas ("A y B", "A, B y C"): una afirmación por persona, con la misma evidencia
    for c in con.execute("SELECT * FROM claims WHERE value_type='name' AND property IN ('composer','librettist','performer')").fetchall():
        name = json.loads(c["value_json"])
        parts = [p.strip() for p in re.split(r"\s+y\s+|,\s*", name) if p.strip()]
        if len(parts) > 1 and norm(name) not in idx and all(len(p.split()) >= 2 for p in parts):
            for part in parts:
                nid = "C" + sha256(f"{c['id']}|{part}")[:16]
                con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at) "
                            "SELECT ?,subject_id,property,?,'name',qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at FROM claims WHERE id=?",
                            (nid, jdump(part), c["id"]))
                con.execute("INSERT OR IGNORE INTO claim_evidence SELECT ?,passage_id,quote,quote_start,quote_end FROM claim_evidence WHERE claim_id=?", (nid, c["id"]))
            con.execute("DELETE FROM claim_evidence WHERE claim_id=?", (c["id"],))
            con.execute("DELETE FROM claims WHERE id=?", (c["id"],))
    for c in con.execute("SELECT id,subject_id,property,value_json FROM claims WHERE value_type='name'").fetchall():
        name = json.loads(c["value_json"])
        cands: list[str] = []
        for variant in clean_name(name, c["property"]):
            cands = list(dict.fromkeys(idx.get(norm(variant), [])))
            if cands:
                break
        eid = cands[0] if len(cands) == 1 else None
        if not cands:
            eid = _partial_match(con, c["subject_id"], c["property"], name)
        if len(cands) > 1:
            # homónimos: se elige el candidato que ya es valor de esa propiedad para ese sujeto en otra afirmación
            known = {json.loads(r[0]) for r in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property=? AND value_type='entity'", (c["subject_id"], c["property"]))}
            hit = [x for x in cands if x in known]
            eid = hit[0] if len(hit) == 1 else None
        if len(cands) > 1 and llm is not None:
            eid = _tiebreak(con, llm, c["id"], name, cands)
        if eid:
            stats["resolved"] += 1
        else:
            eid = "kb:" + sha256(norm(name))[:12]
            con.execute("INSERT OR IGNORE INTO entities(id,type,label,provisional) VALUES(?,?,?,1)", (eid, "other", name))
            stats["provisional"] += 1
        con.execute("UPDATE claims SET value_json=?, value_type='entity' WHERE id=?", (jdump(eid), c["id"]))
    con.commit()
    return stats


def _tiebreak(con, llm, claim_id, name, cands) -> str | None:
    ctx = con.execute("SELECT quote FROM claim_evidence WHERE claim_id=? LIMIT 1", (claim_id,)).fetchone()
    rows = [dict(r) for r in con.execute(f"SELECT id,label,external_ids FROM entities WHERE id IN ({','.join('?'*len(cands))})", cands)]
    user = f"MENCIÓN: {name}\nCONTEXTO: {ctx['quote'] if ctx else ''}\nCANDIDATOS: {jdump(rows)}"
    schema = {"type": "object", "required": ["entity_id"], "properties": {"entity_id": {"type": ["string", "null"]}}}
    r = llm.run("resolve_entities", user, schema)
    eid = r.data.get("entity_id")
    return eid if eid in cands else None


def _compatible(a: str, b: str) -> tuple[bool, bool]:
    """(compatibles, uno_refina_al_otro) para valores de fecha EDTF."""
    ia, ib = edtf_interval(a), edtf_interval(b)
    if not ia or not ib:
        return a == b, False
    overlap = ia[0] <= ib[1] and ib[0] <= ia[1]
    return overlap, overlap and a != b


def detect_conflicts(con: sqlite3.Connection) -> int:
    """Dos valores distintos para la misma propiedad => dos afirmaciones `conflicts_with` y `disputed` (§2.1)."""
    import json
    con.execute("DELETE FROM claim_relations")
    con.execute("UPDATE claims SET status='asserted' WHERE status='disputed'")
    n = 0
    rows = con.execute("SELECT id,subject_id,property,value_json,value_type FROM claims WHERE status!='deprecated' "
                       f"AND property IN ({','.join('?'*len(SINGLE_VALUED))}) ORDER BY id", sorted(SINGLE_VALUED)).fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["subject_id"], r["property"]), []).append(r)
    for (_s, _p), g in groups.items():
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g[i], g[j]
                va, vb = json.loads(a["value_json"]), json.loads(b["value_json"])
                if va == vb:
                    con.execute("INSERT OR IGNORE INTO claim_relations VALUES(?,?,'supports')", (a["id"], b["id"]))
                    continue
                if a["value_type"] == "edtf" and b["value_type"] == "edtf":
                    ok, refines = _compatible(va, vb)
                    if ok:
                        con.execute("INSERT OR IGNORE INTO claim_relations VALUES(?,?,?)", (a["id"], b["id"], "refines" if refines else "supports"))
                        continue
                con.execute("INSERT OR IGNORE INTO claim_relations VALUES(?,?,'conflicts_with')", (a["id"], b["id"]))
                con.execute("UPDATE claims SET status='disputed' WHERE id IN (?,?) AND status='asserted'", (a["id"], b["id"]))
                n += 1
    con.commit()
    return n


def compute_confidence(con: sqlite3.Connection) -> None:
    """Confianza calculada (no inventada): tipo de fuente + concordancia entre fuentes distintas − matiz − disputa."""
    base = {"structured": 0.7, "extracted": 0.5, "derived": 0.4}
    for c in con.execute("SELECT id,subject_id,property,value_json,source_kind,status,hedge FROM claims").fetchall():
        srcs = {source_family(r["source"]) for r in con.execute(
            "SELECT DISTINCT d.source FROM claims c2 JOIN claim_evidence ce ON ce.claim_id=c2.id JOIN passages p ON p.id=ce.passage_id "
            "JOIN documents d ON d.id=p.document_id WHERE c2.subject_id=? AND c2.property=? AND c2.value_json=?",
            (c["subject_id"], c["property"], c["value_json"]))}
        conf = base[c["source_kind"]] + 0.15 * max(0, len(srcs) - 1)
        if c["hedge"] or c["status"] in ("attributed", "legendary"):
            conf -= 0.15
        if c["status"] == "disputed":
            conf *= 0.6
        if c["status"] == "deprecated":
            conf = 0.0
        con.execute("UPDATE claims SET confidence=? WHERE id=?", (round(max(0.0, min(conf, 0.99)), 2), c["id"]))
    con.commit()


_ACTS = re.compile(r"\b(?:en|de)\s+(?:un|una|dos|tres|cuatro|cinco|seis|\d+)\s+(?:actos?|cuadros?|jornadas?)\b.*$", re.I)


def denomination_key(label: str) -> str:
    """Forma comparable de una denominación: sin paréntesis, sin «en tres actos y…», sin tildes ni mayúsculas."""
    return norm(_ACTS.sub("", re.sub(r"\(.*?\)", "", label)).strip(" ,.;:-"))


def _longest_prefix(key: str, by_label: dict[str, str]) -> str | None:
    """«sainete lírico en un acto» -> concepto «sainete lírico»; «zarzuela cómica» -> «zarzuela»: el prefijo de palabras más largo que sea concepto."""
    toks = key.split()
    for n in range(len(toks), 0, -1):
        cid = by_label.get(" ".join(toks[:n]))
        if cid:
            return cid
    return None


def reject_off_subject_dates(con) -> dict:
    """Fechas de nacimiento/muerte EXTRAÍDAS que probablemente no son del sujeto (hijos, esposas, coetáneos citados en su artículo).
    Se aceptan si están en el párrafo inicial del artículo de la persona o si coinciden en año (±1) con una fecha de Wikidata; si no, se
    retiran (`deprecated`, con el motivo en `qualifiers`). Sin fecha de Wikidata con la que contrastar, se conservan."""
    import json
    st = {"checked": 0, "kept_lead": 0, "kept_agree": 0, "kept_nothing_to_compare": 0, "rejected": 0}
    for c in con.execute("SELECT c.id,c.subject_id,c.property,c.value_json,c.qualifiers_json FROM claims c JOIN entities e ON e.id=c.subject_id "
                         "WHERE c.source_kind='extracted' AND c.status!='deprecated' AND c.property IN ('birth_date','death_date') AND e.type='person'").fetchall():
        st["checked"] += 1
        years = {int(json.loads(r["value_json"])[:4]) for r in con.execute(
            "SELECT value_json FROM claims WHERE subject_id=? AND property=? AND source_kind='structured' AND status!='deprecated'", (c["subject_id"], c["property"]))}
        pids = [r["passage_id"] for r in con.execute("SELECT passage_id FROM claim_evidence WHERE claim_id=?", (c["id"],))]
        lead = None
        d = con.execute("SELECT d.id FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es' ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (c["subject_id"],)).fetchone()
        if d:
            lead = next((p["id"] for p in con.execute("SELECT id,text FROM passages WHERE document_id=? ORDER BY char_start", (d["id"],)) if len(p["text"]) >= 110 and p["text"][0].isupper()), None)
        if lead and lead in pids:
            st["kept_lead"] += 1
            continue
        if not years:
            st["kept_nothing_to_compare"] += 1
            continue
        y = int(json.loads(c["value_json"])[:4])
        if any(abs(y - w) <= 1 for w in years):
            st["kept_agree"] += 1
            continue
        q = json.loads(c["qualifiers_json"] or "{}")
        q["rejected"] = "fecha extraída fuera del párrafo inicial y que no coincide con Wikidata: probablemente de otra persona citada en el artículo"
        con.execute("UPDATE claims SET status='deprecated', qualifiers_json=? WHERE id=?", (jdump(q), c["id"]))
        st["rejected"] += 1
    con.commit()
    return st


def detect_genres_in_leads(con) -> dict:
    """Género de una obra en el primer párrafo de su artículo («es una ópera española en tres actos»): coincidencia determinista con el
    vocabulario (etiquetas y sinónimos) tras «es/fue una…», con la cita literal como evidencia. Es una afirmación `denomination`."""
    import json
    terms: dict[str, str] = {}
    for c in con.execute("SELECT pref_label,alt_labels FROM concepts"):
        for t in [c["pref_label"], *json.loads(c["alt_labels"])]:
            if len(t) >= 4:
                terms[t.lower()] = t
    if not terms:
        return {"works": 0, "claims": 0}
    pat = re.compile(r"(?<!\w)(?:es|fue|era|son|se\s+trata\s+de)\s+(?:una|un|el|la)?\s*(?:[\wáéíóúüñ-]+\s+){0,2}?(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")(?!\w)", re.I)
    st = {"works": 0, "claims": 0}
    for w in con.execute("SELECT e.id FROM entities e WHERE e.type='work' AND e.provisional=0 AND e.id NOT IN (SELECT entity_id FROM corpus WHERE included=0)").fetchall():
        d = con.execute("SELECT d.id FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es' ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (w["id"],)).fetchone()
        if not d:
            continue
        lead = next((p for p in con.execute("SELECT id,text FROM passages WHERE document_id=? ORDER BY char_start", (d["id"],)) if len(p["text"]) >= 110 and p["text"][0].isupper()), None)
        if not lead:
            continue
        m = pat.search(lead["text"][:400])
        if not m:
            continue
        quote, value = m.group(0), m.group(1)
        qs = lead["text"].find(quote)
        cid = "C" + sha256(f"{w['id']}|lead-genre|{norm(value)}")[:16]
        con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind,extracted_by_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, w["id"], "denomination", jdump(value), "literal", "{}", "asserted", 0.6, "extracted", jdump({"method": "vocab-lead-match", "version": "1", "date": __import__("kb.util", fromlist=["now"]).now()}), __import__("kb.util", fromlist=["now"]).now()))
        con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, lead["id"], quote, qs, qs + len(quote)))
        st["works"] += 1
        st["claims"] += 1
    con.commit()
    return st


def map_genres(con: sqlite3.Connection, llm=None) -> dict:
    """Clasificaciones (§2.3): `genre`/`denomination` -> concepto del vocabulario, como afirmación `genre_concept` con evidencia heredada."""
    import json
    concepts = {r["id"]: r for r in con.execute("SELECT * FROM concepts")}
    by_label: dict[str, str] = {}
    for cid, r in concepts.items():
        for n in [r["pref_label"], *json.loads(r["alt_labels"]), *json.loads(r["hidden_labels"])]:
            by_label[norm(n)] = cid
    stats = {"mapped": 0, "unmapped": 0}
    for c in con.execute("SELECT * FROM claims WHERE property IN ('genre','denomination') AND status!='deprecated'").fetchall():
        v = json.loads(c["value_json"])
        label = v
        if c["value_type"] == "entity":
            e = con.execute("SELECT label FROM entities WHERE id=?", (v,)).fetchone()
            label = e["label"] if e else v
            if v in concepts:
                by_label.setdefault(norm(label), v)
        cid = by_label.get(norm(label)) or by_label.get(denomination_key(label)) or _longest_prefix(denomination_key(label), by_label)
        if not cid and llm is not None and concepts:
            lst = "\n".join(f"{k}: {r['pref_label']}" for k, r in concepts.items())
            schema = {"type": "object", "required": ["concept_id"], "properties": {"concept_id": {"type": "string"}}}
            r = llm.run("classify_genre", f"ETIQUETA: {label}\nCONCEPTOS:\n{lst}", schema)
            if r.data.get("concept_id") in concepts:
                cid = r.data["concept_id"]
            elif r.engine == "local":
                r = llm.run("classify_genre", f"ETIQUETA: {label}\nCONCEPTOS:\n{lst}", schema, engine="claude")
                cid = r.data.get("concept_id") if r.data.get("concept_id") in concepts else None
        if not cid:
            stats["unmapped"] += 1
            continue
        nid = "C" + sha256(f"{c['id']}|concept|{cid}")[:16]
        con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,hedge,confidence,source_kind,extracted_by_json,created_at) "
                    "SELECT ?,subject_id,'genre_concept',?,'entity',?,status,hedge,confidence,'derived',?,created_at FROM claims WHERE id=?",
                    (nid, jdump(cid), jdump({"from_claim": c["id"], "label": label}), jdump({"method": "vocab-map"}), c["id"]))
        con.execute("INSERT OR IGNORE INTO claim_evidence SELECT ?,passage_id,quote,quote_start,quote_end FROM claim_evidence WHERE claim_id=?", (nid, c["id"]))
        stats["mapped"] += 1
    con.commit()
    return stats
