"""Orquestación: descubrir, capturar, refresco incremental, estimación y evaluación."""
from __future__ import annotations

import json
import re
import sqlite3

import yaml

from .db import DATA, ROOT, jdump
from .extract import CLAIMS_SCHEMA, extract_claims, extract_narratives
from .http import Http
from .llm import LLM
from .sources import mediawiki, wikidata
from .store import capture, segment, split_spans
from .util import now


def bump_version(con) -> str:
    v = now()
    con.execute("INSERT OR REPLACE INTO meta VALUES('kb_version',?)", (v,))
    con.commit()
    return v


def scope() -> dict:
    return yaml.safe_load((ROOT / "config" / "scope.yaml").read_text())


def discover(con, limit: int = 50, roots: dict[str, str] | None = None) -> list[str]:
    """Semilla en Wikidata. Registra en `corpus` lo que entra, con motivo (§4.1)."""
    http = Http("wikidata")
    qids: list[str] = []
    if roots is None:
        roots = dict(wikidata.GENRE_ROOTS)
        roots.update({r["pref_label"]: r["id"] for r in con.execute("SELECT id,pref_label FROM concepts")})
    for name, root in roots.items():
        for q in wikidata.discover(http, root, limit):
            if q not in qids:
                qids.append(q)
    qids = qids[:limit]
    for q in qids:
        con.execute("INSERT OR IGNORE INTO corpus VALUES(?,1,?)", (q, "semilla Wikidata: obra con compositor (P86) y género en el árbol de un concepto del vocabulario"))
    con.commit()
    return qids


def capture_wikidata(con, qids: list[str] | None = None) -> dict:
    http = Http("wikidata")
    qids = qids or [r["entity_id"] for r in con.execute("SELECT entity_id FROM corpus WHERE included=1")]
    stats = wikidata.ingest(con, http, qids, depth=1)
    stats["venue_cities"] = wikidata.derive_venue_cities(con, http)
    bump_version(con)
    return stats


def capture_wikipedia(con, include_people: bool = True, lang: str = "es") -> dict:
    """Instantáneas por oldid de las páginas Wikipedia de cada entidad del corpus (y sus autores) en el idioma `lang`."""
    src = f"wikipedia_{lang}"
    http = Http(src)
    stats = {"pages": 0, "missing": 0}
    ids = [r["entity_id"] for r in con.execute("SELECT entity_id FROM corpus WHERE included=1")]
    todo = list(ids)
    if include_people:
        for q in ids:
            for r in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property IN ('composer','librettist','premiere_venue') AND value_type='entity'", (q,)):
                p = json.loads(r["value_json"])
                if p not in todo:
                    todo.append(p)
    for eid in todo:
        e = con.execute("SELECT external_ids FROM entities WHERE id=?", (eid,)).fetchone()
        title = json.loads(e["external_ids"] or "{}").get(f"{lang}wiki") if e else None
        if not title:
            stats["missing"] += 1
            continue
        doc_id = mediawiki.ingest_page(con, http, src, title, lang=lang)
        if doc_id:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, eid))
            stats["pages"] += 1
    con.commit()
    bump_version(con)
    return stats


def capture_wikisource(con) -> dict:
    """Libretos de Wikisource ES: páginas enlazadas desde Wikidata (sitelink eswikisource) de las obras del corpus."""
    http = Http("wikisource_es")
    stats = {"pages": 0, "missing": 0}
    for r in con.execute("SELECT entity_id FROM corpus WHERE included=1").fetchall():
        e = con.execute("SELECT external_ids FROM entities WHERE id=?", (r["entity_id"],)).fetchone()
        title = json.loads(e["external_ids"] or "{}").get("eswikisource") if e else None
        if not title:
            stats["missing"] += 1
            continue
        doc_id = mediawiki.ingest_page(con, http, "wikisource_es", title)
        if doc_id:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, r["entity_id"]))
            stats["pages"] += 1
    con.commit()
    return stats


