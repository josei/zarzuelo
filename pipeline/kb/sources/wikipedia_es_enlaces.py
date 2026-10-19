"""Vídeos de YouTube enlazados desde los artículos de es.wikipedia (prop=extlinks). Solo URL e identificador: el vídeo no se descarga."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

from ..http import Http
from ..store import capture, segment
from ..util import now

SOURCE = "wikipedia_es_enlaces"
_ID = re.compile(r"^[\w-]{11}$")


def video_id(url: str) -> str | None:
    """Identificador de vídeo de una URL de YouTube (watch?v=, youtu.be/, embed/); None si no es un vídeo (canal, lista…)."""
    u = urlsplit(url)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    vid = None
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [None])[0]
        elif u.path.startswith(("/embed/", "/v/")):
            vid = u.path.split("/")[2]
    return vid if vid and _ID.match(vid) else None


def _extlinks(http: Http, titles: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for query in ("youtube.com", "youtu.be"):
        params = {"action": "query", "prop": "extlinks", "titles": "|".join(titles), "ellimit": "max", "elquery": query, "format": "json"}
        while True:
            r = http.json(http.cfg["endpoint"], params)
            for pg in r.get("query", {}).get("pages", {}).values():
                for l in pg.get("extlinks", []):
                    out.setdefault(pg["title"], []).append(l["*"])
            if "continue" not in r:
                break
            params.update(r["continue"])
    return out


def _oembed(http: Http, vid: str) -> tuple[str, str, str]:
    """Estado, título y canal de un vídeo según el oEmbed público de YouTube. 404 = ya no existe o es privado; 401 = no se puede incrustar
    (sigue visible en YouTube); otro fallo = estado desconocido (no se descarta)."""
    http._wait()
    try:
        r = http.client.get("https://www.youtube.com/oembed", params={"format": "json", "url": f"https://www.youtube.com/watch?v={vid}"})
    except Exception:    # noqa: BLE001  (red: no se decide nada)
        return "desconocido", "", ""
    if r.status_code == 200:
        d = r.json()
        return "ok", " ".join(str(d.get("title", "")).split()), " ".join(str(d.get("author_name", "")).split())
    return {404: "no_disponible", 401: "sin_incrustar", 403: "sin_incrustar"}.get(r.status_code, "desconocido"), "", ""


def ingest(con: sqlite3.Connection, limit: int | None = None) -> dict:
    http = Http(SOURCE)
    cfg = http.cfg
    stats = {"articles": 0, "documents": 0, "videos": 0}
    import json
    todo = [(r["id"], json.loads(r["external_ids"]).get("eswiki")) for r in
            con.execute("SELECT id, external_ids FROM entities WHERE provisional=0 AND type IN ('work','person','venue','company')")]
    todo = [(q, t) for q, t in todo if t]
    for i in range(0, len(todo), 40):
        chunk = todo[i:i + 40]
        links = _extlinks(http, [t for _, t in chunk])
        for qid, title in chunk:
            ids = list(dict.fromkeys(v for v in (video_id(u) for u in links.get(title, [])) if v))
            if not ids:
                continue
            stats["articles"] += 1
            content = f"Vídeos de YouTube enlazados desde el artículo «{title}» de Wikipedia\n" + "\n".join("\t".join(("youtube " + v, *_oembed(http, v))) for v in ids)
            doc_id, new = capture(con, source=SOURCE, url=f"https://es.wikipedia.org/wiki/{title.replace(' ', '_')}#Enlaces_externos",
                                  stable_id=f"extlinks:{title}", content=content, license=cfg["license"], reuse=cfg["reuse"])
            stats["documents"] += int(new)
            stats["videos"] += len(ids)
            segment(con, doc_id, lang="es")
            con.execute("INSERT OR IGNORE INTO doc_subjects VALUES(?,?)", (doc_id, qid))
        if limit is not None and stats["articles"] >= limit:
            break
    con.commit()
    return stats
