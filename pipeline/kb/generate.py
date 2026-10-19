"""Cuestionarios (§7, sin LLM) y resúmenes narrativos anclados (§6, validados). Los monográficos independientes se retiraron."""
from __future__ import annotations

import json
from pathlib import Path
import random
import sqlite3

from .db import DATA, jdump
from .llm import LLM
from .models import Segment
from .util import edtf_year, now, sha256
from .validate import material_text, validate_segment

KB_VERSION_KEY = "kb_version"
MIN_QUIZ_CONF = 0.7

WRITE_SCHEMA = {"type": "object", "required": ["segments"], "additionalProperties": False,
                "properties": {"segments": {"type": "array", "items": {
                    "type": "object", "required": ["text", "cites"], "additionalProperties": False,
                    "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}}}}}}
JUDGE_SCHEMA = {"type": "object", "required": ["verdict", "reason"], "additionalProperties": False,
                "properties": {"verdict": {"type": "string", "enum": ["yes", "partial", "no"]}, "reason": {"type": "string"}}}


def kb_version(con) -> str:
    r = con.execute("SELECT v FROM meta WHERE k=?", (KB_VERSION_KEY,)).fetchone()
    return r["v"] if r else "0"


def _label(con, eid):
    r = con.execute("SELECT label FROM entities WHERE id=?", (eid,)).fetchone()
    return r["label"] if r else eid


def save_output(con, oid: str, kind: str, content: dict, report: dict) -> None:
    out_dir = DATA / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{oid}.json"
    path.write_text(json.dumps(content, ensure_ascii=False, indent=1))
    con.execute("INSERT OR REPLACE INTO outputs VALUES(?,?,?,?,?,?)", (oid, kind, kb_version(con), now(), str(path), jdump(report)))
    con.execute("DELETE FROM output_citations WHERE output_id=?", (oid,))
    for s in content.get("segments", []) + [{"id": q["id"], "claim_ids": q["claim_ids"]} for q in content.get("questions", [])]:
        for cid in s.get("claim_ids", []):
            con.execute("INSERT INTO output_citations VALUES(?,?,?,NULL,NULL)", (oid, s["id"], cid))
        for nid in s.get("narrative_ids", []):
            con.execute("INSERT INTO output_citations VALUES(?,?,NULL,?,NULL)", (oid, s["id"], nid))
        for pid in s.get("passage_ids", []):
            con.execute("INSERT INTO output_citations VALUES(?,?,NULL,NULL,?)", (oid, s["id"], pid))
    con.commit()


# --- cuestionarios ------------------------------------------------------------

def build_quiz(con: sqlite3.Connection, quiz_id: str = "general", max_questions: int = 30, seed: int = 1) -> dict:
    rng = random.Random(seed)
    good = {"composer": [], "premiere_date": []}
    for c in con.execute("SELECT * FROM claims WHERE status='asserted' AND confidence>=? AND property IN ('composer','premiere_date') ORDER BY id", (MIN_QUIZ_CONF,)):
        good[c["property"]].append(c)
    all_composers = {json.loads(c["value_json"]) for c in con.execute("SELECT value_json FROM claims WHERE property='composer' AND value_type='entity'")}
    true_by_work: dict[str, set] = {}
    for c in con.execute("SELECT subject_id,value_json FROM claims WHERE property='composer' AND status!='deprecated'"):
        true_by_work.setdefault(c["subject_id"], set()).add(json.loads(c["value_json"]))
    people = sorted(p for p in all_composers if not p.startswith("kb:"))
    qs = []
    for c in good["composer"]:
        w = c["subject_id"]
        ans = json.loads(c["value_json"])
        if c["value_type"] != "entity" or ans.startswith("kb:"):
            continue
        pool = [p for p in people if p not in true_by_work.get(w, set())]
        if len(pool) < 3:
            continue
        opts = [ans] + rng.sample(pool, 3)
        rng.shuffle(opts)
        qs.append({"id": "Q" + sha256(c["id"])[:12], "prompt": f"¿Quién compuso la música de «{_label(con, w)}»?",
                   "options": [_label(con, o) for o in opts], "answer_index": opts.index(ans), "claim_ids": [c["id"]]})
    years_true: dict[str, set] = {}
    for c in con.execute("SELECT subject_id,value_json FROM claims WHERE property='premiere_date' AND status!='deprecated'"):
        years_true.setdefault(c["subject_id"], set()).add(edtf_year(json.loads(c["value_json"])))
    for c in good["premiere_date"]:
        v = json.loads(c["value_json"])
        y = edtf_year(v)
        if not y or len(v) < 4 or "~" in v or "X" in v or "?" in v:
            continue
        w = c["subject_id"]
        cands = [y + d for d in (-12, -7, -3, 3, 8, 15) if (y + d) not in years_true.get(w, set())]
        if len(cands) < 3:
            continue
        opts = [y] + rng.sample(cands, 3)
        rng.shuffle(opts)
        qs.append({"id": "Q" + sha256(c["id"])[:12], "prompt": f"¿En qué año se estrenó «{_label(con, w)}»?",
                   "options": [str(o) for o in opts], "answer_index": opts.index(y), "claim_ids": [c["id"]]})
    # preguntas sobre disputas
    for r in con.execute("SELECT * FROM claim_relations WHERE relation='conflicts_with' AND claim_a IN (SELECT id FROM claims WHERE property='premiere_date') LIMIT 5"):
        a = con.execute("SELECT * FROM claims WHERE id=?", (r["claim_a"],)).fetchone()
        b = con.execute("SELECT * FROM claims WHERE id=?", (r["claim_b"],)).fetchone()
        qs.append({"id": "Q" + sha256(a["id"] + b["id"])[:12],
                   "prompt": f"¿Qué ocurre con la fecha de estreno de «{_label(con, a['subject_id'])}» en las fuentes?",
                   "options": ["Todas las fuentes coinciden", f"Hay dos versiones: {json.loads(a['value_json'])} y {json.loads(b['value_json'])}", "No consta ninguna fecha"],
                   "answer_index": 1, "claim_ids": [a["id"], b["id"]]})
    rng.shuffle(qs)
    qs = qs[:max_questions]
    content = {"id": quiz_id, "title": "Ensayo general", "questions": qs}
    save_output(con, f"quiz:{quiz_id}", "quiz", content, {"questions": len(qs), "uncited": 0})
    return content


# --- juez semántico (lo usan los resúmenes) ---------------------------------------------------------------------

def _judge(llm: LLM):
    def j(text: str, mats: list[str]) -> str:
        user = "SEGMENTO: " + text + "\nMATERIAL:\n" + "\n".join(f"- {m}" for m in mats)
        return llm.run("judge", user, JUDGE_SCHEMA).data["verdict"]
    return j


# --- resúmenes narrativos (Qwen local, validados) ---------------------------------------------------------------------------

def summary_material(con, entity_id: str, max_paragraphs: int = 6, max_claims: int = 14) -> list[dict]:
    """Pasajes literales del artículo de Wikipedia de la entidad + sus datos estructurados principales."""
    d = con.execute("SELECT d.id FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es' "
                    "ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (entity_id,)).fetchone()
    items: list[dict] = []
    if d:
        total = 0
        for p in con.execute("SELECT id,text FROM passages WHERE document_id=? ORDER BY char_start", (d["id"],)):
            t = " ".join(p["text"].split())
            if len(t) < 110 or any(x in t for x in ("ISBN", "ISSN", "Consultado", "Archivado desde")):
                continue
            items.append({"id": p["id"], "text": t})
            total += len(t)
            if len(items) >= max_paragraphs or total >= 3200:
                break
    if not items:
        return []
    cids = [c["id"] for c in con.execute("SELECT id FROM claims WHERE subject_id=? AND status NOT IN ('deprecated','disputed') AND property NOT IN ('genre_concept','coordinates','city') "
                                         "ORDER BY (source_kind='structured') DESC, confidence DESC, property LIMIT ?", (entity_id, max_claims))]
    texts, _ = material_text(con, cids)
    return items + [{"id": i, "text": texts[i]} for i in cids if i in texts]


def build_summary(con, llm: LLM, entity_id: str, judge_sample: float = 0.0, rng: random.Random | None = None) -> dict | None:
    """Resumen de 3–5 frases escrito por el modelo local y validado frase a frase (§6). Sin material o con <2 frases válidas: no hay resumen."""
    mats = summary_material(con, entity_id)
    if not mats:
        return None
    e = con.execute("SELECT label,type FROM entities WHERE id=?", (entity_id,)).fetchone()
    kind = {"person": "persona", "work": "obra", "venue": "teatro"}.get(e["type"], e["type"])
    provided = {m["id"] for m in mats}
    material = "\n".join(f"[{m['id']}] {m['text']}" for m in mats)
    r = llm.run("summarize", f"SUJETO: {e['label']} ({kind})\nMATERIAL:\n{material}", WRITE_SCHEMA)
    judge = _judge(llm) if judge_sample and (rng or random).random() < judge_sample else None
    segments, report = [], {"proposed": len(r.data["segments"]), "kept": 0, "dropped": [], "judged": bool(judge), "model": r.model}
    for i, s in enumerate(r.data["segments"][:6]):
        seg = Segment(id=f"s{i}", text=s["text"].strip(), claim_ids=[c for c in s["cites"] if c.startswith("C")],
                      narrative_ids=[], passage_ids=[c for c in s["cites"] if c.startswith("P")])
        v = validate_segment(con, seg, provided, judge)
        if v["ok"]:
            segments.append(seg)
            report["kept"] += 1
        else:
            report["dropped"].append({"text": seg.text, "failures": v["failures"]})
    if len(segments) < 2:
        return {"content": None, "report": report}
    content = {"id": entity_id, "title": e["label"], "model": r.model, "prompt": r.prompt_version, "segments": [s.model_dump() for s in segments]}
    save_output(con, f"summary:{entity_id}", "summary", content, report)
    return {"content": content, "report": report}


def local_check(con, llm: LLM, seg: dict, cache_only: bool = False) -> str:
    """Segundo filtro con el modelo local: ¿el material citado respalda TODA la frase? -> yes | partial | no."""
    cites = seg.get("claim_ids", []) + seg.get("narrative_ids", []) + seg.get("passage_ids", [])
    texts, _ = material_text(con, cites)
    if not texts:
        return "no"
    user = "SEGMENTO: " + seg["text"] + "\nMATERIAL:\n" + "\n".join(f"- {t}" for t in texts.values())
    return llm.run("check_support", user, JUDGE_SCHEMA).data["verdict"]


def check_summaries(con, llm: LLM, redo: bool = False) -> dict:
    """Pasa el segundo filtro local por todos los resúmenes: quita las frases que no respalda su cita; con <2 frases, el resumen se retira."""
    st = {"summaries": 0, "segments": 0, "dropped": 0, "withdrawn": 0}
    for o in con.execute("SELECT id,content_path,validation_report_json FROM outputs WHERE kind='summary'").fetchall():
        rep = json.loads(o["validation_report_json"])
        if rep.get("local_checked") and not redo:
            continue
        path = Path(o["content_path"])
        content = json.loads(path.read_text())
        keep, verdicts = [], []
        for s in content["segments"]:
            v = local_check(con, llm, s)
            verdicts.append(v)
            if v == "yes":
                keep.append(s)
            else:
                rep.setdefault("dropped", []).append({"text": s["text"], "failures": [f"segundo filtro local: {v}"]})
        st["summaries"] += 1
        st["segments"] += len(verdicts)
        st["dropped"] += len(verdicts) - len(keep)
        content["segments"] = keep if len(keep) >= 2 else []
        st["withdrawn"] += int(len(keep) < 2)
        rep.update({"local_checked": True, "local_verdicts": verdicts, "kept": len(keep)})
        path.write_text(json.dumps(content, ensure_ascii=False, indent=1))
        con.execute("UPDATE outputs SET validation_report_json=? WHERE id=?", (jdump(rep), o["id"]))
        con.commit()
    return st


def calibrate_local_check(con, llm: LLM) -> dict:
    """Compara el segundo filtro local con el juez Haiku en los resúmenes que Haiku revisó: Haiku aprobó las frases que quedaron
    ("yes") y marcó como "partial" las que descartó por 'el juez responde partial'."""
    import re as _re
    from .generate import summary_material  # noqa: F401  (mismo módulo)
    tp = fn = fp = tn = 0
    for o in con.execute("SELECT id,content_path,validation_report_json FROM outputs WHERE kind='summary'").fetchall():
        rep = json.loads(o["validation_report_json"])
        if not rep.get("judged"):
            continue
        eid = o["id"].split(":", 1)[1]
        mats = summary_material(con, eid)
        e = con.execute("SELECT label,type FROM entities WHERE id=?", (eid,)).fetchone()
        kind = {"person": "persona", "work": "obra", "venue": "teatro"}.get(e["type"], e["type"])
        material = "\n".join(f"[{m['id']}] {m['text']}" for m in mats)
        proposed = llm.run("summarize", f"SUJETO: {e['label']} ({kind})\nMATERIAL:\n{material}", WRITE_SCHEMA).data["segments"]   # caché: las mismas frases
        by_text = {" ".join(s["text"].split()): s for s in proposed}
        labelled = [(s, "yes") for s in json.loads(Path(o["content_path"]).read_text())["segments"]]
        for d in rep.get("dropped", []):
            if any("juez responde partial" in f for f in d["failures"]) and " ".join(d["text"].split()) in by_text:
                s = by_text[" ".join(d["text"].split())]
                labelled.append(({"text": s["text"], "claim_ids": [c for c in s["cites"] if c.startswith("C")], "narrative_ids": [], "passage_ids": [c for c in s["cites"] if c.startswith("P")]}, "partial"))
        for s, truth in labelled:
            v = local_check(con, llm, s)
            flagged = v != "yes"
            if truth == "partial":
                tp += int(flagged); fn += int(not flagged)
            else:
                fp += int(flagged); tn += int(not flagged)
    return {"haiku_partial": tp + fn, "haiku_yes": fp + tn, "local_catches_partial": tp, "local_misses_partial": fn,
            "local_wrongly_flags_yes": fp, "local_agrees_yes": tn,
            "recall_partial": round(tp / (tp + fn), 2) if tp + fn else None, "false_alarm_rate": round(fp / (fp + tn), 2) if fp + tn else None}


JUDGE_BATCH_SCHEMA = {"type": "object", "required": ["verdicts"], "additionalProperties": False,
                      "properties": {"verdicts": {"type": "array", "items": {"type": "object", "required": ["summary", "index", "verdict"], "additionalProperties": False,
                                                                              "properties": {"summary": {"type": "integer"}, "index": {"type": "integer"},
                                                                                             "verdict": {"type": "string", "enum": ["yes", "partial", "no"]}}}}}}


def judge_summaries(con, llm: LLM, max_calls: int = 20, prefer_prompt: str | None = None, per_call: int = 3) -> dict:
    """Haiku revisa varios resúmenes por llamada (`per_call`), empezando por las fichas con más datos (o por los de `prefer_prompt`). Quita las
    frases que no aprueba; con <2 frases el resumen se retira. Se detiene al agotar el tope diario (config/llm.yaml: se mantiene bajo a propósito)."""
    from .llm import UsageCapReached
    st = {"calls": 0, "summaries_verified": 0, "segments_dropped": 0, "withdrawn": 0}
    rows = con.execute(
        "SELECT o.id,o.content_path,o.validation_report_json,(SELECT COUNT(*) FROM claims c WHERE c.subject_id=substr(o.id,9) AND c.status!='deprecated') n "
        "FROM outputs o WHERE o.kind='summary' ORDER BY n DESC, o.id").fetchall()
    if prefer_prompt:
        rows = sorted(rows, key=lambda o: json.loads(Path(o["content_path"]).read_text()).get("prompt") != prefer_prompt)
    pending = []
    for o in rows:
        rep = json.loads(o["validation_report_json"])
        content = json.loads(Path(o["content_path"]).read_text())
        if not rep.get("haiku_checked") and content["segments"]:
            pending.append((o, rep, content))
    for i in range(0, len(pending), per_call):
        if st["calls"] >= max_calls:
            break
        group = pending[i:i + per_call]
        blocks = []
        for k, (o, rep, content) in enumerate(group):
            frases = []
            for j, s in enumerate(content["segments"]):
                cites = s.get("claim_ids", []) + s.get("narrative_ids", []) + s.get("passage_ids", [])
                texts, _ = material_text(con, cites)
                frases.append(f"FRASE {j}: {s['text']}\nMATERIAL {j}:\n" + "\n".join(f"- {t}" for t in texts.values()))
            blocks.append(f"RESUMEN {k}:\n\n" + "\n\n".join(frases))
        try:
            r = llm.run("judge_batch", "\n\n=====\n\n".join(blocks), JUDGE_BATCH_SCHEMA)
        except UsageCapReached:
            break
        st["calls"] += 1
        verdict = {(v["summary"], v["index"]): v["verdict"] for v in r.data["verdicts"]}
        for k, (o, rep, content) in enumerate(group):
            keep = [s for j, s in enumerate(content["segments"]) if verdict.get((k, j)) == "yes"]
            for j, s in enumerate(content["segments"]):
                if verdict.get((k, j)) != "yes":
                    rep.setdefault("dropped", []).append({"text": s["text"], "failures": [f"el juez responde {verdict.get((k, j), 'sin veredicto')}"]})
            st["segments_dropped"] += len(content["segments"]) - len(keep)
            withdrawn = len(keep) < 2
            st["withdrawn"] += int(withdrawn)
            st["summaries_verified"] += 1
            content["segments"] = [] if withdrawn else keep
            rep.update({"haiku_checked": True, "kept": len(keep)})
            Path(o["content_path"]).write_text(json.dumps(content, ensure_ascii=False, indent=1))
            con.execute("UPDATE outputs SET validation_report_json=? WHERE id=?", (jdump(rep), o["id"]))
        con.commit()
    return st
