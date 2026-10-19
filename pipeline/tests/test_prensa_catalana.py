"""prensa_catalana.py con HTTP (OAI-PMH + robots.txt) simulado. Identificadores y títulos son FICTICIOS; sin red."""
import pytest

from kb import http as kbhttp
from kb.sources import prensa_catalana as P

CFG = {"prensa_catalana": {"validated": True, **P.DEFAULT_CFG}}

ROBOTS_BLOCKED = "User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nDisallow:\n"


def rec_xml(ident: str, title: str, rights: str) -> str:
    return (f'<record><header><identifier>{ident}</identifier><datestamp>2020-01-01</datestamp></header>'
            f'<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{title}</dc:title><dc:rights>{rights}</dc:rights>'
            f'<dc:identifier>https://ejemplo.invalid/cdm/ref/{ident}</dc:identifier>'
            f'</oai_dc:dc></metadata></record>')


def envelope(body: str) -> str:
    return (f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<ListRecords>{body}</ListRecords></OAI-PMH>')


RELEVANT = rec_xml("oai:mdc:felippedrell/0", "Allegro y Recitado de la zarzuela Ficticia", "http://creativecommons.org/publicdomain/mark/1.0/")
IRRELEVANT = rec_xml("oai:mdc:felippedrell/1", "Cançó Ficticia per a piano", "http://rightsstatements.org/vocab/InC/1.0/")


def fake_get(self, url, params=None, headers=None, **kw):
    p = params or {}
    if not p:  # robots.txt (lo pide _catalog.robots_report sin parámetros)
        if url in (P.CATALONICA.rstrip("/") + "/robots.txt", P.ARCA.rstrip("/") + "/robots.txt"):
            return ROBOTS_BLOCKED
        raise AssertionError(url)
    if p.get("verb") == "Identify":
        return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><Identify>'
                '<repositoryName>MDC Ficticia</repositoryName></Identify></OAI-PMH>')
    if p.get("verb") == "ListRecords":
        if p.get("set") == "felippedrell":
            return envelope(RELEVANT + IRRELEVANT)
        return envelope("")
    raise AssertionError((url, p))


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    return con


def test_phase0_reports_catalonica_and_arca_blocked_and_mdc_sample(setup):
    rep = P.phase0(sample=5)
    assert rep["endpoints"]["catalonica"]["blocked_for_us"] is True
    assert rep["endpoints"]["arca"]["blocked_for_us"] is True
    assert rep["validated"] is True
    assert rep["endpoints"]["mdc"]["sample_n"] == 1  # solo el registro con "zarzuela" pasa el filtro TOPIC
    assert "zarzuela" in rep["endpoints"]["mdc"]["sample"][0]["title"].lower()


def test_ingest_keeps_only_topic_matches(setup):
    st = P.ingest(setup, None)
    assert st["scanned"] == 2 and st["matched"] == 1 and st["documents"] == 1
    row = setup.execute("SELECT * FROM documents WHERE source='prensa_catalana'").fetchone()
    assert row["stable_id"] == "oai:mdc:felippedrell/0"
    assert row["reuse"] == "reusable"
    assert row["url"] == "https://ejemplo.invalid/cdm/ref/oai:mdc:felippedrell/0"


def test_ingest_idempotent(setup):
    P.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    st2 = P.ingest(setup, None)
    assert st2["documents"] == 0
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1
