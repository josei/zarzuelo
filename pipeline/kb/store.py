"""Capturar (instantáneas inmutables) y segmentar (pasajes con offsets) — plan §4 pasos 2–3."""
from __future__ import annotations

import gzip
import re
import sqlite3

from .db import DATA
from .models import Reuse
from .util import now, sha256


def capture(con: sqlite3.Connection, *, source: str, url: str, stable_id: str, content: str,
            license: str, reuse: Reuse | str, mime: str = "text/plain", raw: bytes | None = None) -> tuple[str, bool]:
    """Guarda una instantánea. Nunca sobrescribe: si el hash cambió, crea otra. -> (doc_id, es_nueva)."""
    h = sha256(content)
    row = con.execute("SELECT id FROM documents WHERE source=? AND stable_id=? AND sha256=?", (source, stable_id, h)).fetchone()
    if row:
        return row["id"], False
    doc_id = "D" + sha256(f"{source}|{stable_id}|{h}")[:16]
    raw_dir = DATA / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rp = raw_dir / f"{h}.gz"
    if not rp.exists():
        rp.write_bytes(gzip.compress(raw if raw is not None else content.encode()))
    # el texto normalizado sobre el que apuntan los offsets se guarda aparte
    tp = raw_dir / f"{h}.txt.gz"
    if not tp.exists():
        tp.write_bytes(gzip.compress(content.encode()))
    con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (doc_id, source, url, stable_id, now(), h, license, Reuse(reuse).value, mime, str(rp)))
    con.commit()
    return doc_id, True


def document_text(con, doc_id: str) -> str:
    h = con.execute("SELECT sha256 FROM documents WHERE id=?", (doc_id,)).fetchone()["sha256"]
    return gzip.decompress((DATA / "raw" / f"{h}.txt.gz").read_bytes()).decode()


_SENT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÑ¿¡«\"“])")


def split_spans(text: str, max_len: int = 1200) -> list[tuple[int, int]]:
    """Párrafos (separados por línea en blanco o salto) y, si son largos, frases. Offsets exactos."""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"[^\n]+", text):
        s, e = m.start(), m.end()
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e <= s:
            continue
        if e - s <= max_len:
            spans.append((s, e))
            continue
        cur = s
        for sm in _SENT.finditer(text, s, e):
            if sm.start() - cur >= max_len * 0.6:
                spans.append((cur, sm.start()))
                cur = sm.end()
        if e > cur:
            spans.append((cur, e))
    return spans


def segment(con: sqlite3.Connection, doc_id: str, lang: str | None = None) -> int:
    """Idempotente: si ya hay pasajes del documento, no hace nada. -> nº de pasajes creados."""
    if con.execute("SELECT 1 FROM passages WHERE document_id=? LIMIT 1", (doc_id,)).fetchone():
        return 0
    text = document_text(con, doc_id)
    n = 0
    for s, e in split_spans(text):
        t = text[s:e]
        con.execute("INSERT INTO passages(id,document_id,char_start,char_end,text,sha256,lang) VALUES(?,?,?,?,?,?,?)",
                    (f"P{sha256(f'{doc_id}|{s}')[:16]}", doc_id, s, e, t, sha256(t), lang))
        n += 1
    con.commit()
    return n
