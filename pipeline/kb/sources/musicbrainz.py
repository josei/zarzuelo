"""MusicBrainz: obras, grabaciones e intérpretes enlazados al corpus (§3.1, §4.4). Núcleo CC0, 1 petición/s, UA obligatorio.

Enlace (conservador): 1) ID `musicbrainz_work` ya presente (viene de Wikidata P435); 2) si no, obra del corpus cuyo
compositor tiene `musicbrainz_artist` y cuyo título normalizado coincide EXACTAMENTE con una única obra de ese artista en MB.
Solo se guardan texto y metadatos; no se descarga audio ni imágenes (Cover Art Archive no se toca).
"""
from __future__ import annotations

import json
import sqlite3

import httpx

from ..http import Http
from ..models import Reuse
from ..store import capture, segment
from ..util import now, norm
from . import _music as M

DEFAULT_CFG = {
    "endpoint": "https://musicbrainz.org/ws/2", "license": "CC0-1.0", "reuse": "reusable", "rate_limit_per_s": 1,
}
SITE = "https://musicbrainz.org"
MAX_PAGES = 5  # páginas de 100 obras por compositor
METHOD = "musicbrainz-map"


def phase0(sample: int = 10) -> dict:
    """Términos/robots, endpoint, `sample` registros reales y licencia efectiva."""
    http = Http("musicbrainz", cache=False, cfg=DEFAULT_CFG)
    http._wait()
    robots = http.client.get(f"{SITE}/robots.txt")
    http._wait()
    r = http.json(f"{DEFAULT_CFG['endpoint']}/work", {"query": 'work:zarzuela', "fmt": "json", "limit": sample})
    works = [{"id": w["id"], "title": w["title"], "type": w.get("type"), "via": "search"} for w in r.get("works", [])]
    if len(works) < sample:  # completa con obras de compositores del corpus que ya traen ID MusicBrainz (Wikidata P434)
        from ..db import connect
        con = connect()
        for mbid in M.corpus_index(con, "musicbrainz_artist"):
            if len(works) >= sample:
                break
            for w in (_get(http, "work", artist=mbid, limit=sample - len(works)) or {}).get("works", []):
                works.append({"id": w["id"], "title": w["title"], "type": w.get("type"), "via": f"artist:{mbid}"})
        works = works[:sample]
    return {
        "source": "musicbrainz", "robots_status": robots.status_code,
        "robots_allows_ws": "Disallow: /ws/" not in robots.text,
        "terms": "https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting (1 req/s, User-Agent con contacto obligatorio); "
                 "https://musicbrainz.org/doc/About/Data_License",
        "endpoint": DEFAULT_CFG["endpoint"], "sample_n": len(works), "sample": works,
        "license": "CC0-1.0 (datos núcleo: artistas, obras, grabaciones, lanzamientos). Anotaciones, tags y ratings: CC BY-NC-SA 3.0 (no se almacenan)",
        "reuse": "reusable", "checked_at": now(),
    }


def _get(http: Http, path: str, **params) -> dict | None:
    try:
        return http.json(f"{http.cfg['endpoint']}/{path}", {"fmt": "json", **params})
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return None
        raise


def _artist_works(http: Http, mbid: str) -> list[dict]:
    out: list[dict] = []
    for page in range(MAX_PAGES):
        r = _get(http, "work", artist=mbid, limit=100, offset=page * 100)
        if not r:
            break
        out += r.get("works", [])
        if len(out) >= r.get("work-count", 0):
            break
    return out


def _match(http: Http, work: dict, mb_artist_of: dict, cache: dict) -> str | None:
    """Título normalizado exactamente igual y único entre las obras de algún compositor del corpus con ID MB."""
    cands: set[str] = set()
    for comp in work["composers"]:
        mbid = mb_artist_of.get(comp)
        if not mbid:
            continue
        if mbid not in cache:
            cache[mbid] = _artist_works(http, mbid)
        cands |= {w["id"] for w in cache[mbid] if norm(w["title"]) == norm(work["label"])}
    return next(iter(cands)) if len(cands) == 1 else None


