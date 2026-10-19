"""Exportador: API estática /api/v1 (§8.3). Respeta `consult_only`: nunca expone su texto."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from pydantic import TypeAdapter

from .db import ROOT, jdump
from .generate import kb_version
from .models import SCHEMA_VERSION, Claim, Concept, Document, Entity, Narrative, Output, Passage
from .util import edtf_interval, now, source_family

OUT = ROOT / "web" / "public" / "api" / "v1"
MAX_BYTES = 500_000

TYPE_DIR = {"work": "works", "person": "people", "venue": "venues", "company": "companies"}


def fname(entity_id: str) -> str:
    """Nombre de fichero seguro (solo ASCII, sin «:» ni tildes) para alojar en S3, GitHub Pages o Windows. Los QID no cambian.
    La web aplica la misma función (web/src/api/client.ts: fname) para pedir el fichero."""
    import unicodedata
    return re.sub(r"[^A-Za-z0-9._-]+", "_", unicodedata.normalize("NFKD", entity_id).encode("ascii", "ignore").decode()).strip("_")


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def _evidence_ref(con, claim_id=None, narrative_id=None):
    if claim_id:
        rows = con.execute("SELECT ce.passage_id,ce.quote,ce.quote_start,ce.quote_end,p.document_id,d.reuse FROM claim_evidence ce "
                           "JOIN passages p ON p.id=ce.passage_id JOIN documents d ON d.id=p.document_id WHERE ce.claim_id=?", (claim_id,))
    else:
        rows = con.execute("SELECT ce.passage_id,ce.quote,ce.quote_start,ce.quote_end,p.document_id,d.reuse FROM narrative_evidence ce "
                           "JOIN passages p ON p.id=ce.passage_id JOIN documents d ON d.id=p.document_id WHERE ce.narrative_id=?", (narrative_id,))
    return rows.fetchall()


_STATUS_RANK = {"disputed": 4, "legendary": 3, "attributed": 2, "asserted": 1}


def _merge_duplicates(claims: list[dict]) -> list[dict]:
    """El mismo hecho (propiedad + valor) dado por varias fuentes se muestra una vez, con todas sus evidencias."""
    merged: dict[tuple, dict] = {}
    for c in claims:
        k = (c["property"], c["value"])
        m = merged.get(k)
        if not m:
            merged[k] = {**c, "n_claims": 1}
            continue
        m["evidence_ids"] = list(dict.fromkeys(m["evidence_ids"] + c["evidence_ids"]))
        m["source_families"] = sorted(set(m.get("source_families", [])) | set(c.get("source_families", [])))
        m["relations"] = m["relations"] + [r for r in c["relations"] if r not in m["relations"]]
        m["confidence"] = max(m["confidence"], c["confidence"])
        if c.get("wikidata_rank") == "preferred":
            m["wikidata_rank"] = "preferred"
        m["references"] = list(dict.fromkeys((m.get("references") or []) + (c.get("references") or [])))
        m["hedge"] = m["hedge"] or c["hedge"]
        if _STATUS_RANK.get(c["status"], 0) > _STATUS_RANK.get(m["status"], 0):
            m["status"] = c["status"]
        if c["source_kind"] != m["source_kind"]:
            m["source_kind"] = "structured"      # respaldado también por una fuente estructurada
        m["n_claims"] += 1
    return list(merged.values())


def _clean_alt_labels(label: str, alts: list[str], limit: int = 6) -> list[str]:
    """Nombres alternativos sin repeticiones que solo difieren en tildes o mayúsculas (se prefiere la forma con tildes)."""
    from .util import norm
    best: dict[str, str] = {}
    for a in alts:
        k = norm(a)
        if k == norm(label):
            continue
        if k not in best or sum(ch > "\u007f" for ch in a) > sum(ch > "\u007f" for ch in best[k]):
            best[k] = a
    return list(best.values())[:limit]


def _contains(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return b[0] <= a[0] and a[1] <= b[1]


def _single_valued_primary(claims: list[dict]) -> list[dict]:
    """Propiedades de un solo valor (fechas, lugares de nacimiento/muerte…): las fechas imprecisas compatibles se pliegan en la más
    precisa (sus evidencias se conservan); si quedan valores distintos, el mejor respaldado (rango preferido de Wikidata, más
    corroboración, más confianza) es el principal y el resto se marcan como alternativas en disputa."""
    from .resolve import SINGLE_VALUED
    out: list[dict] = []
    by_prop: dict[str, list[dict]] = {}
    for c in claims:
        (by_prop.setdefault(c["property"], []) if c["property"] in SINGLE_VALUED else out).append(c)
    for prop, group in by_prop.items():
        if group[0]["value_type"] == "edtf":
            for a in sorted(group, key=lambda c: -len(str(c["value"]))):   # de más a menos precisa
                ia = edtf_interval(str(a["value"]))
                target = next((b for b in group if b is not a and not b.get("_folded") and ia and edtf_interval(str(b["value"]))
                               and _contains(edtf_interval(str(b["value"])), ia) and len(str(b["value"])) > len(str(a["value"]))), None)
                if target:
                    target["evidence_ids"] = list(dict.fromkeys(target["evidence_ids"] + a["evidence_ids"]))
                    target["source_families"] = sorted(set(target.get("source_families", [])) | set(a.get("source_families", [])))
                    target["n_claims"] += a["n_claims"]
                    a["_folded"] = True
            group = [c for c in group if not c.get("_folded")]
        group.sort(key=lambda c: (c.get("wikidata_rank") == "preferred", c["n_claims"], c["confidence"]), reverse=True)
        for i, c in enumerate(group):
            if i > 0:
                c["alternative"] = True
                c["status"] = "disputed"
        out.extend(group)
    return out


def export_schemas(out: Path) -> None:
    from pydantic.json_schema import models_json_schema
    models = {"claim": Claim, "entity": Entity, "narrative": Narrative, "concept": Concept,
              "document": Document, "passage": Passage, "output": Output}
    for name, model in models.items():
        _w(out / "schema" / f"{name}.schema.json", TypeAdapter(model).json_schema())
    # un único esquema con $defs compartidos: de aquí salen los tipos TypeScript sin duplicados
    _, defs = models_json_schema([(m, "validation") for m in models.values()], ref_template="#/$defs/{model}")
    _w(out / "schema" / "api.schema.json", {"title": "Api", "type": "object", "additionalProperties": False,
                                             "properties": {k: {"$ref": f"#/$defs/{m.__name__}"} for k, m in models.items()}, **defs})


def export_all(con: sqlite3.Connection, out: Path = OUT) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    export_schemas(out)
    excluded = {r[0] for r in con.execute("SELECT entity_id FROM corpus WHERE included=0")}   # p. ej. duplicados (ver mark_duplicates)
    ents = {e["id"]: e for e in con.execute("SELECT * FROM entities") if e["id"] not in excluded}
    # documentos usados como fuentes (sin texto consult_only)
    docs_used: set[str] = set()
    counts = {k: 0 for k in TYPE_DIR.values()}
    index: dict[str, list] = {k: [] for k in TYPE_DIR.values()}

    def entity_claims(eid):
        res = []
        for c in con.execute("SELECT * FROM claims WHERE subject_id=? AND status!='deprecated' ORDER BY property,id", (eid,)):
            v = json.loads(c["value_json"])
            vlabel = ents[v]["label"] if c["value_type"] == "entity" and v in ents else None
            # solo se enlaza a entidades con ficha exportada (no provisionales y de un tipo con página)
            vlink = None
            if c["value_type"] == "entity" and v in ents and not ents[v]["provisional"] and ents[v]["type"] in TYPE_DIR:
                vlink = {"work": "obra", "person": "persona", "venue": "teatro", "company": "compania"}[ents[v]["type"]]
            ev = _evidence_ref(con, claim_id=c["id"])
            for r in ev:
                docs_used.add(r["document_id"])
            rel = [dict(r) for r in con.execute("SELECT claim_a,claim_b,relation FROM claim_relations WHERE claim_a=? OR claim_b=?", (c["id"], c["id"]))]
            fams = sorted({source_family(d["source"]) for d in (con.execute("SELECT d.source FROM documents d WHERE d.id=?", (r["document_id"],)).fetchone() for r in ev) if d})
            qj = json.loads(c["qualifiers_json"] or "{}")
            res.append({"id": c["id"], "wikidata_rank": qj.get("wikidata_rank"), "references": qj.get("references", []), "alternative": False, "source_families": fams, "property": c["property"], "value": v, "value_label": vlabel, "value_link": vlink, "value_type": c["value_type"],
                        "status": c["status"], "hedge": c["hedge"], "confidence": c["confidence"], "source_kind": c["source_kind"],
                        "evidence_ids": [r["passage_id"] for r in ev], "relations": rel})
        return _single_valued_primary(_merge_duplicates(res))

    name_pat, name_map = _name_index(ents)
    docs_by_entity = documents_view(con, out, ents)
    videos_view(con, out, ents)
    authored = authored_works(con, ents)
    for eid, e in ents.items():
        d = TYPE_DIR.get(e["type"])
        if not d or e["provisional"]:
            continue
        claims = entity_claims(eid)
        bio = biography(con, eid, name_pat, name_map)
        summ = summary_of(con, eid, name_pat, name_map)
        narr = []
        for n in con.execute("SELECT n.* FROM narratives n JOIN narrative_entities ne ON ne.narrative_id=n.id WHERE ne.entity_id=?", (eid,)):
            ev = _evidence_ref(con, narrative_id=n["id"])
            for r in ev:
                docs_used.add(r["document_id"])
            narr.append({"id": n["id"], "type": n["type"], "title": n["title"], "summary": n["summary"], "time": n["time_edtf"],
                         "status": n["status"], "evidence_ids": [r["passage_id"] for r in ev]})
        _w(out / d / f"{eid}.json", {"id": eid, "type": e["type"], "label": e["label"], "labels_alt": _clean_alt_labels(e["label"], json.loads(e["labels_alt"])),
                                      "external_ids": json.loads(e["external_ids"]), "claims": claims, "narratives": narr, "biography": bio, "summary": summ, "scores": scores_of(con, eid) if e["type"] == "work" else [], "videos": videos_of(con, eid), "authored": authored.get(eid, []) if e["type"] == "person" else [],
                                      "documents": (docs_by_entity.get(eid) or [])[:12], "documents_total": len(docs_by_entity.get(eid) or []),
                                      "rism": rism_of(con, eid)[:15] if e["type"] == "person" else [], "rism_total": len(rism_of(con, eid)) if e["type"] == "person" else 0,
                                      "rism_url": f"https://rism.online/{json.loads(e['external_ids']).get('rism_person')}" if e["type"] == "person" and json.loads(e["external_ids"]).get("rism_person") else None})
        # evidencias troceadas por entidad
        pids = {p for c in claims for p in c["evidence_ids"]} | {p for n in narr for p in n["evidence_ids"]} | {p["passage_id"] for p in (bio or {"paragraphs": []})["paragraphs"]} | {i for sg in (summ or {"segments": []})["segments"] for i in sg["evidence_ids"]}
        if bio:
            docs_used.add(bio["document_id"])
        ev_items = []
        for pid in sorted(pids):
            p = con.execute("SELECT p.id,p.text,p.char_start,p.char_end,p.document_id,d.reuse FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?", (pid,)).fetchone()
            # consult_only: se da la referencia (pasaje y documento) pero NO el texto
            ev_items.append({"id": p["id"], "document_id": p["document_id"], "char_start": p["char_start"], "char_end": p["char_end"],
                             "reuse": p["reuse"], "text": p["text"] if p["reuse"] == "reusable" else None})
        _w(out / "evidence" / f"{eid}.json", ev_items)
        counts[d] += 1
        bp = best_premiere(con, eid) if d == "works" else None
        year = bp[0][:4] if bp else None   # misma fecha de estreno que la portada (una por obra)
        comp = [c["value_label"] for c in claims if c["property"] == "composer"]
        index[d].append({"id": eid, "label": e["label"], **({"year": year, "composer": comp} if d == "works" else {})})
    for d, items in index.items():
        if items:
            _w(out / d / "index.json", sorted(items, key=lambda x: x["label"]))
    # fuentes
    for did in sorted(docs_used):
        r = con.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
        _w(out / "sources" / f"{did}.json", {k: r[k] for k in ("id", "source", "url", "stable_id", "retrieved_at", "sha256", "license", "reuse")})
    # vocabulario: cada concepto con su fuente, sinónimos con recuento de uso, jerarquía y sus obras (cronológicas)
    genres = []
    for g in con.execute("SELECT * FROM concepts"):
        rows = con.execute("SELECT DISTINCT cl.subject_id, cl.qualifiers_json FROM claims cl WHERE cl.property='genre_concept' AND cl.value_json=? AND cl.status!='deprecated'", (jdump(g["id"]),)).fetchall()
        works, used = {}, {}
        for r in rows:
            e = ents.get(r["subject_id"])
            if not e or e["type"] != "work" or e["provisional"]:
                continue
            lab = (json.loads(r["qualifiers_json"] or "{}").get("label") or "").strip()
            if lab:
                used[lab] = used.get(lab, 0) + 1
            if r["subject_id"] not in works:
                bp = best_premiere(con, e["id"])
                comps = [ents[json.loads(x[0])]["label"] for x in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='composer' AND value_type='entity' AND status!='deprecated'", (e["id"],))
                         if json.loads(x[0]) in ents and not ents[json.loads(x[0])]["provisional"]][:2]
                works[e["id"]] = {"id": e["id"], "label": e["label"], "year": int(bp[0][:4]) if bp else None, "composers": list(dict.fromkeys(comps))}
        ws = sorted(works.values(), key=lambda w: (w["year"] or 9999, w["label"]))
        decades: dict[int, int] = {}
        for w in ws:
            if w["year"]:
                decades[w["year"] // 10 * 10] = decades.get(w["year"] // 10 * 10, 0) + 1
        broader = json.loads(g["broader"])
        wiki = (g["scope_source"] or "").startswith("https://es.wikipedia.org")
        # «definición» solo si la fuente es el artículo de Wikipedia de ESE concepto; si no, es un pasaje donde se usa el término
        from urllib.parse import unquote
        m = re.search(r"title=([^&]+)", g["scope_source"] or "")
        src_title = _base_title(unquote(m.group(1)).replace("_", " ")) if m else ""
        is_article = wiki and src_title in {_base_title(g["pref_label"]), *(_base_title(a) for a in json.loads(g["alt_labels"]))} and src_title == _base_title(g["pref_label"]) or \
            (wiki and g["id"].startswith("Q") and src_title == _base_title(g["pref_label"]))
        genres.append({"id": g["id"], "pref_label": g["pref_label"], "alt_labels": json.loads(g["alt_labels"]), "hidden_labels": json.loads(g["hidden_labels"]),
                       "scope_note": g["scope_note"] if g["scope_note"] and g["scope_note"] != "music genre" else None, "scope_source": g["scope_source"],
                       "note_kind": ("definicion" if is_article else "uso" if wiki else "wikidata"),
                       "license": "CC-BY-SA-4.0" if wiki else "CC0-1.0", "broader": broader, "related": json.loads(g["related"]),
                       "denominations": sorted(({"label": k, "count": v} for k, v in used.items()), key=lambda x: -x["count"]),
                       "works": ws, "by_decade": [{"decade": d, "n": n} for d, n in sorted(decades.items())]})
    ids = {g["id"] for g in genres}
    for g in genres:
        g["narrower"] = [{"id": o["id"], "label": o["pref_label"]} for o in genres if g["id"] in o["broader"]]
        g["broader"] = [{"id": b, "label": next((x["pref_label"] for x in genres if x["id"] == b), b)} for b in g["broader"] if b in ids]
        _w(out / "genres" / f"{fname(g['id'])}.json", g)
    if genres:
        _w(out / "genres" / "index.json", [{"id": g["id"], "label": g["pref_label"], "n": len(g["works"]), "broader": [b["id"] for b in g["broader"]],
                                            "note": (g["scope_note"] or "")[:200] if g["note_kind"] == "definicion" else ""} for g in sorted(genres, key=lambda x: -len(x["works"]))])
    views(con, out, ents)
    home_view(con, out, ents)
    collaborations_view(con, out, ents)
    # salidas generadas
    qi = []
    for o in con.execute("SELECT * FROM outputs"):
        content = json.loads(Path(o["content_path"]).read_text())
        _resolve_evidence(con, content)
        if o["kind"] == "quiz":
            _w(out / "quizzes" / f"{content['id']}.json", content)
            qi.append({"id": content["id"], "title": content["title"], "n": len(content["questions"])})
    if qi:
        _w(out / "quizzes" / "index.json", qi)
    # búsqueda
    search = [{"id": e["id"], "label": e["label"], "type": e["type"], "alt": " ".join(json.loads(e["labels_alt"]))}
              for e in ents.values() if e["type"] in TYPE_DIR and not e["provisional"]]
    _w(out / "search" / "index.json", search)
    manifest = {"schema_version": SCHEMA_VERSION, "kb_version": kb_version(con), "generated_at": now(), "counts": {
        **counts, "claims": con.execute("SELECT COUNT(*) FROM claims WHERE status!='deprecated'").fetchone()[0],
        "passages": con.execute("SELECT COUNT(*) FROM passages").fetchone()[0], "sources": len(docs_used),
        "concepts": len(genres)}}
    _w(out / "manifest.json", manifest)
    return manifest


def _passages_of(con, claim_ids=(), narrative_ids=(), passage_ids=()) -> list[str]:
    ids = list(passage_ids)
    for c in claim_ids:
        ids += [r[0] for r in con.execute("SELECT passage_id FROM claim_evidence WHERE claim_id=?", (c,))]
    for n in narrative_ids:
        ids += [r[0] for r in con.execute("SELECT passage_id FROM narrative_evidence WHERE narrative_id=?", (n,))]
    return list(dict.fromkeys(ids))


def _resolve_evidence(con, content: dict) -> None:
    """El frontal resuelve citas contra /evidence/{entity_id}.json: se le da la entidad y los ids de pasaje."""
    for q in content.get("questions", []):
        q["evidence_ids"] = _passages_of(con, q["claim_ids"])
        r = con.execute("SELECT subject_id FROM claims WHERE id=?", (q["claim_ids"][0],)).fetchone()
        q["entity_id"] = r[0] if r else None
    for s in content.get("segments", []):
        s["evidence_ids"] = _passages_of(con, s.get("claim_ids", []), s.get("narrative_ids", []), s.get("passage_ids", []))


def _base_title(label: str) -> str:
    from .util import norm
    return norm(re.sub(r"\s*\(.*?\)\s*", " ", label))


def possible_duplicates(con, ents) -> dict[str, str]:
    """Obras que parecen la misma con dos identificadores (p. ej. dos ítems de Wikidata para dos artículos de Wikipedia):
    mismo título base, mismos compositores y mismo año de estreno. No se fusionan: se marcan. -> {duplicada: principal}."""
    groups: dict[tuple, list[str]] = {}
    for e in ents.values():
        if e["type"] != "work" or e["provisional"]:
            continue
        comps = tuple(sorted({json.loads(r[0]) for r in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='composer' AND value_type='entity' AND status!='deprecated'", (e["id"],))}))
        bp = best_premiere(con, e["id"])
        year = edtf_interval(bp[0])[0] if bp and edtf_interval(bp[0]) else None
        if comps and year:
            groups.setdefault((_base_title(e["label"]), comps, year), []).append(e["id"])
    out: dict[str, str] = {}
    for ids in groups.values():
        if len(ids) > 1:
            w = {i: con.execute("SELECT COUNT(*) FROM claims WHERE subject_id=? AND status!='deprecated'", (i,)).fetchone()[0] for i in ids}
            main = max(ids, key=lambda i: (w[i], i))
            for i in ids:
                if i != main:
                    out[i] = main
    return out


_YEAR = re.compile(r"\b(1[4-9]\d\d|20[0-2]\d)\b")
_GENERIC = {"zarzuela", "ópera", "opera", "sainete", "revista", "tonadilla", "género chico", "opereta", "comedia", "drama", "música", "teatro", "orquesta"}
_NOT_PROSE = re.compile(r"ISBN|ISSN|Consultado|consultado el|pp\.|\bvol\.|Archivado desde|Wayback|\bdoi\b", re.I)


def _name_index(ents) -> tuple[re.Pattern | None, dict[str, tuple[str, str]]]:
    """Expresión regular con los nombres de todas las fichas exportables (más largos primero) -> (route, id)."""
    route = {"work": "obra", "person": "persona", "venue": "teatro"}
    names: dict[str, tuple[str, str]] = {}
    for e in ents.values():
        if e["type"] not in route or e["provisional"]:
            continue
        for n in [e["label"], *json.loads(e["labels_alt"] or "[]")]:
            n = re.sub(r"\s*\(.*?\)\s*$", "", n).strip()      # sin «(zarzuela)»
            if len(n) >= 6 and n[0].isupper() and n.lower() not in _GENERIC and n not in names:
                names[n] = (route[e["type"]], e["id"])
    if not names:
        return None, {}
    pat = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?![\w])")
    return pat, names


def _segments(text: str, self_id: str, pat, names) -> list[dict]:
    """Trocea un párrafo en tramos: texto, enlace a otra ficha o año resaltado."""
    marks: list[tuple[int, int, dict]] = []
    if pat:
        for m in pat.finditer(text):
            route, eid = names[m.group(1)]
            if eid != self_id:
                marks.append((m.start(), m.end(), {"to": f"/{route}/{eid}"}))
    for m in _YEAR.finditer(text):
        if not any(a <= m.start() < b for a, b, _ in marks):
            marks.append((m.start(), m.end(), {"year": True}))
    out, pos = [], 0
    for a, b, extra in sorted(marks):
        if a < pos:
            continue
        if a > pos:
            out.append({"t": text[pos:a]})
        out.append({"t": text[a:b], **extra})
        pos = b
    if pos < len(text):
        out.append({"t": text[pos:]})
    return out


_DOC_TOPIC = re.compile(r"zarzuel|sarsuel|tonadill|sainete|g[eé]nero (chico|[ií]nfimo)|[oó]pera|opereta|operette|libreto|partitur|particel|teatro l[ií]rico|revista musical|"
                        r"musique dramatique|programme|coliseo|cuplé|couplet|havanera|zortzico|jota|habanera|romanza", re.I)
_TYPE_LABEL = {"text": "Texto", "image": "Imagen", "image fixe": "Imagen", "sound": "Sonido", "monographie imprimée": "Impreso", "printed monograph": "Impreso"}
DOC_SOURCES = {"europeana": "Europeana / Hispana", "prensa_catalana": "Biblioteca de Catalunya (MDC)", "gallica": "Gallica (BnF)", "bvph": "Prensa histórica (BVPH)"}
_KEYS = {"title": ("título", "title"), "creators": ("creador", "creator"), "date": ("fecha", "date"), "type": ("tipo", "type"), "subjects": ("materia", "subject"),
         "inst": ("institución", "publisher", "fondo"), "original": ("ver en origen",), "descr": ("descripción", "description")}
_RIGHTS = (("publicdomain/zero", "CC0"), ("publicdomain/mark", "Dominio público"), ("by-sa", "CC BY-SA"), ("licenses/by/4", "CC BY 4.0"), ("domaine public", "Dominio público"),
           ("CC BY 4.0", "CC BY 4.0"))


def parse_record(text: str) -> dict:
    """Lector común de las fichas de fondos digitalizados (Europeana/Hispana, MDC, Gallica, BVPH), que usan claves distintas."""
    d: dict = {k: [] for k in _KEYS}
    for line in text.splitlines()[1:]:
        if ": " not in line:
            continue
        k, v = line.split(": ", 1)
        for name, aliases in _KEYS.items():
            if k.strip().lower() in aliases and v.strip():
                d[name].append(v.strip())
    return d


def _rights_label(lic: str) -> str:
    return next((lab for needle, lab in _RIGHTS if needle.lower() in (lic or "").lower()), (lic or "")[:30])


def documents_view(con, out: Path, ents) -> dict[str, list[dict]]:
    """Documentos digitalizados (libretos, partituras, prensa…) de fuentes abiertas: metadatos y enlaces al original; nada se almacena.
    Devuelve {entity_id: [doc]} y escribe views/documents.json con todos."""
    from .store import document_text
    route = {"work": "obra", "person": "persona", "venue": "teatro"}
    allr, by_entity = [], {}
    for src, label in DOC_SOURCES.items():
        for d in con.execute("SELECT * FROM documents WHERE source=? AND reuse='reusable' ORDER BY retrieved_at, rowid", (src,)):
            full = document_text(con, d["id"])
            rec = parse_record(full)
            title = (rec["title"] or [None])[0]
            if not title or not _DOC_TOPIC.search(" ".join([title, *rec["subjects"], *rec["descr"]])):    # solo lo relacionado con el teatro lírico
                continue
            linked = [{"id": e, "label": ents[e]["label"], "route": route[ents[e]["type"]]} for (e,) in con.execute("SELECT entity_id FROM doc_subjects WHERE document_id=?", (d["id"],))
                      if e in ents and not ents[e]["provisional"] and ents[e]["type"] in route]
            item = {"id": d["id"], "source": src, "source_label": label, "title": title[:240], "creators": list(dict.fromkeys(n for n in (re.sub(r"\s*<https?://[^>]+>", "", c).strip()[:90] for c in rec["creators"]) if n and not n.startswith("http")))[:3],
                    "date": (rec["date"] or [None])[0], "type": next((_TYPE_LABEL[t.lower()] for t in rec["type"] if t.lower() in _TYPE_LABEL), None), "subjects": rec["subjects"][:3], "institution": (rec["inst"] or [None])[0],
                    "rights": _rights_label(d["license"]), "url": (rec["original"] or [d["url"]])[0], "record_url": d["url"], "entities": linked}
            allr.append(item)
            for l in linked:
                by_entity.setdefault(l["id"], []).append(item)
    # el mismo ítem llega a veces por dos registros (p. ej. Europeana y el repositorio de origen): se funden por título y fecha, conservando el más completo
    from .util import norm
    best: dict[tuple, dict] = {}
    for it in allr:
        k = (norm(it["title"])[:140], it["date"])
        cur = best.get(k)
        if cur is None:
            best[k] = it
            continue
        rich = lambda x: (len(x["creators"]), len(x["entities"]), x["source"] == "europeana")
        keep, other = (it, cur) if rich(it) > rich(cur) else (cur, it)
        keep["entities"] = list({e["id"]: e for e in keep["entities"] + other["entities"]}.values())
        keep["creators"] = keep["creators"] or other["creators"]
        best[k] = keep
    allr = list(best.values())
    by_entity = {}
    for it in allr:
        for l in it["entities"]:
            by_entity.setdefault(l["id"], []).append(it)
    _w(out / "views" / "documents.json", allr)
    return by_entity


def authored_works(con, ents) -> dict[str, list[dict]]:
    """Obras de cada persona, a partir de las afirmaciones composer/librettist de las obras (el inverso, para enlazar desde su ficha)."""
    out: dict[str, dict[str, dict]] = {}
    for w in (e for e in ents.values() if e["type"] == "work" and not e["provisional"]):
        bp = best_premiere(con, w["id"])
        iv = edtf_interval(bp[0]) if bp else None
        year = iv[0] if iv else None
        for r in con.execute("SELECT property,value_json FROM claims WHERE subject_id=? AND property IN ('composer','librettist') AND value_type='entity' AND status!='deprecated'", (w["id"],)):
            v = json.loads(r["value_json"])
            if v in ents and not ents[v]["provisional"] and ents[v]["type"] == "person":
                it = out.setdefault(v, {}).setdefault(w["id"], {"id": w["id"], "label": w["label"], "year": year, "roles": []})
                if r["property"] not in it["roles"]:
                    it["roles"].append(r["property"])
    return {pid: sorted(ws.values(), key=lambda x: (x["year"] or 9999, x["label"])) for pid, ws in out.items()}


def videos_view(con, out: Path, ents) -> None:
    """Índice de fichas con vídeos enlazados desde su artículo de Wikipedia (views/videos.json), para descubrirlos."""
    route = {"work": "obra", "person": "persona", "venue": "teatro"}
    rows = []
    for eid, e in ents.items():
        if e["provisional"] or e["type"] not in route:
            continue
        vs = videos_of(con, eid)
        if vs:
            rows.append({"id": eid, "label": e["label"], "type": e["type"], "route": route[e["type"]], "article_url": vs[0]["article_url"], "videos": vs})
    rows.sort(key=lambda r: r["label"].casefold())
    _w(out / "views" / "videos.json", rows)


def videos_of(con, eid: str) -> list[dict]:
    """Vídeos de YouTube enlazados desde el artículo de Wikipedia de la ficha (solo identificador y artículo de origen; no se afirma que sea la obra)."""
    from .store import document_text
    out, seen = [], set()
    for d in con.execute("SELECT d.* FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es_enlaces' ORDER BY d.rowid DESC LIMIT 1", (eid,)):
        for line in document_text(con, d["id"]).splitlines()[1:]:
            head, _, rest = line.partition("\t")
            vid = head.removeprefix("youtube ").strip()
            estado, _, rest = rest.partition("\t")
            title, _, channel = rest.partition("\t")
            if vid and vid not in seen and estado != "no_disponible":      # los vídeos que ya no existen o son privados no se publican
                seen.add(vid)
                out.append({"id": vid, "article_url": d["url"], "title": title or None, "channel": channel or None, "embeddable": estado != "sin_incrustar"})
    return out


def rism_of(con, eid: str) -> list[dict]:
    """Fuentes musicales de la persona en RISM: solo cita corta y enlace (licencia CC BY-NC-SA: consulta, no reproducción)."""
    from .store import document_text
    res = []
    for d in con.execute("SELECT d.* FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='rism' ORDER BY d.rowid", (eid,)):
        first = document_text(con, d["id"]).splitlines()[0]
        label = re.sub(r"^RISM\s+\S+\s+", "", first)
        res.append({"label": label[:70], "url": d["url"]})
    return res


def scores_of(con, eid: str) -> list[dict]:
    """Partituras de la obra en IMSLP (metadatos y enlaces; los PDF NO se almacenan). Cada fichero con su estado de copyright."""
    import gzip
    from urllib.parse import quote, urlsplit
    from .sources.imslp import parse_work
    out = []
    for d in con.execute("SELECT d.* FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='imslp' ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (eid,)):
        try:
            parsed = parse_work(gzip.decompress(Path(d["raw_path"]).read_bytes()).decode())
        except Exception:    # noqa: BLE001  (crudo ilegible: sin partituras, no se rompe la exportación)
            continue
        def clean(t: str) -> str:   # restos de formato del wiki (<br>, [[enlaces]], comas iniciales)
            t = re.sub(r"<br\s*/?>", " · ", t or "")
            t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
            t = re.sub(r"<[^>]+>", "", t)
            return re.sub(r"\s+", " ", t).strip(" ,;·-")
        def describe(f: dict) -> tuple[str, str, tuple]:
            """Etiqueta legible, edición y clave de orden. IMSLP rotula casi todo «Complete Score»: se deduce el número del nombre del fichero
            (PMLP123-Autor_I-Obra_15D.pdf -> «Núm. 15 D») y la edición del autor del nombre más el editor/año."""
            m = re.match(r"PMLP\d+-(?P<ed>[^-]+)-(?P<rest>.+?)\.pdf$", f["name"], re.I)
            # nombres con guiones como separador de palabras (PMLP1-La-verbena-de-la-Paloma-…): el primer tramo es parte del título, no una edición
            hyphen_title = bool(m) and "_" not in f["name"] and f["name"].count("-") >= 4
            ed = ("" if hyphen_title else (m.group("ed").replace("_", " ").strip() if m else "")) or "—"
            num = re.search(r"_(\d+)\s*([A-Za-z]{0,3})?(?:\+(\d+)\s*([A-Za-z]{0,4})?)?_?$", m.group("rest")) if m else None
            n = int(num.group(1)) if num else 10**6
            tail = " ".join(x for x in (num.group(2), ("+ " + num.group(3) + (" " + num.group(4) if num.group(4) else "")) if num.group(3) else "") if x).strip() if num else ""
            generic = (clean(f.get("desc")) or "").lower() in ("", "complete score", "score")
            label = (f"Núm. {num.group(1)}{(' ' + tail) if tail else ''}" if num and generic else clean(f.get("desc")) or f["name"])
            return label, ed, (n, tail)
        pub = lambda f: clean(f.get("publisher"))
        files = []
        for f in sorted(parsed["files"], key=lambda f: (describe(f)[1], describe(f)[2], f["name"])):
            if not f.get("name"):
                continue
            label, ed, _k = describe(f)
            files.append({"name": f["name"], "desc": label, "edition": f"{ed} · {pub(f)}" if pub(f) else ed, "copyright": f.get("copyright") or "", "publisher": "",
                          "editor": clean(f.get("editor")), "image_type": f.get("image_type") or "",
                          "url": "https://imslp.org/wiki/File:" + quote(f["name"].replace(" ", "_"), safe="(),_.-")})
        if files:
            page = d["url"].split("?")[0]
            out.append({"source": "IMSLP", "page_url": page, "license": "CC-BY-SA-4.0 (texto de la wiki); cada fichero declara su propio copyright", "files": files,
                        "retrieved_at": d["retrieved_at"]})
    return out


def summary_of(con, eid: str, pat=None, names=None) -> dict | None:
    """Resumen narrativo validado (kind=summary): cada frase con las evidencias de lo que cita."""
    r = con.execute("SELECT content_path, created_at, validation_report_json FROM outputs WHERE id=?", (f"summary:{eid}",)).fetchone()
    if not r:
        return None
    c = json.loads(Path(r["content_path"]).read_text())
    dep = {x[0] for x in con.execute("SELECT id FROM claims WHERE status='deprecated'")}
    c["segments"] = [s for s in c["segments"] if not any(i in dep for i in s.get("claim_ids", []))]   # una frase que cita un dato retirado no se publica
    segs = [{"text": s["text"], "parts": _segments(s["text"], eid, pat, names or {}),   # enlaces a otras fichas y años resaltados, como en el texto de Wikipedia
             "evidence_ids": _passages_of(con, s.get("claim_ids", []), s.get("narrative_ids", []), s.get("passage_ids", []))} for s in c["segments"]]
    segs = [s for s in segs if s["evidence_ids"]]
    import yaml
    verified = bool(json.loads(r["validation_report_json"] or "{}").get("haiku_checked"))   # revisado por un segundo modelo (Haiku)
    if not verified and not yaml.safe_load((ROOT / "config" / "publish.yaml").read_text()).get("publish_unverified_summaries", False):
        return None                                                                          # sin revisar: no se publica (config/publish.yaml)
    return {"segments": segs, "model": c.get("model"), "generated_at": r["created_at"], "verified": verified} if len(segs) >= 2 else None


def biography(con, eid: str, pat, names, max_chars: int = 2400) -> dict | None:
    """Primeros párrafos del artículo de Wikipedia ES de la entidad (texto reutilizable CC BY-SA), con enlaces y años resaltados."""
    d = con.execute("SELECT d.* FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE ds.entity_id=? AND d.source='wikipedia_es' AND d.reuse='reusable' "
                    "ORDER BY d.retrieved_at DESC, d.rowid DESC LIMIT 1", (eid,)).fetchone()
    if not d:
        return None
    paras, total = [], 0
    for p in con.execute("SELECT id,text FROM passages WHERE document_id=? ORDER BY char_start", (d["id"],)):
        t = re.sub(r"\s+", " ", re.sub("[\u200b\u200c\u2060\ufeff]", "", p["text"])).replace("( ", "(").replace(" )", ")").replace(" ,", ",").strip()
        if len(t) < 110 or _NOT_PROSE.search(t):       # titulares, listas y referencias no son prosa
            continue
        paras.append({"passage_id": p["id"], "segments": _segments(t, eid, pat, names)})
        total += len(t)
        if total >= max_chars:
            break
    if not paras:
        return None
    title_url = d["url"]
    return {"paragraphs": paras, "document_id": d["id"], "license": d["license"], "revision": d["stable_id"], "url": title_url,
            "history_url": re.sub(r"&oldid=\d+", "&action=history", title_url), "retrieved_at": d["retrieved_at"]}


def mark_duplicates(con) -> dict[str, str]:
    """Excluye del corpus las obras duplicadas (dos identificadores para la misma obra) y registra el motivo. Se queda la ficha con
    más datos. Idempotente; `discover` no las vuelve a incluir."""
    ents = {e["id"]: e for e in con.execute("SELECT * FROM entities WHERE type='work' AND provisional=0")}
    dupes = possible_duplicates(con, ents)
    for dup, main in dupes.items():
        con.execute("INSERT OR REPLACE INTO corpus VALUES(?,0,?)", (dup, f"duplicado de {main}: mismo título base, compositores y año de estreno"))
    con.commit()
    return dupes


def best_premiere(con, work_id: str) -> tuple[str, str] | None:
    """Una fecha de estreno por obra: la estructurada primero; si no, la de mayor confianza. -> (edtf, claim_id)."""
    r = con.execute("SELECT id,value_json FROM claims WHERE subject_id=? AND property='premiere_date' AND value_type='edtf' AND status!='deprecated' "
                    "ORDER BY (source_kind='structured') DESC, (CASE WHEN source_kind='structured' THEN confidence END) DESC, substr(value_json,2,4) ASC, length(value_json) DESC, id LIMIT 1", (work_id,)).fetchone()
    return (json.loads(r["value_json"]), r["id"]) if r else None


def home_view(con, out: Path, ents) -> None:
    """Portada: títulos destacados (los mejor documentados, con representación de las épocas antiguas), en orden cronológico."""
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "home.yaml").read_text())
    scope = yaml.safe_load((ROOT / "config" / "scope.yaml").read_text())["years"]
    works = []
    for e in ents.values():
        if e["type"] != "work" or e["provisional"]:
            continue
        bp = best_premiere(con, e["id"])
        y = edtf_interval(bp[0]) if bp else None
        if not y or not (scope["from"] <= y[0] <= scope["to"]):    # fuera del alcance (config/scope.yaml): no se destaca
            continue
        weight = con.execute("SELECT COUNT(*) FROM claims WHERE subject_id=? AND status!='deprecated' AND property!='genre_concept'", (e["id"],)).fetchone()[0] \
            + con.execute("SELECT COUNT(*) FROM narrative_entities WHERE entity_id=?", (e["id"],)).fetchone()[0]
        comps = []
        for r in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='composer' AND value_type='entity' AND status!='deprecated' ORDER BY (source_kind='structured') DESC", (e["id"],)):
            v = json.loads(r[0])
            if v in ents and not ents[v]["provisional"] and ents[v]["label"] not in comps:
                comps.append(ents[v]["label"])
        works.append({"id": e["id"], "label": e["label"], "year": y[0], "composers": comps[:2], "weight": weight})
    eras = []
    for era in cfg["eras"]:
        inside = [w for w in works if era["from"] <= w["year"] <= era["to"]]
        top = sorted(inside, key=lambda w: (-w["weight"], w["year"], w["label"]))[: cfg.get("per_era", 10)]
        eras.append({**era, "total": len(inside), "works": sorted(top, key=lambda w: (w["year"], w["label"]))})
    _w(out / "views" / "home.json", {"eras": eras, "total": len(works), "source": cfg.get("source"),
                                      "criterion": "en cada época, las obras con más datos documentados, en orden cronológico"})


def collaborations_view(con, out: Path, ents) -> None:
    """Red de colaboraciones entre personas: compositor ↔ libretista por cada obra que firman juntos (aristas con sus obras)."""
    people: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    for w in (e for e in ents.values() if e["type"] == "work" and not e["provisional"]):
        by_role: dict[str, list[str]] = {"composer": [], "librettist": []}
        for r in con.execute("SELECT property,value_json FROM claims WHERE subject_id=? AND property IN ('composer','librettist') AND value_type='entity' AND status!='deprecated'", (w["id"],)):
            v = json.loads(r["value_json"])
            if v in ents and not ents[v]["provisional"] and ents[v]["type"] == "person" and v not in by_role[r["property"]]:
                by_role[r["property"]].append(v)
        bp = best_premiere(con, w["id"])
        year = edtf_interval(bp[0])[0] if bp and edtf_interval(bp[0]) else None
        for role, ids in by_role.items():
            for i in ids:
                p = people.setdefault(i, {"id": i, "label": ents[i]["label"], "works": 0, "roles": set()})
                p["works"] += 1
                p["roles"].add(role)
        for c in by_role["composer"]:
            for l in by_role["librettist"]:
                if c != l:
                    ed = edges.setdefault((c, l), {"source": c, "target": l, "works": []})
                    ed["works"].append({"id": w["id"], "label": w["label"], "year": year})
    deg: dict[str, int] = {}
    for (a, b) in edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    nodes = [{**p, "roles": sorted(p["roles"]), "degree": deg.get(p["id"], 0)} for p in people.values() if p["id"] in deg]
    _w(out / "views" / "collaborations.json", {"nodes": sorted(nodes, key=lambda n: (-n["degree"], -n["works"], n["label"])),
                                                "edges": [{**e, "weight": len(e["works"])} for e in edges.values()]})


def views(con, out: Path, ents) -> None:
    # cronograma: UNA fecha principal por (entidad, tipo), la misma que muestra la ficha; las demás son versiones alternativas
    route = {"work": "obra", "person": "persona", "venue": "teatro"}
    by: dict[tuple, list] = {}
    for c in con.execute("SELECT * FROM claims WHERE value_type='edtf' AND status!='deprecated' AND property IN ('premiere_date','birth_date','death_date','opening_date','closing_date','revival_date')"):
        e = ents.get(c["subject_id"])
        if e and not e["provisional"] and e["type"] in route:
            by.setdefault((c["subject_id"], c["property"]), []).append(c)
    tl = []
    for (eid, prop), cs in by.items():
        groups: dict[str, list] = {}
        for c in cs:
            groups.setdefault(json.loads(c["value_json"]), []).append(c)
        vals = [v for v in groups if edtf_interval(v)]
        # una fecha menos precisa que otra que la contiene se pliega en ella (1923 en 1923-12-02)
        vals = [v for v in vals if not any(o != v and len(o) > len(v) and _contains(edtf_interval(v), edtf_interval(o)) for o in vals)]
        if not vals:
            continue
        def rank(v):
            g = groups[v]
            pref = any(json.loads(x["qualifiers_json"] or "{}").get("wikidata_rank") == "preferred" for x in g)
            return (pref, len(g), any(x["source_kind"] == "structured" for x in g), max(x["confidence"] for x in g), len(v))
        best = max(vals, key=rank)
        iv = edtf_interval(best)
        cl = groups[best][0]
        tl.append({"claim_id": cl["id"], "entity_id": eid, "label": ents[eid]["label"], "route": route[ents[eid]["type"]], "kind": prop, "edtf": best,
                   "from": iv[0], "to": iv[1], "status": "disputed" if len(vals) > 1 else cl["status"], "alternatives": sorted(v for v in vals if v != best),
                   "evidence": [r[0] for r in con.execute("SELECT passage_id FROM claim_evidence WHERE claim_id=?", (cl["id"],))]})
    _w(out / "views" / "timeline.json", sorted(tl, key=lambda r: (r["from"], r["label"])))
    # red
    nodes, edges = {}, []
    for c in con.execute("SELECT * FROM claims WHERE property IN ('composer','librettist','premiere_venue','company') AND value_type='entity' AND status!='deprecated'"):
        v = json.loads(c["value_json"])
        if c["subject_id"] in ents and v in ents:
            for i in (c["subject_id"], v):
                nodes[i] = {"id": i, "label": ents[i]["label"], "type": ents[i]["type"]}
            edges.append({"source": v, "target": c["subject_id"], "kind": c["property"], "claim_id": c["id"]})
    _w(out / "views" / "network.json", {"nodes": list(nodes.values()), "edges": edges})
    # mapa: teatros con coordenadas (P625 de Wikidata), su ciudad (P131) y los estrenos que acogieron
    venues = []
    for v in (e for e in ents.values() if e["type"] == "venue" and not e["provisional"]):
        co = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='coordinates' AND status!='deprecated' LIMIT 1", (v["id"],)).fetchone()
        if not co:
            continue
        lat, lon = (float(x) for x in json.loads(co[0]).split(","))
        city = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property IN ('city','location') AND value_type='entity' AND status!='deprecated' "
                           "ORDER BY (property='city') DESC LIMIT 1", (v["id"],)).fetchone()   # la ciudad (municipio) manda sobre el barrio
        cid = json.loads(city[0]) if city else None
        prem = []
        for c in con.execute("SELECT subject_id FROM claims WHERE property='premiere_venue' AND value_json=? AND status!='deprecated'", (json.dumps(v["id"]),)):
            w = ents.get(c[0])
            if not w or w["type"] != "work" or w["provisional"]:
                continue
            bp = best_premiere(con, w["id"])
            prem.append({"id": w["id"], "label": w["label"], "year": int(bp[0][:4]) if bp else None})
        prem = sorted({p["id"]: p for p in prem}.values(), key=lambda p: (p["year"] or 9999, p["label"]))
        opening = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='opening_date' AND status!='deprecated' LIMIT 1", (v["id"],)).fetchone()
        closing = con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property='closing_date' AND status!='deprecated' LIMIT 1", (v["id"],)).fetchone()
        venues.append({"id": v["id"], "label": v["label"], "lat": lat, "lon": lon, "city": ents[cid]["label"] if cid in ents else None,
                       "opening": json.loads(opening[0]) if opening else None, "closing": json.loads(closing[0]) if closing else None, "premieres": prem})
    _w(out / "views" / "map.json", sorted(venues, key=lambda v: (-len(v["premieres"]), v["label"])))
    # estadísticas con la consulta publicada
    q1 = "SELECT substr(value_json,2,4) AS y, COUNT(*) FROM claims WHERE property='premiere_date' AND value_type='edtf' AND status!='deprecated' GROUP BY y"
    per_year = [{"year": int(r[0]), "n": r[1]} for r in con.execute(q1) if r[0].isdigit()]
    q2 = "SELECT value_json, COUNT(*) FROM claims WHERE property='composer' AND value_type='entity' AND status!='deprecated' GROUP BY value_json ORDER BY 2 DESC LIMIT 20"
    top = [{"entity_id": json.loads(r[0]), "label": ents[json.loads(r[0])]["label"] if json.loads(r[0]) in ents else json.loads(r[0]), "n": r[1]} for r in con.execute(q2)]
    n_works = con.execute("SELECT COUNT(*) FROM entities WHERE type='work' AND provisional=0").fetchone()[0]
    n_dated = con.execute("SELECT COUNT(DISTINCT subject_id) FROM claims WHERE property='premiere_date'").fetchone()[0]
    _w(out / "views" / "stats.json", {
        "premieres_per_year": {"query": q1, "rows": per_year}, "top_composers": {"query": q2, "rows": top},
        "coverage": {"works": n_works, "works_with_premiere_date": n_dated}})
