"""Vocabulario de géneros (§2.3, capa V). Cada concepto lleva su fuente (AGENTS.md)."""
from __future__ import annotations

import yaml

from .db import ROOT, jdump
from .http import Http

VOCAB_PATH = ROOT / "vocab" / "genres.yaml"


def load_into_db(con, path=VOCAB_PATH) -> int:
    data = yaml.safe_load(path.read_text()) or {}
    n = 0
    for c in data.get("concepts", []):
        if not c.get("scope_source"):
            raise ValueError(f"concepto sin fuente: {c.get('id')}")
        con.execute("INSERT OR REPLACE INTO concepts VALUES(?,?,?,?,?,?,?,?,?)",
                    (c["id"], c.get("scheme", "genres"), c["pref_label"], jdump(c.get("alt_labels", [])),
                     jdump(c.get("hidden_labels", [])), c.get("scope_note"), c["scope_source"],
                     jdump(c.get("broader", [])), jdump(c.get("related", []))))
        n += 1
    con.commit()
    return n


def build_from_wikidata(http: Http, roots: dict[str, str], path=VOCAB_PATH) -> int:
    """Amplía genres.yaml con los subgéneros (P279*) de las raíces, con etiquetas y alias de Wikidata como fuente."""
    existing = (yaml.safe_load(path.read_text()) or {}).get("concepts", []) if path.exists() else []
    by_id = {c["id"]: c for c in existing}
    for root in roots.values():
        q = f"""SELECT ?g ?parent WHERE {{ ?g wdt:P279* wd:{root} . OPTIONAL {{ ?g wdt:P279 ?parent . ?parent wdt:P279* wd:{root} }} }}"""
        r = http.json(http.cfg["sparql"], {"query": q, "format": "json"}, {"Accept": "application/sparql-results+json"})
        parents: dict[str, set] = {}
        for b in r["results"]["bindings"]:
            g = b["g"]["value"].rsplit("/", 1)[1]
            parents.setdefault(g, set())
            if "parent" in b:
                parents[g].add(b["parent"]["value"].rsplit("/", 1)[1])
        ids = sorted(set(parents) | set(roots.values()))
        for i in range(0, len(ids), 40):
            ents = http.json(http.cfg["endpoint"], {"action": "wbgetentities", "ids": "|".join(ids[i:i + 40]), "props": "labels|aliases|descriptions",
                                                   "languages": "es|en", "format": "json"})["entities"]
            for qid, e in ents.items():
                lab = (e.get("labels", {}).get("es") or e.get("labels", {}).get("en") or {}).get("value")
                if not lab or qid in by_id:
                    continue
                alts = sorted({a["value"] for l in ("es", "en") for a in e.get("aliases", {}).get(l, [])} - {lab})
                desc = (e.get("descriptions", {}).get("es") or e.get("descriptions", {}).get("en") or {}).get("value")
                by_id[qid] = {"id": qid, "pref_label": lab, "alt_labels": alts, "scope_note": desc,
                              "scope_source": f"https://www.wikidata.org/wiki/{qid}",
                              "broader": sorted(p for p in parents.get(qid, ()) if p != qid)}
    path.write_text("# Generado por `kb vocab build` desde Wikidata (CC0); cada concepto cita su fuente.\n"
                    "# Se puede contrastar y ampliar a mano con el Diccionario de la Zarzuela (solo consulta).\n"
                    + yaml.safe_dump({"concepts": sorted(by_id.values(), key=lambda c: c["id"])}, allow_unicode=True, sort_keys=False))
    return len(by_id)


# --- construcción desde Wikipedia (artículos y pasajes archivados) -------------------------------------------------------------
_AGENT = __import__("re").compile(r"(ero|era|eros|eras|ista|istas|ear|esco|ático|ente|mente|ado|ados)$", __import__("re").I)


def _lead(con, doc_id: str) -> str | None:
    for p in con.execute("SELECT text FROM passages WHERE document_id=? ORDER BY char_start", (doc_id,)):
        t = " ".join(p["text"].split())
        if len(t) >= 110 and t[0].isupper() and not t.startswith(("Para otros", "Desambiguación")):
            return t
    return None