def _render(w: dict, recs: list[dict]) -> tuple[str, list[dict]]:
    lines = [f"MB work {w['id']} {w['title']}"]
    if w.get("type"):
        lines.append(f"type: {w['type']}")
    if w.get("languages"):
        lines.append("language: " + ", ".join(w["languages"]))
    elif w.get("language"):
        lines.append(f"language: {w['language']}")
    for i in w.get("iswcs", []) or []:
        lines.append(f"iswc: {i}")
    rels = []
    for rel in w.get("relations", []):
        a = rel.get("artist")
        if a and rel["type"] in ("composer", "librettist"):
            lines.append(f"{rel['type']}: {a['name']} [{a['id']}]")
            rels.append({"type": rel["type"], "id": a["id"], "name": a["name"]})
    for rec in recs:
        d = rec.get("first-release-date") or ""
        lines.append(f"recording: {rec['title']} [{rec['id']}]" + (f" first-release-date: {d}" if d else ""))
        for ac in rec.get("artist-credit", []):
            if isinstance(ac, dict) and ac.get("artist"):
                lines.append(f"recording [{rec['id']}] credited-artist: {ac['artist']['name']} [{ac['artist']['id']}]")
    return "\n".join(lines), rels


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http("musicbrainz")
    cfg = http.cfg
    work_of = M.corpus_index(con, "musicbrainz_work")        # mbid -> id corpus
    person_of = M.corpus_index(con, "musicbrainz_artist")
    mb_artist_of = {v: k for k, v in person_of.items()}      # id corpus -> mbid
    stats = {"works": 0, "linked_by_title": 0, "documents": 0, "recordings": 0, "artists": 0, "claims": 0, "not_found": 0}
    cache: dict = {}
    corpus = M.corpus_works(con)
    corpus.sort(key=lambda w: "musicbrainz_work" not in w["xids"])  # primero los ya enlazados por ID
    for w in corpus:
        if limit is not None and stats["works"] >= limit:
            break
        mbid, conf = w["xids"].get("musicbrainz_work"), 0.9
        if not mbid:
            mbid, conf = _match(http, w, mb_artist_of, cache), 0.75
            if not mbid:
                continue
        mw = _get(http, f"work/{mbid}", inc="artist-rels")
        if not mw:
            stats["not_found"] += 1
            continue
        recs: list[dict] = []
        for page in range(3):  # hasta 300 grabaciones por obra
            rr = _get(http, "recording", work=mbid, inc="artist-credits", limit=100, offset=page * 100)
            if not rr:
                break
            recs += rr.get("recordings", [])
            if len(recs) >= rr.get("recording-count", 0):
                break
        text, rels = _render(mw, recs)
        doc_id, new = capture(
            con, source="musicbrainz", url=f"{SITE}/work/{mbid}", stable_id=mbid, content=text, license=cfg["license"],
            reuse=Reuse(cfg["reuse"]), mime="text/plain",
            raw=json.dumps({"work": mw, "recordings": recs}, ensure_ascii=False).encode())
        stats["documents"] += int(new)
        segment(con, doc_id, lang="es")
        passages = {p["text"]: p for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc_id,))}
        wid = w["id"]
        if "musicbrainz_work" not in w["xids"]:
            stats["linked_by_title"] += 1
        M.add_xid(con, wid, "musicbrainz_work", mbid)
        for i in mw.get("iswcs", []) or []:
            M.add_xid(con, wid, "iswc", i)

        def artist(aid: str, name: str) -> str:
            if aid in person_of:
                return person_of[aid]
            eid = f"MBA:{aid}"
            if not con.execute("SELECT 1 FROM entities WHERE id=?", (eid,)).fetchone():
                stats["artists"] += 1
            M.ensure_entity(con, eid, "person", name, {"musicbrainz_artist": aid})
            return eid

        for rel in rels:
            line = f"{rel['type']}: {rel['name']} [{rel['id']}]"
            stats["claims"] += M.add_claim(con, passages, subject=wid, name=rel["type"], value=artist(rel["id"], rel["name"]),
                                           vtype="entity", line=line, method=METHOD, confidence=conf)
        for rec in recs:
            rid = f"MBR:{rec['id']}"
            M.ensure_entity(con, rid, "recording", rec["title"], {"musicbrainz_recording": rec["id"]})
            stats["recordings"] += 1
            stats["claims"] += M.add_claim(con, passages, subject=rid, name="recording_of", value=wid, vtype="entity",
                                           line=f"recording: {rec['title']} [{rec['id']}]" + (f" first-release-date: {rec['first-release-date']}" if rec.get("first-release-date") else ""),
                                           method=METHOD, confidence=conf)
            if rec.get("first-release-date"):
                stats["claims"] += M.add_claim(con, passages, subject=rid, name="recording_date", value=rec["first-release-date"],
                                               vtype="edtf", line=f"recording: {rec['title']} [{rec['id']}] first-release-date: {rec['first-release-date']}",
                                               method=METHOD, confidence=conf)
            for ac in rec.get("artist-credit", []):
                if isinstance(ac, dict) and ac.get("artist"):
                    a = ac["artist"]
                    stats["claims"] += M.add_claim(
                        con, passages, subject=rid, name="performer", value=artist(a["id"], a["name"]), vtype="entity",
                        line=f"recording [{rec['id']}] credited-artist: {a['name']} [{a['id']}]", method=METHOD, confidence=conf)
        stats["works"] += 1
        con.commit()
    con.commit()
    return stats
