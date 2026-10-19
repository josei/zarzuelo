"""Discogs con HTTP simulado. TODOS los datos son FICTICIOS."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import discogs as dg

CFG = {"discogs": {"validated": True, **dg.DEFAULT_CFG}}


def release(rid, credit_name="Compositor Ficticio", role="Composed By", track="Obra Ficticia"):
    return {"id": rid, "title": "Disco Ficticio", "year": 1975, "country": "Spain",
            "artists": [{"id": 9, "name": "Intérprete Ficticio (2)"}], "labels": [{"name": "Sello Ficticio"}],
            "formats": [{"name": "Vinyl"}], "images": [{"uri": "https://ejemplo.invalid/x.jpg"}],
            "tracklist": [{"position": "A1", "title": track, "extraartists": [{"id": 3, "name": credit_name, "role": role}]}]}


def fake_get(self, url, params=None, headers=None, **kw):
    assert headers["Authorization"] == "Discogs token=tok-ficticio"
    if url.endswith("/database/search"):
        return json.dumps({"results": [{"id": 1}, {"id": 2}]})
    if url.endswith("/releases/1"):
        return json.dumps(release(1))
    if url.endswith("/releases/2"):
        return json.dumps(release(2, credit_name="Otra Persona"))
    raise AssertionError(url)


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setenv("DISCOGS_TOKEN", "tok-ficticio")
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    ins = "INSERT INTO entities(id,type,label,external_ids,provisional) VALUES(?,?,?,'{}',0)"
    con.execute(ins, ("Q1", "person", "Compositor Ficticio"))
    con.execute(ins, ("Q2", "work", "Obra Ficticia"))
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES('c1','Q2','composer','\"Q1\"','entity','structured')")
    con.commit()
    return con


def test_accept_requires_title_and_composer_credit():
    assert dg.accept(release(1), "Obra Ficticia", ["Compositor Ficticio"])
    assert not dg.accept(release(1, role="Producer"), "Obra Ficticia", ["Compositor Ficticio"])
    assert not dg.accept(release(1, track="Otra Obra"), "Obra Ficticia", ["Compositor Ficticio"])
    assert not dg.accept(release(2, credit_name="Otra Persona"), "Obra Ficticia", ["Compositor Ficticio"])


def test_ingest_only_accepted_release(setup):
    st = dg.ingest(setup, None)
    assert st["releases"] == 1 and st["works"] == 1
    c = {(r["subject_id"], r["property"]): json.loads(r["value_json"]) for r in setup.execute("SELECT * FROM claims")}
    assert c[("DR:1", "recording_of")] == "Q2" and c[("DR:1", "recording_date")] == "1975"
    assert c[("DR:1", "performer")] == "DA:9"
    assert setup.execute("SELECT label FROM entities WHERE id='DA:9'").fetchone()[0] == "Intérprete Ficticio"
    # no se guarda ninguna imagen en el texto capturado
    assert "ejemplo.invalid" not in "".join(r["text"] for r in setup.execute("SELECT text FROM passages"))


def test_phase0_without_token(monkeypatch):
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    r = dg.phase0()
    assert r["validated"] is False and "DISCOGS_TOKEN" in r["missing"]


def test_ingest_requires_token(con, monkeypatch):
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        dg.ingest(con, 1)