def capture_wikisource_search(con, limit: int | None = None) -> dict:
    """Busca libretos en Wikisource ES por título de la obra. Conservador: solo se acepta una página cuyo título normalizado
    coincide exactamente con la etiqueta de la obra (o es 'Etiqueta/…' = subpágina de ella). Nada de coincidencias aproximadas."""
    from .util import norm
    http = Http("wikisource_es")
    stats = {"searched": 0, "pages": 0, "no_match": 0}
    works = con.execute("SELECT e.id,e.label FROM entities e JOIN corpus c ON c.entity_id=e.id WHERE e.type='work' AND c.included=1 "
                        "AND NOT EXISTS (SELECT 1 FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=e.id AND d.source='wikisource_es')").fetchall()
    for w in works[:limit]:
        stats["searched"] += 1
        label = re.sub(r"\s*\(.*?\)\s*$", "", w["label"]).strip()   # sin la desambiguación '(zarzuela)'
        r = http.json(http.cfg["endpoint"], {"action": "query", "list": "search", "srsearch": f'intitle:"{label}"', "srnamespace": 0, "srlimit": 5, "format": "json"})
        hit = next((h["title"] for h in r["query"]["search"] if norm(h["title"]) == norm(label) or norm(h["title"]).startswith(norm(label) + "/")), None)
        if not hit:
            stats["no_match"] += 1
            continue
        doc_id = mediawiki.ingest_page(con, http, "wikisource_es", hit)
        if doc_id:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, w["id"]))
            stats["pages"] += 1
    con.commit()
    return stats


def discover_categories(con, categories: list[str], depth: int = 2, lang: str = "es") -> int:
    """Amplía el corpus con miembros de categorías de Wikipedia que tengan ficha en Wikidata (§4.1)."""
    http = Http(f"wikipedia_{lang}")
    seen, frontier, qids, pages = set(), list(categories), [], []
    for _ in range(depth + 1):
        nxt = []
        for cat in frontier:
            if cat in seen:
                continue
            seen.add(cat)
            cont = {}
            while True:
                r = http.json(http.cfg["endpoint"], {"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": 500, "format": "json", **cont}, use_cache=False)
                for m in r["query"]["categorymembers"]:
                    if m["ns"] == 14:
                        nxt.append(m["title"])
                    elif m["ns"] == 0:
                        pages.append((m["pageid"], cat))
                if "continue" not in r:
                    break
                cont = r["continue"]
        frontier = nxt
    cat_of = dict(pages)
    for i in range(0, len(pages), 50):   # lotes de 50 páginas por petición
        ids = [p for p, _ in pages[i:i + 50]]
        pr = http.json(http.cfg["endpoint"], {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "pageids": "|".join(map(str, ids)), "format": "json"})
        for pid, pg in pr["query"]["pages"].items():
            q = pg.get("pageprops", {}).get("wikibase_item")
            if q:
                qids.append((q, cat_of[int(pid)]))
    n = 0
    for q, cat in qids:
        if not con.execute("SELECT 1 FROM corpus WHERE entity_id=?", (q,)).fetchone():
            con.execute("INSERT INTO corpus VALUES(?,1,?)", (q, f"categoría de Wikipedia: {cat}"))
            n += 1
    con.commit()
    return n


def wikipedia_docs(con):
    return [r["id"] for r in con.execute("SELECT d.id FROM documents d WHERE d.source = 'wikipedia_es' ORDER BY d.retrieved_at")]


def extract_all(con, llm: LLM, narratives: bool = False, engine: str | None = None, limit_docs: int | None = None, progress=None) -> dict:
    tot = {"kept": 0, "discarded": 0, "passages": 0}
    docs = wikipedia_docs(con)[:limit_docs]
    for d in docs:
        s = extract_claims(con, llm, d, engine=engine, progress=progress)
        for k in tot:
            tot[k] += s.get(k, 0)
        if narratives:
            tot["narratives"] = tot.get("narratives", 0) + extract_narratives(con, llm, d)["kept"]
    if tot["kept"]:
        bump_version(con)
    return tot


# --- refresco incremental (§4) -----------------------------------------------------------------