def build_from_seed(con, http, seed_path=None, path=VOCAB_PATH) -> dict:
    """Amplía vocab/genres.yaml con los conceptos de config/vocab_seed.yaml, cada uno con su fuente archivada. No pisa los existentes."""
    import re
    from .sources import mediawiki
    seed = yaml.safe_load((seed_path or (ROOT / "config" / "vocab_seed.yaml")).read_text())
    existing = (yaml.safe_load(path.read_text()) or {}).get("concepts", []) if path.exists() else []
    by_id = {c["id"]: c for c in existing}
    stats = {"articles": 0, "terms": 0, "missing": []}
    title_to_id: dict[str, str] = {}
    for a in seed.get("articles", []):
        r = http.json(http.cfg["endpoint"], {"action": "query", "titles": a["title"], "prop": "pageprops|redirects", "ppprop": "wikibase_item", "rdlimit": 100,
                                             "redirects": 1, "format": "json", "formatversion": 2})
        pg = r["query"]["pages"][0]
        if pg.get("missing") or not pg.get("pageprops", {}).get("wikibase_item"):
            stats["missing"].append(a["title"])
            continue
        qid, canon = pg["pageprops"]["wikibase_item"], pg["title"]
        title_to_id[a["title"]] = qid
        title_to_id[canon] = qid
        doc_id = mediawiki.ingest_page(con, http, "wikipedia_es", canon)
        lead = _lead(con, doc_id) if doc_id else None
        url = con.execute("SELECT url FROM documents WHERE id=?", (doc_id,)).fetchone()["url"] if doc_id else None
        label = re.sub(r"\s*\(.*?\)\s*$", "", canon)
        alts, seen = [], {label.lower()}
        for x in pg.get("redirects", []):
            t = re.sub(r"\s*\(.*?\)\s*$", "", x["title"]).strip()
            if t.lower() in seen or _AGENT.search(t) or len(t.split()) > 4:
                continue
            seen.add(t.lower())
            alts.append(t)
        if qid in by_id:   # ya existía (p. ej. desde Wikidata): se completan sinónimos y nota, sin pisar
            ex = by_id[qid]
            ex["alt_labels"] = sorted(set(ex.get("alt_labels", [])) | set(alts))
            ex["scope_note"], ex["scope_source"] = (lead, url) if lead else (None, ex.get("scope_source"))
            continue
        by_id[qid] = {"id": qid, "pref_label": label, "alt_labels": alts, "scope_note": lead, "scope_source": url, "broader": []}
        stats["articles"] += 1
    for t in seed.get("terms", []):
        found = None
        for title in t["in"]:
            row = con.execute("SELECT p.text,d.url FROM passages p JOIN documents d ON d.id=p.document_id WHERE d.source='wikipedia_es' AND d.url LIKE ? AND p.text LIKE ? "
                              "ORDER BY (length(p.text) < 100 OR length(p.text) > 450), length(p.text) LIMIT 1", (f"%title={title.replace(' ', '_')}&%", f"%{t['find']}%")).fetchone()
            if row:
                found = row
                break
        if not found:
            stats["missing"].append(t["label"])
            continue
        from .util import norm as _norm
        same = next((c for c in by_id.values() if _norm(c["pref_label"]) == _norm(t["label"])), None)
        if same:                      # concepto ya presente (p. ej. de Wikidata) con solo una descripción: se completa con el pasaje
            if (same.get("scope_source") or "").startswith("https://www.wikidata.org"):
                same["scope_note"], same["scope_source"] = " ".join(found["text"].split()), found["url"]
            stats["terms"] += 1
            continue
        cid = "kb:genre:" + re.sub(r"\W+", "-", t["label"]).strip("-")
        broader = [title_to_id[t["broader"]]] if t.get("broader") in title_to_id else []
        by_id[cid] = {"id": cid, "pref_label": t["label"], "alt_labels": t.get("alt", []), "scope_note": " ".join(found["text"].split()),
                      "scope_source": found["url"], "broader": broader}
        stats["terms"] += 1
    path.write_text("# Generado por `kb vocab build` (Wikidata) y `kb vocab seed` (Wikipedia); cada concepto cita su fuente.\n"
                    + yaml.safe_dump({"concepts": sorted(by_id.values(), key=lambda c: c["pref_label"].lower())}, allow_unicode=True, sort_keys=False))
    stats["concepts"] = len(by_id)
    return stats
