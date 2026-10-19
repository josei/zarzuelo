"""Europeana con HTTP simulado. TODOS los datos (IDs, nombres, textos) son FICTICIOS; sin red."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import europeana as eu

CFG = {"europeana": {"validated": True, "endpoint": "https://api.europeana.eu/record/v2/search.json",
                      "license": "por objeto (campo rights de Europeana)", "reuse": "consult_only",
                      "rate_limit_per_s": 1000, "country": "Spain", "queries": ["ficticia"]}}
KEY = "clave-ficticia"

ITEM1 = {
    "id": "/123/item1", "title": ["Obra Ficticia Uno"], "dcCreator": ["http://www.wikidata.org/entity/Q999888"],
    "year": ["1890"], "type": "IMAGE", "dcDescription": ["Descripción ficticia uno"],
    "dataProvider": ["Biblioteca Ficticia"], "provider": ["Hispana"],
    "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
    "edmIsShownAt": ["https://ejemplo.invalid/view/1"], "edmIsShownBy": ["https://ejemplo.invalid/media/1.jpg"],
    "guid": "https://www.europeana.eu/item/123/item1?lang=es",
}
ITEM2 = {
    "id": "/456/item2", "title": ["Obra Ficticia Dos"], "dcCreator": ["Ficticio, Pepe (1850-1920)"],
    "year": ["1901"], "type": "TEXT", "dcDescription": ["Descripción ficticia dos"],
    "dataProvider": [], "provider": ["Europeana"], "rights": ["http://rightsstatements.org/vocab/InC/1.0/"],
}


class FakeResp:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class FakeHttp:
    """Sustituye por completo a kb.http.Http: json()/client.head()/client.get() simulados, sin red."""

    def __init__(self):
        self.cfg = CFG["europeana"]
        self.client = self

    def _wait(self):
        pass

    def json(self, url, params=None, headers=None, use_cache=True):
        assert headers == {"X-Api-Key": KEY}
        p = params or {}
        if url == self.cfg["endpoint"]:
            if p.get("query") != "ficticia":
                return {"items": []}
            assert p["qf"] == "COUNTRY:Spain"
            return {"items": [ITEM1, ITEM2], "totalResults": 2, "nextCursor": None}
        if url.endswith("/123/item1.json"):
            return {"object": {"agents": [{"about": "http://www.wikidata.org/entity/Q999888",
                                           "prefLabel": {"es": ["Persona Ficticia"]}}]}}
        raise AssertionError(f"petición inesperada {url} {p}")

    def head(self, url, headers=None):
        assert headers == {"X-Api-Key": KEY}
        return FakeResp(200 if "/123/item1/" in url else 404)

    def get(self, url, **kw):
        if "iiif.europeana.eu" in url:
            return FakeResp(text="User-agent: *\nDisallow:")
        return FakeResp(text="User-agent: *\nDisallow: /api/*\nDisallow: /iiif/*\n")


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setenv("EUROPEANA_API_KEY", KEY)
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(eu, "Http", lambda name, cache=False, cfg=None: FakeHttp())
    ins = "INSERT INTO entities(id,type,label,external_ids,provisional) VALUES(?,?,?,?,0)"
    con.execute(ins, ("Q999888", "person", "Persona Ficticia", "{}"))
    con.execute(ins, ("P1", "person", "Pepe Ficticio", "{}"))
    con.execute(ins, ("W1", "work", "Obra Ficticia Dos", "{}"))
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES"
                "('c1','W1','composer','\"P1\"','entity','structured')")
    con.commit()
    return con


def test_reuse_for():
    assert eu.reuse_for("http://creativecommons.org/publicdomain/mark/1.0/") == eu.Reuse.reusable
    assert eu.reuse_for("http://creativecommons.org/publicdomain/zero/1.0/") == eu.Reuse.reusable
    assert eu.reuse_for("http://creativecommons.org/licenses/by/4.0/") == eu.Reuse.reusable
    assert eu.reuse_for("http://rightsstatements.org/vocab/InC/1.0/") == eu.Reuse.consult_only
    assert eu.reuse_for(None) == eu.Reuse.consult_only
    assert eu.reuse_for("") == eu.Reuse.consult_only


def test_ingest_links_captures_and_scrubs_key(setup):
    st = eu.ingest(setup, None)
    assert st == {"documents": 2, "seen": 2, "linked_objects": 2, "links": 3, "reusable": 1, "consult_only": 1, "iiif": 1}
    rows = {r["stable_id"]: r for r in setup.execute("SELECT * FROM documents")}
    assert rows["/123/item1"]["reuse"] == "reusable" and rows["/456/item2"]["reuse"] == "consult_only"
    text = "".join(r["text"] for r in setup.execute("SELECT text FROM passages"))
    assert "Creador: Persona Ficticia <http://www.wikidata.org/entity/Q999888>" in text
    assert "Medio (solo referencia): https://ejemplo.invalid/media/1.jpg" in text
    assert KEY not in text  # la clave nunca se guarda en el texto capturado


def test_ingest_links_person_and_work(setup):
    eu.ingest(setup, None)
    subs = {r["document_id"]: r["entity_id"] for r in setup.execute(
        "SELECT ds.* FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id WHERE d.stable_id='/456/item2'")}
    assert set(subs.values()) <= {"P1", "W1"}
    n = setup.execute("SELECT count(*) FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id "
                      "WHERE d.stable_id='/456/item2'").fetchone()[0]
    assert n == 2  # P1 (compositor) y W1 (obra), via _objlink.Index


def test_ingest_idempotent(setup):
    eu.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    eu.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1


def test_phase0_requires_key(monkeypatch):
    monkeypatch.delenv("EUROPEANA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        eu.phase0()


def test_phase0_reports_robots_and_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("EUROPEANA_API_KEY", KEY)
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(eu, "Http", lambda name, cache=False, cfg=None: FakeHttp())
    monkeypatch.setattr(eu.kbdb, "DATA", tmp_path)
    r = eu.phase0(2)
    assert r["sample"] == 2
    assert r["rights_in_sample"] == {"http://creativecommons.org/publicdomain/mark/1.0/": 1,
                                      "http://rightsstatements.org/vocab/InC/1.0/": 1}
    assert r["reuse_in_sample"] == {"reusable": 1, "consult_only": 1}
    assert r["iiif_manifest_status_first_item"] == 200
    assert r["robots"]["www.europeana.eu"] == {"url": "https://www.europeana.eu/robots.txt",
                                                "disallow_api": True, "disallow_iiif": True}
    assert (tmp_path / "phase0" / "europeana_sample.json").exists()  # nunca en el data/ real (ver conftest)
