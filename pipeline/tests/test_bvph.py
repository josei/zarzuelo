"""bvph.py con HTTP (OAI-PMH) simulado. Identificadores, títulos y cabeceras son FICTICIOS; sin red."""
import httpx
import pytest

from kb import http as kbhttp
from kb.sources import bvph as V

CFG = {"bvph": {"validated": True, **V.DEFAULT_CFG}}


def rec_xml(ident: str, title: str, rights: str, deleted: bool = False) -> str:
    if deleted:
        return f'<record><header status="deleted"><identifier>{ident}</identifier><datestamp>2020-01-01</datestamp></header></record>'
    return (f'<record><header><identifier>{ident}</identifier><datestamp>2020-01-01</datestamp>'
            f'<setSpec>prensahistorica5</setSpec></header><metadata>'
            f'<oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{title}</dc:title><dc:rights>{rights}</dc:rights>'
            f'<dc:identifier>https://ejemplo.invalid/registro.do?id={ident}</dc:identifier>'
            f'</oai_dc:dc></metadata></record>')


def envelope(body: str) -> str:
    return (f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<ListRecords>{body}</ListRecords></OAI-PMH>')


THREE_RECS = (rec_xml("oai:bvph:1", "Diario Ficticio de Madrid", "CC BY 4.0 - http://creativecommons.org/licenses/by/4.0/")
              + rec_xml("oai:bvph:2", "Boletín Ficticio Restringido", "http://rightsstatements.org/vocab/InC/1.0/")
              + rec_xml("oai:bvph:3", "x", "x", deleted=True))


def fake_get(self, url, params=None, headers=None, **kw):
    p = params or {}
    if url == V.ROBOTS:
        raise httpx.HTTPStatusError("nf", request=httpx.Request("GET", url), response=httpx.Response(404))
    if p.get("verb") == "Identify":
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><Identify>'
                '<repositoryName>BVPH Ficticia</repositoryName></Identify></OAI-PMH>')
    if p.get("verb") == "ListSets":
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListSets>'
                '<set><setSpec>prensahistorica5</setSpec><setName>x</setName></set></ListSets></OAI-PMH>')
    if p.get("verb") == "ListRecords":
        if p.get("set") == "prensahistorica5":
            return envelope(THREE_RECS)
        return envelope("")  # otros sets: vacíos en la prueba
    raise AssertionError((url, p))


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    return con


def test_phase0_reports_no_robots_and_sample(setup):
    rep = V.phase0(sample=5)
    assert rep["validated"] is True
    assert rep["identify"]["repositoryName"] == "BVPH Ficticia"
    assert rep["sample_n"] == 2  # el tercero está borrado
    assert "robots" in rep and "404" in rep["robots"]


def test_ingest_captures_only_live_records_with_per_record_reuse(setup):
    # los otros 8 ListSets reales devuelven vacío en la prueba (fake_get); solo prensahistorica5 tiene datos
    st = V.ingest(setup, None)
    assert st == {"scanned": 2, "documents": 2, "reusable": 1, "consult_only": 1, "deleted": 1}
    docs = {r["stable_id"]: r for r in setup.execute("SELECT * FROM documents WHERE source='bvph'")}
    assert docs["oai:bvph:1"]["reuse"] == "reusable"
    assert docs["oai:bvph:2"]["reuse"] == "consult_only"
    assert docs["oai:bvph:1"]["url"] == "https://ejemplo.invalid/registro.do?id=oai:bvph:1"
    from kb.store import document_text
    doc1 = setup.execute("SELECT id FROM documents WHERE stable_id='oai:bvph:1'").fetchone()
    assert "title: Diario Ficticio de Madrid" in document_text(setup, doc1["id"])


def test_ingest_idempotent_and_dedupes_across_sets(setup):
    st1 = V.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    st2 = V.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1
    assert st2["documents"] == 0  # nada nuevo: mismo contenido, mismo hash
