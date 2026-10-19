"""Discogs: lanzamientos (grabaciones) y artistas enlazados a obras del corpus (§3.6). Requiere DISCOGS_TOKEN (.env).

API v2 (https://api.discogs.com), 60 peticiones/min autenticado, User-Agent obligatorio. Los datos de lanzamientos,
artistas y sellos son CC0 (https://data.discogs.com); las condiciones de uso de la API restringen imágenes y datos de
usuarios/marketplace, que NO se guardan. Solo texto y metadatos; ninguna imagen ni audio.

Enlace (conservador): se busca por título + compositor y un lanzamiento solo se acepta si a) una pista tiene como título
exactamente el título de la obra (normalizado) y b) los créditos (lanzamiento o pista) incluyen al compositor del corpus
con rol de composición/autoría.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

import httpx

from ..http import Http
from ..models import Reuse
from ..store import capture, segment
from ..util import norm, now
from . import _music as M

DEFAULT_CFG = {"endpoint": "https://api.discogs.com", "license": "CC0-1.0", "reuse": "reusable", "rate_limit_per_s": 1}
METHOD = "discogs-map"
_ROLE = re.compile(r"compos|written|music by|words by|libret", re.I)
_SUFFIX = re.compile(r"\s+\(\d+\)$")


def _token() -> str | None:
    return os.environ.get("DISCOGS_TOKEN") or None


def _headers() -> dict:
    return {"Authorization": f"Discogs token={_token()}", "Accept": "application/vnd.discogs.v2.plain+json"}


def _name(n: str) -> str:
    return _SUFFIX.sub("", n).strip()


def phase0(sample: int = 10) -> dict:
    out = {
        "source": "discogs", "endpoint": DEFAULT_CFG["endpoint"],
        "terms": "https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use ; datos CC0: https://data.discogs.com",
        "license": "CC0-1.0 (metadatos de lanzamientos, artistas y sellos); imágenes y datos de usuario/marketplace excluidos",
        "reuse": "reusable", "checked_at": now(),
    }
    if not _token():
        out.update(validated=False, sample_n=0, sample=[],
                   missing="DISCOGS_TOKEN (token personal: discogs.com > Settings > Developers > Generate new token); "
                           "añadirlo a .env o al entorno y relanzar `kb source phase0 discogs`")
        return out
    http = Http("discogs", cache=False, cfg=DEFAULT_CFG)
    http._wait()
    rb = http.client.get(f"{DEFAULT_CFG['endpoint']}/robots.txt")
    out["robots_status"] = rb.status_code
    r = http.json(f"{DEFAULT_CFG['endpoint']}/database/search", {"q": "zarzuela", "type": "release", "per_page": sample}, _headers())
    res = r.get("results", [])[:sample]
    out["sample_n"] = len(res)
    out["sample"] = [{"id": x["id"], "title": x.get("title"), "year": x.get("year"), "country": x.get("country")} for x in res]
    out["validated"] = len(res) >= min(sample, 1)
    return out


def _get(http: Http, path: str, **params) -> dict | None:
    try:
        return http.json(f"{http.cfg['endpoint']}/{path}", params, _headers())
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return None
        raise


def _credits(rel: dict) -> list[dict]:
    out = list(rel.get("extraartists", []))
    for t in rel.get("tracklist", []):
        out += t.get("extraartists", [])
    return out


def accept(rel: dict, title: str, composer_names: list[str]) -> dict | None:
    """-> pista coincidente si el lanzamiento cumple a) y b); si no, None."""
    track = next((t for t in rel.get("tracklist", []) if norm(t.get("title", "")) == norm(title)), None)
    if not track:
        return None
    names = {norm(_name(n)) for n in composer_names}
    if any(norm(_name(c.get("name", ""))) in names and _ROLE.search(c.get("role", "")) for c in _credits(rel)):
        return track
    return None


def _render(rel: dict, track: dict, work_title: str) -> str:
    lines = [f"Discogs release {rel['id']} {rel.get('title', '')}"]
    for a in rel.get("artists", []):
        lines.append(f"artist: {_name(a['name'])} [{a['id']}]")
    for l in rel.get("labels", []):
        lines.append(f"label: {l['name']}")
    if rel.get("year"):
        lines.append(f"year: {rel['year']}")
    if rel.get("country"):
        lines.append(f"country: {rel['country']}")
    for f in rel.get("formats", []):
        lines.append(f"format: {f.get('name', '')}")
    lines.append(f"track {track.get('position', '')}: {track['title']}")
    for c in _credits(rel):
        if _ROLE.search(c.get("role", "")):
            lines.append(f"credit: {_name(c['name'])} [{c.get('id')}] {c['role']}")
    return "\n".join(lines)


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    if not _token():
        raise RuntimeError("falta DISCOGS_TOKEN en el entorno o en .env")
    http = Http("discogs")
    cfg = http.cfg
    labels = {r["id"]: r["label"] for r in con.execute("SELECT id,label FROM entities WHERE type='person'")}
    stats = {"works": 0, "searched": 0, "documents": 0, "releases": 0, "artists": 0, "claims": 0}
    for w in M.corpus_works(con):
        if limit is not None and stats["searched"] >= limit:
            break
        comps = [labels[c] for c in w["composers"] if c in labels]
        if not comps:
            continue
        stats["searched"] += 1
        sr = _get(http, "database/search", q=f"{w['label']} {comps[0]}", type="release", per_page=5) or {}
        hit = False
        for res in sr.get("results", [])[:5]:
            rel = _get(http, f"releases/{res['id']}")
            if not rel:
                continue
            track = accept(rel, w["label"], comps)
            if not track:
                continue
            hit = True
            text = _render(rel, track, w["label"])
            doc_id, new = capture(
                con, source="discogs", url=f"https://www.discogs.com/release/{rel['id']}", stable_id=f"{rel['id']}",
                content=text, license=cfg["license"], reuse=Reuse(cfg["reuse"]), mime="text/plain",
                raw=json.dumps(rel, ensure_ascii=False).encode())
            stats["documents"] += int(new)
            segment(con, doc_id, lang="es")
            passages = {p["text"]: p for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc_id,))}
            rid = f"DR:{rel['id']}"
            M.ensure_entity(con, rid, "recording", rel.get("title", ""), {"discogs_release": str(rel["id"])})
            stats["releases"] += 1
            stats["claims"] += M.add_claim(con, passages, subject=rid, name="recording_of", value=w["id"], vtype="entity",
                                           line=f"track {track.get('position', '')}: {track['title']}", method=METHOD, confidence=0.7)
            if rel.get("year"):
                stats["claims"] += M.add_claim(con, passages, subject=rid, name="recording_date", value=str(rel["year"]),
                                               vtype="edtf", line=f"year: {rel['year']}", method=METHOD, confidence=0.7)
            for a in rel.get("artists", []):
                eid = f"DA:{a['id']}"
                if not con.execute("SELECT 1 FROM entities WHERE id=?", (eid,)).fetchone():
                    stats["artists"] += 1
                M.ensure_entity(con, eid, "person", _name(a["name"]), {"discogs_artist": str(a["id"])})
                stats["claims"] += M.add_claim(con, passages, subject=rid, name="performer", value=eid, vtype="entity",
                                               line=f"artist: {_name(a['name'])} [{a['id']}]", method=METHOD, confidence=0.7)
        stats["works"] += int(hit)
        con.commit()
    con.commit()
    return stats
