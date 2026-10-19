"""Utilidades comunes de los conectores de catálogos (IAI, BVMC, MAE, RISM, DAHR): robots.txt, config, registros
renderizados como texto canónico (una línea = un pasaje citable) y enlace conservador a entidades del corpus."""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urlsplit

from ..db import jdump
from ..http import Http, load_sources
from ..models import Reuse
from ..store import capture, segment
from ..util import norm, now, sha256

AGENT = "zarzuelo-kb"
OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}


def cfg_for(name: str) -> dict:
    """Configuración de la fuente (también si aún no está validada: la fase 0 la necesita)."""
    return dict(load_sources().get(name) or {"rate_limit_per_s": 0.2})


def make_http(name: str, http: Http | None = None) -> Http:
    return http or Http(name, cfg=cfg_for(name))


def parse_robots(text: str) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return rp


def robots_report(http: Http, base: str, probe_paths: list[str]) -> dict:
    """Descarga robots.txt de `base` y dice qué rutas de `probe_paths` se pueden pedir."""
    url = base.rstrip("/") + "/robots.txt"
    try:
        txt = http.get(url)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)[:200], "allowed": {}}
    rp = parse_robots(txt)
    return {"url": url, "crawl_delay": rp.crawl_delay(AGENT),
            "allowed": {p: rp.can_fetch(AGENT, base.rstrip("/") + p) for p in probe_paths},
            "excerpt": txt[:400]}


def robots_allows(http: Http, base: str, path: str) -> bool:
    try:
        rp = parse_robots(http.get(base.rstrip("/") + "/robots.txt"))
    except Exception:  # noqa: BLE001
        return False  # sin robots legible no se rastrea
    return rp.can_fetch(AGENT, base.rstrip("/") + path)


