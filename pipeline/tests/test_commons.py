"""Pruebas de commons.py con HTTP simulado. Todos los datos son FICTICIOS; sin red."""
import json

from kb import http as kbhttp
from kb.sources import commons

CFG = {"commons": {"validated": True, **commons.DEFAULT}}

FILE_PAGE = {
    "pageid": 1, "title": "File:Obra Ficticia - cartel.jpg",
    "imageinfo": [{
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Obra_Ficticia_-_cartel.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/x/Obra_Ficticia_-_cartel.jpg", "mime": "image/jpeg",
        "size": 234567, "width": 800, "height": 1200, "timestamp": "2020-01-01T00:00:00Z",
        "extmetadata": {
            "LicenseShortName": {"value": "CC BY-SA 4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
            "ImageDescription": {"value": "Cartel ficticio de Obra Ficticia, de Compositor Ficticio"},
            "Artist": {"value": "Autor Ficticio"}, "Credit": {"value": ""}, "DateTimeOriginal": {"value": "1950"},
            "Categories": {"value": "Zarzuela posters"}, "AttributionRequired": {"value": "true"}, "Restrictions": {"value": ""},
        },
    }],
}


def fake_get(self, url, params=None, headers=None, **kw):
    if url.endswith("/robots.txt"):
        return "User-agent: *\nDisallow: /w/\nCrawl-delay: 1"
    assert params["action"] == "query"
    if params.get("list") == "categorymembers":
        return json.dumps({"query": {"categorymembers": []}})
    if params.get("generator") == "categorymembers":
        if params.get("gcmtitle") == commons.SEEDS[0]:
            return json.dumps({"query": {"pages": [FILE_PAGE]}})
        return json.dumps({"query": {"pages": []}})
    if params.get("generator") == "search":
        return json.dumps({"query": {"pages": []}})
    raise AssertionError(params)


def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(commons, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    return con


def seed(con):
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('P1','person','Compositor Ficticio','{}')")
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('W1','work','Obra Ficticia','{}')")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) "
                "VALUES('c1','W1','composer','\"P1\"','entity','asserted','structured')")
    con.commit()


def test_file_record_and_render():
    rec = commons.file_record(FILE_PAGE)
    assert rec["license"] == "CC BY-SA 4.0" and rec["artist"] == "Autor Ficticio"
    text = commons.render(rec)
    assert "License: CC BY-SA 4.0" in text
    assert "Media (reference only, not stored): https://upload.wikimedia.org/wikipedia/commons/x/Obra_Ficticia_-_cartel.jpg" in text


def test_ingest_links_by_title_and_composer(con, monkeypatch):
    setup(con, monkeypatch)
    seed(con)
    stats = commons.ingest(con, None)
    assert stats["documents"] == 1 and stats["linked"] == 1 and stats["skipped_unlinked"] == 0
    subj = [r["entity_id"] for r in con.execute("SELECT * FROM doc_subjects")]
    assert subj == ["W1"]
    # nunca se descarga la imagen: solo se guarda la URL como referencia en el texto
    row = con.execute("SELECT reuse, license FROM documents").fetchone()
    assert row["reuse"] == "reusable" and row["license"] == "CC BY-SA 4.0"


def test_phase0(con, monkeypatch):
    setup(con, monkeypatch)
    rep = commons.phase0(1)
    assert rep["source"] == "commons" and rep["n_sample"] == 1
    assert rep["sample"][0]["license"] == "CC BY-SA 4.0"
    assert rep["robots"]["allowed"] is False  # /w/ vetado a rastreadores, pero se usa la API, no un crawler
