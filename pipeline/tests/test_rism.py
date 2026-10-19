"""RISM con HTTP simulado. TODOS los datos son FICTICIOS (IDs, títulos, nombres)."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import rism

CFG = {"rism": {"validated": True, **rism.DEFAULT_CFG}}
PERSON_REL = "people/90000001"
SRC_LINKED, SRC_OTHER = "sources/90100001", "sources/90100002"


def _source_doc(num: str, with_creator: bool = True) -> dict:
    d = {
        "id": f"https://rism.online/sources/{num}", "label": {"es": [f"Obra Ficticia {num}"]},
        "sourceTypes": {"sourceType": {"typeLabel": {"es": ["Manuscrito"]}}},
        "contents": {"summary": [{"label": {"es": ["Fecha"]}, "value": {"none": ["1800c"]}}]},
        "exemplars": {"items": [{"heldBy": {"siglum": "E-Xf", "label": {"none": ["Archivo Ficticio"]}},
                                 "summary": [{"label": {"en": ["Shelfmark"]}, "value": {"none": [f"Mus.ms.{num}"]}}]}]},
    }
    if with_creator:
        d["creator"] = {"role": {"label": {"es": ["Compositor/Autor"]}},
                        "relatedTo": {"id": f"https://rism.online/{PERSON_REL}", "label": {"none": ["Ficticio, Compositor"]}}}
    return d


def fake_get(self, url, params=None, headers=None, **kw):
    if url.endswith(f"{PERSON_REL}/sources"):
        return json.dumps({"items": [{"id": f"https://rism.online/{SRC_LINKED}"}, {"id": f"https://rism.online/{SRC_OTHER}"}]})
    if url.endswith(SRC_LINKED):
        return json.dumps(_source_doc("90100001", with_creator=True))
    if url.endswith(SRC_OTHER):
        return json.dumps(_source_doc("90100002", with_creator=False))
    raise AssertionError(f"petición inesperada {url}")


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    con.execute("INSERT INTO entities(id,type,label,external_ids,provisional) VALUES(?,?,?,?,0)",
                ("Q1", "person", "Compositor Ficticio", json.dumps({"rism": PERSON_REL})))
    con.commit()
    return con


def test_ingest_links_source_with_matching_composer(setup):
    st = rism.ingest(setup, None)
    assert st["persons"] == 1 and st["sources"] == 2 and st["documents"] == 2
    rows = {(r["subject_id"], r["property"]): r for r in setup.execute("SELECT * FROM claims")}
    assert json.loads(rows[("RISM:90100001", "composer")]["value_json"]) == "Q1"
    assert ("RISM:90100002", "composer") not in rows  # sin creator declarado: no se enlaza
    assert st["claims"] == 1


def test_claim_has_evidence_and_doc_license(setup):
    rism.ingest(setup, None)
    cid = setup.execute("SELECT id FROM claims WHERE subject_id='RISM:90100001'").fetchone()["id"]
    ev = setup.execute("SELECT * FROM claim_evidence WHERE claim_id=?", (cid,)).fetchone()
    assert ev is not None and "compositor:" in ev["quote"]
    d = setup.execute("SELECT license, reuse FROM documents WHERE source='rism' LIMIT 1").fetchone()
    assert (d["license"], d["reuse"]) == ("CC-BY-NC-SA-4.0", "consult_only")
    doc_subj = setup.execute("SELECT * FROM doc_subjects").fetchall()
    assert len(doc_subj) == 1  # solo la fuente enlazada queda indexada bajo el compositor del corpus


def test_limit_and_idempotent(setup):
    assert rism.ingest(setup, 1)["sources"] == 1
    rism.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM claims").fetchone()[0]
    rism.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM claims").fetchone()[0] == n1
