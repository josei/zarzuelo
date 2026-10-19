"""Datos FICTICIOS: nombres y registros inventados para probar el filtrado y la conciliación."""
import json
from pathlib import Path

from kb.sources import bne_local as B


def _write(path: Path, header: list[str], rows: list[list[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(";".join(header) + "\n" + "\n".join(";".join(r) for r in rows) + "\n", encoding="utf-8")


def _corpus(con):
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids) VALUES('Q1','person','Ana Ficticia','[]','{}')")
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids) VALUES('Q2','person','Ana Ficticia','[]','{}')")  # homónima
    for q, prop, y in (("Q1", "birth_date", 1850), ("Q1", "death_date", 1920), ("Q2", "birth_date", 1700), ("Q2", "death_date", 1760)):
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES(?,?,?,?,?,?)",
                    (f"c{q}{prop}", q, prop, json.dumps(str(y)), "edtf", "structured"))


def test_flip_and_years():
    assert B.flip("Ficticia, Ana, ( 1850-1920)") == "ana ficticia"
    assert B.heading_years("Ficticia, Ana, ( 1850-1920)") == (1850, 1920)
    assert B._years_agree((1850, 1920), (1850, 1920))
    assert not B._years_agree((1850, 1920), (1700, 1760))
    assert not B._years_agree((None, None), (1850, 1920))  # sin años no se concilia


def test_personas_reconcile_by_name_and_years(con, tmp_path):
    _corpus(con)
    _write(tmp_path / "per/persona-UTF8.csv", ["id", "id_symphony", "nombre_de_persona", "otros_nombres"],
           [['" 9811"', "", '"Ficticia, Ana, ( 1850-1920)"', '"A. Ficticia /**/ Anita"'],
            ['" 9812"', "", '"Ficticia, Ana, ( 1600-1650)"', ""],      # años no coinciden con ninguna
            ['" 9813"', "", '"Otra, Persona, ( 1850-1920)"', ""]])      # no está en el corpus
    st = B.ingest_personas(con, base=tmp_path)
    assert st["reconciled"] == 1 and st["ambiguous"] == 1
    e = con.execute("SELECT external_ids,labels_alt FROM entities WHERE id='Q1'").fetchone()
    assert json.loads(e["external_ids"])["bne_alma"] == "9811"
    assert "Anita" in json.loads(e["labels_alt"])
    assert con.execute("SELECT COUNT(*) FROM claims WHERE source_kind='structured' AND id NOT LIKE 'cQ%'").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM claim_evidence WHERE claim_id NOT LIKE 'cQ%'").fetchone()[0] == 2
    assert json.loads(con.execute("SELECT external_ids FROM entities WHERE id='Q2'").fetchone()[0]) == {}


def test_catalog_filters_by_topic(con, tmp_path):
    _write(tmp_path / "par/musicaescrita-UTF8.csv", ["id", "titulo", "autores"],
           [['" 1"', '"Obra ficticia: zarzuela en un acto"', '"Nadie, Fulano"'], ['" 2"', '"Sonata ficticia"', '"Nadie, Fulano"']])
    st = B.ingest_catalog(con, "partituras", base=tmp_path)
    assert st == {"scanned": 2, "kept": 1}
    assert con.execute("SELECT COUNT(*) FROM documents WHERE source='bne_partituras'").fetchone()[0] == 1


def test_hemeroteca_index(con, tmp_path):
    p = tmp_path / "fulltext-csv.csv"
    p.write_text('﻿"ISSN";"Título";"Enlace al texto completo (txt)"\n"0000-0000";"Periódico ficticio";"http://ejemplo.invalid/x.zip"\n', encoding="utf-8")
    assert B.ingest_hemeroteca_index(con, base=tmp_path) == {"titles": 1}