def tnorm(s: str) -> str:
    """Título normalizado para comparar: sin acentos, puntuación ni arroba de ordenación (@)."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", norm(s)).split())


def surname(name: str) -> str:
    """'Apellido, Nombre' -> 'apellido' normalizado; sin coma, última palabra."""
    base = name.split(",")[0] if "," in name else (name.split() or [""])[-1]
    return tnorm(base)


def _people_labels(con: sqlite3.Connection, work_id: str) -> list[str]:
    out = []
    for r in con.execute("SELECT value_json FROM claims WHERE subject_id=? AND property IN ('composer','librettist') AND status!='deprecated'", (work_id,)):
        pid = json.loads(r["value_json"])
        e = con.execute("SELECT label, labels_alt FROM entities WHERE id=?", (pid,)).fetchone()
        if e:
            out.append(tnorm(e["label"]))
            out += [tnorm(a) for a in json.loads(e["labels_alt"] or "[]")]
    return out


def link_work(con: sqlite3.Connection, title: str, creators: list[str] | None = None, require_creator: bool = True) -> str | None:
    """Obra del corpus cuyo título normalizado coincide EXACTAMENTE y es único; si hay creadores en el registro
    (y la obra tiene autores conocidos) al menos un apellido debe coincidir. Sin certeza -> None."""
    t = tnorm(re.sub(r"\s*[/:].*$", "", title)) if title else ""
    if len(t) < 4:
        return None
    cands = []
    for r in con.execute("SELECT id,label,labels_alt FROM entities WHERE type='work'"):
        names = [r["label"]] + json.loads(r["labels_alt"] or "[]")
        if any(tnorm(n) == t for n in names):
            cands.append(r["id"])
    if len(cands) != 1:
        return None
    wid = cands[0]
    people = _people_labels(con, wid)
    if creators:
        sn = {surname(c) for c in creators if c}
        if people and not any(s and s in p.split() for s in sn for p in people):
            return None
        if not people and require_creator:
            return None
    elif require_creator:
        return None
    return wid


def corpus_external_ids(con: sqlite3.Connection, prop: str, key: str, types=("person",), limit: int | None = None,
                        wd_http: Http | None = None) -> dict[str, str]:
    """{QID: id externo} para entidades del corpus: primero de external_ids[key]; si no, vía Wikidata (propiedad `prop`)."""
    found: dict[str, str] = {}
    qids = []
    for r in con.execute(f"SELECT id, external_ids FROM entities WHERE type IN ({','.join('?' * len(types))}) AND id LIKE 'Q%' ORDER BY id", types):
        xi = json.loads(r["external_ids"] or "{}")
        if xi.get(key):
            found[r["id"]] = xi[key]
        else:
            qids.append(r["id"])
    if qids and len(found) < (limit or 10**9):
        h = wd_http or Http("wikidata")
        for i in range(0, len(qids), 40):
            r = h.json(h.cfg["endpoint"], {"action": "wbgetentities", "ids": "|".join(qids[i:i + 40]), "props": "claims", "format": "json"})
            for q, e in r.get("entities", {}).items():
                for c in e.get("claims", {}).get(prop, []):
                    v = c["mainsnak"].get("datavalue", {}).get("value")
                    if v:
                        found[q] = str(v)
                        break
            if limit and len(found) >= limit:
                break
    return found


def year_of(s: str | None) -> str | None:
    m = re.search(r"\b(1[5-9]\d\d)\b", s or "")
    return m.group(1) if m else None


def store_record(con: sqlite3.Connection, *, source: str, cfg: dict, url: str, stable_id: str, lines: list[str],
                 raw: str | None = None, mime: str = "text/plain", lang: str | None = None,
                 subjects: list[str] | None = None, claims: list[tuple[str, str, str, str]] | None = None) -> dict:
    """Captura el registro (texto canónico), segmenta, enlaza `subjects` (doc_subjects) y crea afirmaciones
    `claims` = (entidad, propiedad, valor, tipo) con evidencia = línea exacta `propiedad: valor` (pasaje)."""
    text = "\n".join(lines)
    doc_id, new = capture(con, source=source, url=url, stable_id=stable_id, content=text, license=cfg["license"],
                          reuse=Reuse(cfg.get("reuse", "consult_only")), mime=mime,
                          raw=(raw.encode() if raw else None))
    segment(con, doc_id, lang=lang)
    for s in subjects or []:
        con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, s))
    n_claims = 0
    passages = {p["text"]: p for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc_id,))}
    for ent, prop, val, vt in claims or []:
        line = f"{prop}: {val}"
        p = passages.get(line)
        if not p:
            continue
        cid = "C" + sha256(f"{source}|{ent}|{prop}|{val}|{stable_id}")[:16]
        con.execute("INSERT OR IGNORE INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind,extracted_by_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, ent, prop, jdump(val), vt, jdump({"source": source, "record": stable_id}), "asserted", 0.6, "structured",
                     jdump({"method": f"{source}-map", "version": "1", "date": now()}), now()))
        con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?,?,?,?)", (cid, p["id"], line, 0, len(line)))
        n_claims += 1
    con.commit()
    return {"doc_id": doc_id, "new": new, "claims": n_claims, "linked": len(subjects or [])}


def oai_harvest(http: Http, base_url: str, *, metadata_prefix: str = "oai_dc", set_spec: str | None = None,
                from_: str | None = None, max_records: int | None = None) -> Iterator[dict]:
    """Cosechador OAI-PMH mínimo (`ListRecords` con `resumptionToken`). Cede dicts `{_identifier, _datestamp, <campo>: [valores]}`
    con los campos de metadatos (p. ej. Dublin Core) agrupados por nombre local, en el orden del documento
    (`_identifier`/`_datestamp` llevan guion bajo para no chocar con un campo dc homónimo, p. ej. dc:identifier).
    Omite registros borrados (`status="deleted"`)."""
    params: dict = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
    if set_spec:
        params["set"] = set_spec
    if from_:
        params["from"] = from_
    n = 0
    while True:
        root = ET.fromstring(http.get(base_url, params))
        for rec in root.iterfind(".//oai:record", OAI_NS):
            header = rec.find("oai:header", OAI_NS)
            if header is None or header.get("status") == "deleted":
                continue
            fields: dict[str, list[str]] = {}
            md = rec.find("oai:metadata", OAI_NS)
            if md is not None:
                for el in md.iter():
                    if list(el) or el.text is None or not el.text.strip():
                        continue
                    fields.setdefault(el.tag.rsplit("}", 1)[-1], []).append(el.text.strip())
            out = dict(fields)
            out["_identifier"] = header.findtext("oai:identifier", "", OAI_NS)
            out["_datestamp"] = header.findtext("oai:datestamp", "", OAI_NS)
            yield out
            n += 1
            if max_records and n >= max_records:
                return
        token_el = root.find(".//oai:resumptionToken", OAI_NS)
        token = token_el.text.strip() if token_el is not None and token_el.text else None
        if not token:
            return
        params = {"verb": "ListRecords", "resumptionToken": token}


def host_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"