def _latest_docs(con, source: str) -> dict[str, sqlite3.Row]:
    """entidad/título -> documento más reciente de esa fuente."""
    out: dict[str, sqlite3.Row] = {}
    for d in con.execute("SELECT * FROM documents WHERE source=? ORDER BY retrieved_at, rowid", (source,)):
        key = d["stable_id"].split("@")[0] if source == "wikidata" else d["url"].split("title=")[-1].split("&")[0]
        out[key] = d
    return out


def check_changes(con, sources: list[str] | None = None) -> list[dict]:
    """Paso 1 del refresco: ¿algo ha cambiado? Solo compara revisiones, no descarga contenido."""
    changes = []
    if not sources or "wikidata" in sources:
        http = Http("wikidata")
        latest = _latest_docs(con, "wikidata")
        ids = sorted(latest)
        for i in range(0, len(ids), 50):
            r = http.json(http.cfg["endpoint"], {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": "info", "format": "json"}, use_cache=False)
            for qid, e in r.get("entities", {}).items():
                rev = str(e.get("lastrevid", ""))
                if rev and latest[qid]["stable_id"] != f"{qid}@{rev}":
                    changes.append({"source": "wikidata", "key": qid, "old": latest[qid]["stable_id"], "new": rev})
    if not sources or "wikipedia_es" in sources:
        http = Http("wikipedia_es")
        for key, d in _latest_docs(con, "wikipedia_es").items():
            title = key.replace("_", " ")
            from urllib.parse import unquote
            lr = mediawiki.latest_revision(http, unquote(title))
            if lr and str(lr[0]) != d["stable_id"]:
                changes.append({"source": "wikipedia_es", "key": unquote(title), "old": d["stable_id"], "new": str(lr[0])})
    return changes


def refresh(con, llm: LLM, sources: list[str] | None = None, dry_run: bool = False) -> dict:
    changes = check_changes(con, sources)
    report = {"at": now(), "dry_run": dry_run, "changed": changes, "claims_new": 0, "claims_updated": 0, "claims_deprecated": 0, "llm_calls_estimated": 0}
    if dry_run:
        for ch in changes:
            if ch["source"] == "wikipedia_es":
                got = mediawiki.fetch_page(Http("wikipedia_es"), "wikipedia_es", ch["key"], int(ch["new"]))
                if got:
                    text = got[0]
                    subj = con.execute("SELECT e.label FROM documents d JOIN doc_subjects s ON s.document_id=d.id JOIN entities e ON e.id=s.entity_id WHERE d.source='wikipedia_es' AND d.url LIKE ?", (f"%title={ch['key'].replace(' ', '_')}&%",)).fetchone()
                    for s, e in split_spans(text):
                        if e - s >= 30 and subj and llm.pending("extract_claims", f"SUJETO: {subj['label']}\nPASAJE: {text[s:e]}"):
                            report["llm_calls_estimated"] += 1
        return report
    if not changes:
        return report
    before = {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM claims")}
    wd = [c["key"] for c in changes if c["source"] == "wikidata"]
    old_docs = {}
    if wd:
        for q, d in _latest_docs(con, "wikidata").items():
            if q in wd:
                old_docs[d["id"]] = d
        wikidata.ingest(con, Http("wikidata"), wd, depth=0)
    wp = [c for c in changes if c["source"] == "wikipedia_es"]
    http = Http("wikipedia_es")
    for ch in wp:
        old = [d for k, d in _latest_docs(con, "wikipedia_es").items() if k.replace("_", " ") in (ch["key"], ch["key"].replace(" ", "_"))]
        subj = con.execute("SELECT entity_id FROM doc_subjects WHERE document_id=?", (old[0]["id"],)).fetchone() if old else None
        new_id = mediawiki.ingest_page(con, http, "wikipedia_es", ch["key"], int(ch["new"]))
        if new_id and subj:
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (new_id, subj["entity_id"]))
            old_docs[old[0]["id"]] = old[0]
            extract_claims(con, llm, new_id)
    # revisión de afirmaciones apoyadas en pasajes antiguos (§4.4)
    for old_id in old_docs:
        for c in con.execute("SELECT DISTINCT claim_id FROM claim_evidence ce JOIN passages p ON p.id=ce.passage_id WHERE p.document_id=?", (old_id,)).fetchall():
            cid = c["claim_id"]
            quotes = con.execute("SELECT passage_id,quote FROM claim_evidence WHERE claim_id=?", (cid,)).fetchall()
            supported = False
            for q in quotes:
                doc = con.execute("SELECT document_id FROM passages WHERE id=?", (q["passage_id"],)).fetchone()["document_id"]
                if doc != old_id:
                    continue
                new_doc = _newer(con, old_docs[old_id])
                if new_doc:
                    hit = con.execute("SELECT id,char_start FROM passages WHERE document_id=? AND instr(text,?)>0", (new_doc, q["quote"])).fetchone()
                    if hit:
                        pst = con.execute("SELECT text FROM passages WHERE id=?", (hit["id"],)).fetchone()["text"]
                        qs = pst.find(q["quote"])
                        con.execute("DELETE FROM claim_evidence WHERE claim_id=? AND passage_id=?", (cid, q["passage_id"]))
                        con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, hit["id"], q["quote"], qs, qs + len(q["quote"])))
                        supported = True
            has_other = con.execute("SELECT 1 FROM claim_evidence ce JOIN passages p ON p.id=ce.passage_id WHERE ce.claim_id=? AND p.document_id!=?", (cid, old_id)).fetchone()
            if not supported and not has_other:
                con.execute("UPDATE claims SET status='deprecated' WHERE id=?", (cid,))
    after = {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM claims")}
    report["claims_new"] = len(set(after) - set(before))
    report["claims_deprecated"] = sum(1 for k, v in after.items() if v == "deprecated" and before.get(k) != "deprecated")
    report["claims_updated"] = sum(1 for k, v in after.items() if k in before and before[k] == "deprecated" and v != "deprecated")
    con.commit()
    bump_version(con)
    rd = DATA / "reports"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"refresh-{now().replace(':', '')}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def _newer(con, old_doc) -> str | None:
    r = con.execute("SELECT id FROM documents WHERE source=? AND url LIKE ? AND rowid>(SELECT rowid FROM documents WHERE id=?) ORDER BY rowid DESC LIMIT 1",
                    (old_doc["source"], old_doc["url"].split("&oldid")[0].split("?revision")[0] + "%" if old_doc["source"] != "wikidata" else old_doc["url"].split("?")[0] + "%", old_doc["id"])).fetchone()
    return r["id"] if r else None


# --- estimación y evaluación -------------------------------------------------------------------

def estimate(con, llm: LLM) -> dict:
    """Llamadas pendientes por motor y tarea antes de lanzar nada."""
    from .llm.core import load_prompt
    _, pv = load_prompt("extract_claims")
    n = 0
    for d in wikipedia_docs(con):
        subj = con.execute("SELECT e.label FROM doc_subjects s JOIN entities e ON e.id=s.entity_id WHERE s.document_id=?", (d,)).fetchone()
        if not subj:
            continue
        for p in con.execute("SELECT id,text FROM passages WHERE document_id=?", (d,)):
            if len(p["text"]) >= 30 and not con.execute("SELECT 1 FROM extractions WHERE passage_id=? AND task='extract_claims' AND prompt_version=?", (p["id"], pv)).fetchone():
                n += 1
    return {"extract_claims": {"engine": llm.cfg["tasks"]["extract_claims"]["engine"], "pending_calls": n}}


def gold_draft(con, n: int = 40, seed: int = 7, path=None) -> int:
    """Borrador del conjunto de oro para revisar a mano: pasajes REALES de Wikipedia ES con los hechos que propuso el modelo.
    El revisor marca `ok: true/false` en cada candidato y añade en `faltan` lo que el pasaje afirma y no se extrajo."""
    import random
    rng = random.Random(seed)
    rows = con.execute(
        "SELECT p.id pid,p.text,d.url,e.label subj,e.type stype FROM passages p JOIN documents d ON d.id=p.document_id "
        "JOIN doc_subjects ds ON ds.document_id=d.id JOIN entities e ON e.id=ds.entity_id "
        "WHERE d.source='wikipedia_es' AND length(p.text)>=120 AND length(p.text)<=900").fetchall()
    with_claims = {r[0] for r in con.execute("SELECT DISTINCT passage_id FROM claim_evidence ce JOIN claims c ON c.id=ce.claim_id WHERE c.source_kind='extracted'")}
    pos = [r for r in rows if r["pid"] in with_claims]
    neg = [r for r in rows if r["pid"] not in with_claims and re.search(r"\b1[5-9]\d\d\b", r["text"])]   # sin hechos extraídos pero con un año: ¿se escapó algo?
    rng.shuffle(pos); rng.shuffle(neg)
    pick = pos[: int(n * 0.75)] + neg[: n - int(n * 0.75)]
    items = []
    for r in pick:
        cands = []
        for c in con.execute("SELECT c.property,c.value_json,c.value_type,ce.quote FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id WHERE ce.passage_id=? AND c.source_kind='extracted' AND c.status!='deprecated'", (r["pid"],)):
            v = json.loads(c["value_json"])
            if c["value_type"] == "entity":
                e = con.execute("SELECT label FROM entities WHERE id=?", (v,)).fetchone()
                v = e["label"] if e else v
            cands.append({"property": c["property"], "value": str(v), "quote": c["quote"], "ok": None})
        items.append({"subject": r["subj"], "type": r["stype"], "source": r["url"], "passage": r["text"], "candidatos": cands, "faltan": []})
    out = yaml.safe_dump({"instrucciones": "En cada candidato pon ok: true si el pasaje afirma ese hecho sobre el SUJETO, o ok: false si no. En `faltan` añade {property, value} de lo que el pasaje afirma sobre el sujeto y no aparece. Borra los items que no puedas juzgar.", "items": items}, allow_unicode=True, sort_keys=False, width=120)
    (path or ROOT / "gold" / "extract_claims.draft.yaml").write_text(out)
    return len(items)


def eval_gold(con, llm: LLM, task: str = "extract_claims", engine: str | None = None) -> dict:
    """Compara la extracción con el conjunto de oro (gold/<task>.yaml): precisión y cobertura."""
    from .extract import verify_claim
    gold = yaml.safe_load((ROOT / "gold" / f"{task}.yaml").read_text())["items"]
    for it in gold:   # formato revisado: candidatos con ok:true + `faltan`
        if "candidatos" in it and "expected" not in it:
            it["expected"] = [{"property": c["property"], "value": c["value"]} for c in it["candidatos"] if c.get("ok") is True] + \
                             [{"property": f["property"], "value": f["value"]} for f in it.get("faltan") or []]
            it["unreviewed"] = any(c.get("ok") is None for c in it["candidatos"])
    gold = [it for it in gold if not it.get("unreviewed")]
    tp = fp = fn = 0
    for it in gold:
        if it.get("fictional"):
            continue
        r = llm.run(task, f"SUJETO: {it['subject']}\nPASAJE: {it['passage']}", CLAIMS_SCHEMA, engine=engine)
        got = {(c["property"], c["value"]) for c in r.data["claims"] if verify_claim(c, it["passage"])[0]}
        exp = {(e["property"], e["value"]) for e in it["expected"]}
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"task": task, "engine": engine or llm.cfg["tasks"][task]["engine"], "precision": round(prec, 3), "coverage": round(rec, 3), "tp": tp, "fp": fp, "fn": fn}


def eval_silver(con) -> dict:
    """Concordancia de las afirmaciones extraídas con las estructuradas de Wikidata (señal automática, no sustituye al oro)."""
    agree = disagree = 0
    for c in con.execute("SELECT * FROM claims WHERE source_kind='extracted' AND status!='deprecated' AND property IN ('composer','librettist','premiere_date','birth_date','death_date')"):
        peers = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property=? AND source_kind='structured' AND status!='deprecated'", (c["subject_id"], c["property"])).fetchall()
        if not peers:
            continue
        if any(p["value_json"] == c["value_json"] or (c["value_type"] == "edtf" and json.loads(p["value_json"])[:4] == json.loads(c["value_json"])[:4]) for p in peers):
            agree += 1
        else:
            disagree += 1
    t = agree + disagree
    return {"compared": t, "agree": agree, "disagree": disagree, "agreement": round(agree / t, 3) if t else None}
