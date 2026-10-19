"""_oai.py con HTTP simulado. Todos los identificadores y títulos son FICTICIOS; sin red."""
import pytest

from kb import http as kbhttp
from kb.sources import _oai as O

BASE = "https://ejemplo.invalid/oai"

REC1 = ('<record><header><identifier>oai:ejemplo:1</identifier><datestamp>2020-01-01</datestamp>'
        '<setSpec>teatre</setSpec></header><metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Zarzuela Ficticia en un acto</dc:title>'
        '<dc:rights>http://creativecommons.org/publicdomain/mark/1.0/</dc:rights>'
        '</oai_dc:dc></metadata></record>')
REC2_DELETED = ('<record><header status="deleted"><identifier>oai:ejemplo:2</identifier>'
                '<datestamp>2020-01-02</datestamp></header></record>')
REC3 = ('<record><header><identifier>oai:ejemplo:3</identifier><datestamp>2020-01-03</datestamp></header>'
        '<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Poema Ficticio sin relación</dc:title>'
        '<dc:rights>http://rightsstatements.org/vocab/InC/1.0/</dc:rights>'
        '</oai_dc:dc></metadata></record>')


def envelope(body: str, token: str | None = None) -> str:
    rt = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return (f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<responseDate>2020-01-01T00:00:00Z</responseDate>'
            f'<ListRecords>{body}{rt}</ListRecords></OAI-PMH>')


def fake_get(self, url, params=None, headers=None, **kw):
    p = params or {}
    assert url == BASE
    if p.get("resumptionToken") == "tok2":
        return envelope(REC3)
    if p.get("verb") == "ListRecords":
        return envelope(REC1 + REC2_DELETED, token="tok2")
    raise AssertionError(p)


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)


def http():
    return kbhttp.Http("x", cfg={"rate_limit_per_s": 1000})


def test_pagination_and_deleted():
    recs = list(O.list_records(http(), BASE, "oai_dc", set_spec="teatre"))
    assert [r["identifier"] for r in recs] == ["oai:ejemplo:1", "oai:ejemplo:2", "oai:ejemplo:3"]
    assert recs[1]["deleted"] is True and recs[1]["dc"] == {}
    assert recs[0]["dc"]["title"] == ["Zarzuela Ficticia en un acto"]
    assert recs[0]["sets"] == ["teatre"]


def test_limit_stops_before_resumption():
    recs = list(O.list_records(http(), BASE, "oai_dc", limit=1))
    assert len(recs) == 1 and recs[0]["identifier"] == "oai:ejemplo:1"


def test_render_dc():
    rec = next(O.list_records(http(), BASE, "oai_dc", limit=1))
    text = O.render_dc(rec)
    assert text.splitlines()[0] == "OAI oai:ejemplo:1"
    assert "title: Zarzuela Ficticia en un acto" in text


def test_reuse_for():
    assert O.reuse_for(["http://creativecommons.org/publicdomain/mark/1.0/"])[0].value == "reusable"
    assert O.reuse_for(["http://rightsstatements.org/vocab/InC/1.0/"])[0].value == "consult_only"
    r, lic = O.reuse_for(None)
    assert r.value == "consult_only" and lic == "sin declarar"


def test_matches_topic():
    assert O.matches_topic({"dc": {"title": ["Zarzuela Ficticia en un acto"]}})
    assert O.matches_topic({"dc": {"title": ["¡¡La Por!! : sarsuela en un acte"]}})
    assert O.matches_topic({"dc": {"title": ["Comedia cómico-lírica Ficticia"]}})
    assert not O.matches_topic({"dc": {"title": ["Poema Ficticio sin relación"]}})


def test_error_raises(monkeypatch):
    def err_get(self, url, params=None, headers=None, **kw):
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                '<error code="badArgument">falta metadataPrefix</error></OAI-PMH>')
    monkeypatch.setattr(kbhttp.Http, "get", err_get)
    with pytest.raises(O.OAIError):
        O.request(http(), BASE, verb="Identify")


def test_no_records_match_is_not_an_error(monkeypatch):
    def empty_get(self, url, params=None, headers=None, **kw):
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                '<error code="noRecordsMatch">sin resultados</error></OAI-PMH>')
    monkeypatch.setattr(kbhttp.Http, "get", empty_get)
    assert list(O.list_records(http(), BASE, "oai_dc")) == []


def test_identify(monkeypatch):
    def idn_get(self, url, params=None, headers=None, **kw):
        assert (params or {}).get("verb") == "Identify"
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><Identify>'
                '<repositoryName>Repo Ficticio</repositoryName><baseURL>https://ejemplo.invalid/oai</baseURL>'
                '</Identify></OAI-PMH>')
    monkeypatch.setattr(kbhttp.Http, "get", idn_get)
    assert O.identify(http(), BASE)["repositoryName"] == "Repo Ficticio"
