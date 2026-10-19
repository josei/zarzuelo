"""Enlace conservador de registros externos a entidades del corpus (obras, personas, teatros) por título/compositor.

Reglas (precisión sobre cobertura):
- Un título coincide solo por igualdad de la forma normalizada (sin acentos, paréntesis, artículo inicial ni puntuación).
- Si la obra del corpus tiene compositor conocido, el registro debe nombrarlo (apellido y nombre) para enlazar;
  si el corpus no lo tiene o el registro no lo da, solo se enlaza un título de >= 3 palabras y único en el corpus.
- Una obra con título ambiguo (varias entidades) sin compositor coincidente no se enlaza.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..util import norm

_ART = {"el", "la", "los", "las", "un", "una", "l", "les", "lo", "de", "del", "d"}


def norm_title(s: str) -> str:
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = norm(s).replace("'", " ").replace("’", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    toks = s.split()
    while toks and toks[0] in _ART and len(toks) > 1:
        toks.pop(0)
    return " ".join(toks)


def name_tokens(s: str) -> set[str]:
    """Tokens de un nombre de persona ('Apellido, Nombre' o 'Nombre Apellido'), sin partículas ni paréntesis."""
    s = re.sub(r"\([^)]*\)", " ", s)
    s = norm(s)
    s = re.sub(r"[^\w\s]", " ", s)
    return {t for t in s.split() if t not in _ART and len(t) > 1}


def title_candidates(raw: str) -> list[str]:
    """Formas candidatas del título en un literal con subtítulo: completo y recortado por ',', ' - ', ':' o '/'."""
    prefix = raw.split("(")[0] if "(" in raw else ""
    raw = re.sub(r"\((?:zarzuela|opera|ópera|play|operetta|sarsuela)[^)]*\)", " ", raw, flags=re.I)
    outs = [raw]
    if prefix.strip():
        outs.append(prefix)
    for sep in (", ", " - ", " – ", ": ", " / ", " | "):
        if sep in raw:
            outs.append(raw.split(sep)[0])
    seen, res = set(), []
    for o in outs:
        n = norm_title(o)
        if n and n not in seen:
            seen.add(n)
            res.append(n)
    return res


class CorpusIndex:
    def __init__(self, con: sqlite3.Connection):
        self.works: dict[str, dict] = {}      # id -> {titles:set, composers:[token sets]}
        self.by_title: dict[str, list[str]] = {}
        self.persons: dict[str, list[set[str]]] = {}   # id -> variantes de tokens
        self.venues: dict[str, str] = {}      # titulo normalizado -> id
        self.labels: dict[str, str] = {}
        for e in con.execute("SELECT * FROM entities WHERE provisional=0 AND type IN ('work','person','venue')"):
            alts = json.loads(e["labels_alt"] or "[]")
            names = [e["label"], *alts]
            self.labels[e["id"]] = e["label"]
            if e["type"] == "work":
                titles = {n for x in names for n in title_candidates(x)[:1]}
                titles |= {norm_title(x) for x in names if norm_title(x)}
                comps = []
                for r in con.execute("SELECT p.label, p.labels_alt FROM claims c JOIN entities p ON p.id=json_extract(c.value_json,'$') "
                                     "WHERE c.subject_id=? AND c.property='composer'", (e["id"],)):
                    for lab in [r["label"], *json.loads(r["labels_alt"] or "[]")]:
                        t = name_tokens(lab)
                        if len(t) >= 2:
                            comps.append(t)
                self.works[e["id"]] = {"titles": titles, "composers": comps}
                for t in titles:
                    self.by_title.setdefault(t, []).append(e["id"])
            elif e["type"] == "person":
                self.persons[e["id"]] = [t for t in (name_tokens(x) for x in names) if len(t) >= 2]
            else:
                for x in names:
                    n = norm_title(x)
                    if n:
                        self.venues.setdefault(n, e["id"])

    # --- personas
    def person_ids(self, name: str) -> list[str]:
        """Personas del corpus cuyos tokens contienen todos los del nombre dado (>= 2 tokens)."""
        t = name_tokens(name)
        if len(t) < 2:
            return []
        return [pid for pid, variants in self.persons.items() if any(t <= v or v <= t for v in variants)]

    # --- obras
    def composer_hit(self, wid: str, composer_text: str | None) -> bool | None:
        """True/False si el texto nombra/no nombra a un compositor de la obra; None si no hay datos para decidir."""
        comps = self.works[wid]["composers"]
        if not comps or not composer_text:
            return None
        toks = name_tokens(composer_text)
        return any(c <= toks or (len(toks) >= 2 and toks <= c) for c in comps)

    def match_work(self, title: str, composer_text: str | None = None) -> str | None:
        ids: list[str] = []
        for cand in title_candidates(title):
            for wid in self.by_title.get(cand, []):
                if wid not in ids:
                    ids.append(wid)
            if ids:
                n_tokens = len(cand.split())
                break
        else:
            return None
        hits = [w for w in ids if self.composer_hit(w, composer_text) is True]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
        undecided = [w for w in ids if self.composer_hit(w, composer_text) is None]
        if len(ids) == 1 and undecided and n_tokens >= 3:
            return undecided[0]
        return None

    def match_entity_name(self, name: str) -> str | None:
        """Categoría/etiqueta que coincide exactamente con un teatro, una obra de título >= 2 palabras o una persona única."""
        n = norm_title(name)
        if n in self.venues:
            return self.venues[n]
        if len(n.split()) >= 2 and len(self.by_title.get(n, [])) == 1:
            return self.by_title[n][0]
        ids = self.person_ids(name)
        return ids[0] if len(ids) == 1 and len(name_tokens(name)) >= 2 else None


def edtf_from_slash(s: str) -> str | None:
    """'1897/06/23' | '1897/06' | '1897' -> EDTF; otro formato -> None (no se adivina)."""
    m = re.fullmatch(r"\s*(\d{4})(?:[/-](\d{1,2})(?:[/-](\d{1,2}))?)?\s*", s or "")
    if not m:
        return None
    y, mo, d = m.groups()
    if mo and not d and "-" in s:  # 'AAAA-NN' es más probablemente un rango de años que año-mes
        return None
    if mo and not 1 <= int(mo) <= 12:
        return None
    if d and not 1 <= int(d) <= 31:
        return None
    return y + (f"-{int(mo):02d}" + (f"-{int(d):02d}" if d else "") if mo else "")


def add_claim(con, subject: str, prop: str, value, vtype: str, passage: sqlite3.Row, line: str,
              method: str, confidence: float = 0.8) -> str:
    """Afirmación estructurada con evidencia (patrón de wikidata.py). value es el valor (id de entidad, EDTF, literal)."""
    from ..db import jdump
    from ..util import now, sha256
    cid = "C" + sha256(f"{subject}|{prop}|{value}")[:16]
    con.execute(
        "INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind,extracted_by_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (cid, subject, prop, jdump(value), vtype, "{}", "asserted", confidence, "structured",
         jdump({"method": method, "version": "1", "date": now()}), now()))
    con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, passage["id"], line, 0, len(line)))
    return cid


def robots_info(http, base: str, probe_path: str) -> dict:
    """Lee robots.txt de la fuente: ¿se permite `probe_path` a nuestro agente? y Crawl-delay declarado."""
    from urllib.robotparser import RobotFileParser
    from ..http import UA
    try:
        txt = http.get(base.rstrip("/") + "/robots.txt", use_cache=False)
    except Exception as e:  # pragma: no cover - red
        return {"error": str(e)}
    rp = RobotFileParser()
    rp.parse(txt.splitlines())
    return {"url": base.rstrip("/") + "/robots.txt", "allowed": rp.can_fetch(UA, base.rstrip("/") + probe_path),
            "probe": probe_path, "crawl_delay": rp.crawl_delay(UA)}
