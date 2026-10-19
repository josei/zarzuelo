"""IAI Zarzuela con HTTP simulado (metsresolver). TODOS los datos son FICTICIOS (PID, título, nombres)."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import iai_zarzuela as iai

PID = "999000111"
METS_FICTICIO = """<?xml version="1.0"?>
<mets:mets xmlns:mets="http://www.loc.gov/METS/" xmlns:mods="http://www.loc.gov/mods/v3">
<mets:dmdSec><mets:mdWrap><mets:xmlData>
<mods:mods>
  <mods:titleInfo><mods:title>Obra Ficticia en un acto</mods:title></mods:titleInfo>
  <mods:genre>zarzuela</mods:genre>
  <mods:originInfo><mods:dateIssued>1899</mods:dateIssued></mods:originInfo>
  <mods:name><mods:namePart type="given">Compositor</mods:namePart><mods:namePart type="family">Ficticio</mods:namePart></mods:name>
</mods:mods>
</mets:xmlData></mets:mdWrap></mets:dmdSec>
</mets:mets>"""


def fake_get(self, url, params=None, headers=None, **kw):
    if url.endswith("/metsresolver") and (params or {}).get("id") == PID:
        return METS_FICTICIO
    raise AssertionError(f"petición inesperada {url} {params}")


CFG = {"iai_zarzuela": {"validated": True, **iai.DEFAULT_CFG, "known_pids": [PID]}}


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    ins = "INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES(?,?,?,?,?,0)"
    con.execute(ins, ("Q1", "work", "Obra Ficticia en un acto", "[]", "{}"))
    con.execute(ins, ("Q2", "person", "Ficticio, Compositor", "[]", "{}"))
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES(?,?,?,?,?,?)",
                ("c1", "Q1", "composer", '"Q2"', "entity", "structured"))
    con.commit()
    return con


def test_ingest_known_pid_captures_and_links(setup):
    st = iai.ingest(setup, None)
    assert st == {"documents": 1, "linked": 1, "not_found": 0}
    d = setup.execute("SELECT stable_id, reuse FROM documents").fetchone()
    assert d["stable_id"] == PID and d["reuse"] == "consult_only"
    text = setup.execute(
        "SELECT text FROM passages WHERE document_id=(SELECT id FROM documents) ORDER BY char_start"
    ).fetchall()
    joined = "\n".join(r["text"] for r in text)
    assert "zarzuela" in joined and "1899" in joined and "Compositor Ficticio" in joined


def test_no_known_pids_means_no_network(con, monkeypatch):
    cfg = {"iai_zarzuela": {"validated": True, **iai.DEFAULT_CFG, "known_pids": []}}
    monkeypatch.setattr(kbhttp, "load_sources", lambda: cfg)
    def boom(*a, **k):
        raise AssertionError("no debería llamar a la red sin known_pids")
    monkeypatch.setattr(kbhttp.Http, "get", boom)
    assert iai.ingest(con, None) == {"documents": 0, "linked": 0, "not_found": 0}


def test_idempotent(setup):
    iai.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    iai.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1
