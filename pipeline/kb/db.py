"""Base SQLite (plan §5) con FTS5 sobre pasajes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "kb.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL, stable_id TEXT NOT NULL,
  retrieved_at TEXT NOT NULL, sha256 TEXT NOT NULL, license TEXT NOT NULL,
  reuse TEXT NOT NULL CHECK(reuse IN ('reusable','consult_only')), mime TEXT, raw_path TEXT,
  UNIQUE(source, stable_id, sha256));
CREATE TABLE IF NOT EXISTS passages(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
  char_start INT NOT NULL, char_end INT NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL,
  lang TEXT, ocr_conf REAL, CHECK(char_end > char_start));
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, content='passages', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS passages_ai AFTER INSERT ON passages BEGIN
  INSERT INTO passages_fts(rowid, text) VALUES (new.rowid, new.text); END;
CREATE TRIGGER IF NOT EXISTS passages_ad AFTER DELETE ON passages BEGIN
  INSERT INTO passages_fts(passages_fts, rowid, text) VALUES('delete', old.rowid, old.text); END;
CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL, labels_alt TEXT DEFAULT '[]',
  external_ids TEXT DEFAULT '{}', provisional INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES entities(id), property TEXT NOT NULL,
  value_json TEXT NOT NULL, value_type TEXT NOT NULL DEFAULT 'literal', qualifiers_json TEXT DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'asserted', hedge TEXT, confidence REAL DEFAULT 0,
  source_kind TEXT NOT NULL, extracted_by_json TEXT DEFAULT '{}', created_at TEXT);
CREATE INDEX IF NOT EXISTS claims_subject ON claims(subject_id, property);
CREATE TABLE IF NOT EXISTS claim_evidence(
  claim_id TEXT NOT NULL REFERENCES claims(id), passage_id TEXT NOT NULL REFERENCES passages(id),
  quote TEXT NOT NULL, quote_start INT NOT NULL, quote_end INT NOT NULL,
  PRIMARY KEY(claim_id, passage_id, quote_start));
CREATE TABLE IF NOT EXISTS claim_relations(
  claim_a TEXT NOT NULL, claim_b TEXT NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN ('conflicts_with','supports','refines')),
  PRIMARY KEY(claim_a, claim_b, relation));
CREATE TABLE IF NOT EXISTS narratives(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  time_edtf TEXT, place_id TEXT, status TEXT NOT NULL, confidence REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS narrative_entities(
  narrative_id TEXT NOT NULL, entity_id TEXT NOT NULL, role TEXT, PRIMARY KEY(narrative_id, entity_id));
CREATE TABLE IF NOT EXISTS narrative_evidence(
  narrative_id TEXT NOT NULL, passage_id TEXT NOT NULL, quote TEXT NOT NULL,
  quote_start INT NOT NULL, quote_end INT NOT NULL, PRIMARY KEY(narrative_id, passage_id, quote_start));
CREATE TABLE IF NOT EXISTS concepts(
  id TEXT PRIMARY KEY, scheme TEXT, pref_label TEXT NOT NULL, alt_labels TEXT DEFAULT '[]',
  hidden_labels TEXT DEFAULT '[]', scope_note TEXT, scope_source TEXT, broader TEXT DEFAULT '[]', related TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS outputs(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, kb_version TEXT NOT NULL, created_at TEXT NOT NULL,
  content_path TEXT, validation_report_json TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS output_citations(
  output_id TEXT NOT NULL, segment_id TEXT NOT NULL, claim_id TEXT, narrative_id TEXT, passage_id TEXT);
CREATE TABLE IF NOT EXISTS corpus(
  entity_id TEXT PRIMARY KEY, included INT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, engine TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INT DEFAULT 0, error TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS llm_calls(day TEXT NOT NULL, engine TEXT NOT NULL, n INT NOT NULL DEFAULT 0, cost_usd REAL DEFAULT 0, PRIMARY KEY(day, engine));
CREATE TABLE IF NOT EXISTS doc_subjects(document_id TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(document_id, entity_id));
CREATE TABLE IF NOT EXISTS extractions(passage_id TEXT NOT NULL, task TEXT NOT NULL, prompt_version TEXT NOT NULL, engine TEXT, status TEXT, n_proposed INT, n_kept INT, PRIMARY KEY(passage_id, task));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=120)  # varios procesos (extracción, captura) comparten la base
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")       # lectores y escritores de procesos distintos no se bloquean entre sí
    con.execute("PRAGMA busy_timeout=120000")
    con.executescript(SCHEMA)
    return con


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True)
