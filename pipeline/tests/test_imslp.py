"""Pruebas de imslp.py con HTTP simulado. Todos los datos son FICTICIOS; sin red."""
import json

from kb import http as kbhttp
from kb.sources import imslp

CFG = {"imslp": {"validated": True, **imslp.DEFAULT}}

WIKITEXT = """{{Information}}
*****WORK INFO*****
|Work Title=Obra Ficticia
|First Performance=1899/05/12
|Year/Date of Composition=1898
|Librettist=Libretista Ficticio
|Language=Spanish
*****COMMENTS*****
Comentario ficticio de prueba.
*****END OF TEMPLATE*****
{{#fte:imslpfile
|File Name=Obra_Ficticia_vocal_score.pdf
|File Description=Partitura vocal ficticia
|Copyright=Public Domain
|Editor=Editor Ficticio
}}
"""

PAGE_TITLE = "Obra Ficticia (Compositor Ficticio)"


def fake_get(self, url, params=None, headers=None, **kw):
    if url.endswith("/robots.txt"):
        return "User-agent: *\nAllow: /\nCrawl-delay: 2"
    assert params["action"] == "query"
    if params.get("list") == "categorymembers":
        return json.dumps({"query": {"categorymembers": [{"title": PAGE_TITLE, "ns": 0}]}})
    if params.get("prop") == "revisions":
        return json.dumps({"query": {"pages": {"1": {
            "pageid": 1, "title": PAGE_TITLE,
            "revisions": [{"revid": 100, "timestamp": "2020-01-01T00:00:00Z", "*": WIKITEXT}]}}}})
    raise AssertionError(params)


def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(imslp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    return con


def seed(con):
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('P1','person','Compositor Ficticio','{}')")
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('W1','work','Obra Ficticia','{}')")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) "
                "VALUES('c1','W1','composer','\"P1\"','entity','asserted','structured')")
    con.commit()


def test_parse_work():
    parsed = imslp.parse_work(WIKITEXT)
    assert parsed["info"]["Work Title"] == "Obra Ficticia"
    assert parsed["info"]["First Performance"] == "1899/05/12"
    assert parsed["comments"] == "Comentario ficticio de prueba."
    assert len(parsed["files"]) == 1
    f = parsed["files"][0]
    assert f["name"] == "Obra_Ficticia_vocal_score.pdf" and f["copyright"] == "Public Domain"


def test_composer_and_title_from_page_title():
    assert imslp.composer_of(PAGE_TITLE) == "Compositor Ficticio"
    assert imslp.work_title_of(PAGE_TITLE) == "Obra Ficticia"


def test_ingest_links_and_adds_claims(con, monkeypatch):
    setup(con, monkeypatch)
    seed(con)
    stats = imslp.ingest(con, None)
    assert stats["documents"] == 1 and stats["linked"] == 1 and stats["unlinked"] == 0
    assert stats["files_referenced"] == 1
    claims = {r["property"]: json.loads(r["value_json"]) for r in con.execute("SELECT * FROM claims WHERE subject_id='W1'")}
    assert claims["composer"] == "P1"  # ya existía
    assert claims["premiere_date"] == "1899-05-12"
    assert "librettist" not in claims  # Libretista Ficticio no está en el corpus: no se enlaza (conservador)
    # el PDF nunca se descarga: solo aparece su nombre/copyright como referencia en el texto capturado
    text = "".join(r["text"] for r in con.execute("SELECT text FROM passages"))
    assert "Obra_Ficticia_vocal_score.pdf" in text and "Public Domain" in text

    # idempotente
    assert imslp.ingest(con, None)["documents"] == 0


def test_phase0(con, monkeypatch):
    setup(con, monkeypatch)
    rep = imslp.phase0(1)
    assert rep["source"] == "imslp" and rep["n_sample"] == 1
    assert rep["sample"][0]["title"] == PAGE_TITLE
    assert rep["robots"]["allowed"] is True
