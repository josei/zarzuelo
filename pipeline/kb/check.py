"""`kb check` (AGENTS.md): debe pasar antes de desplegar."""
from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

from .export import MAX_BYTES, OUT


def run_checks(con: sqlite3.Connection, api: Path = OUT) -> list[str]:
    errs: list[str] = []
    n = con.execute("SELECT COUNT(*) FROM claims c WHERE status!='deprecated' AND NOT EXISTS (SELECT 1 FROM claim_evidence e WHERE e.claim_id=c.id)").fetchone()[0]
    if n:
        errs.append(f"{n} afirmaciones sin evidencia")
    n = con.execute("SELECT COUNT(*) FROM narratives c WHERE NOT EXISTS (SELECT 1 FROM narrative_evidence e WHERE e.narrative_id=c.id)").fetchone()[0]
    if n:
        errs.append(f"{n} narrativas sin evidencia")
    # segmentos de salidas: todo segmento con cita
    for o in con.execute("SELECT * FROM outputs"):
        content = json.loads(Path(o["content_path"]).read_text())
        for s in content.get("segments", []):
            if not (s.get("claim_ids") or s.get("narrative_ids") or s.get("passage_ids")):
                errs.append(f"{o['id']}: segmento {s['id']} sin cita")
        for q in content.get("questions", []):
            if not q.get("claim_ids"):
                errs.append(f"{o['id']}: pregunta {q['id']} sin cita")
    if not api.exists():
        errs.append("no hay API exportada (kb export)")
        return errs
    # tamaño máximo (comprimido)
    for f in api.rglob("*.json"):
        if len(gzip.compress(f.read_bytes())) > MAX_BYTES:
            errs.append(f"{f.relative_to(api)} supera {MAX_BYTES} bytes comprimido")
    # esquemas válidos
    for f in (api / "schema").glob("*.schema.json"):
        try:
            json.loads(f.read_text())
        except Exception as e:  # noqa: BLE001
            errs.append(f"esquema inválido {f.name}: {e}")
    # no exponer texto consult_only
    consult = [r["text"] for r in con.execute("SELECT p.text FROM passages p JOIN documents d ON d.id=p.document_id WHERE d.reuse='consult_only'")]
    if consult:
        blob = "".join(f.read_text() for f in api.rglob("*.json") if "schema" not in f.parts)
        for t in consult:
            if len(t) > 60 and t in blob:
                errs.append("texto de fuente consult_only expuesto en la API")
                break
    for f in (api / "evidence").glob("*.json") if (api / "evidence").exists() else []:
        for it in json.loads(f.read_text()):
            if it["reuse"] != "reusable" and it["text"] is not None:
                errs.append(f"{f.name}: texto consult_only en evidencia")
    return errs
