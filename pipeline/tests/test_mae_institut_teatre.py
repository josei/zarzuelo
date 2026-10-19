"""MAE - Institut del Teatre con HTTP simulado (OAI-PMH). TODOS los datos son FICTICIOS."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import mae_institut_teatre as mae

CFG = {"mae_institut_teatre": {"validated": True, **mae.DEFAULT_CFG}}

PAGE1 = """<?xml version='1.0' encoding='UTF-8'?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords>
<record><header><identifier>oai:escena.cdmae.cat:ficticio/1</identifier><datestamp>2024-01-01T00:00:00Z</datestamp></header>
<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Obra Ficticia Uno. 1950</dc:title>
<dc:subject>Sarsuela</dc:subject>
<dc:date>1950</dc:date>
<dc:identifier>http://hdl.handle.net/ficticio/1</dc:identifier>
<dc:rights>Acceso de estudio o investigación con cita (ficticio)</dc:rights>
</oai_dc:dc></metadata></record>
<record><header><identifier>oai:escena.cdmae.cat:ficticio/2</identifier><datestamp>2024-01-02T00:00:00Z</datestamp></header>
<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Don Carlo</dc:title>
<dc:subject>Fotografies d'escena</dc:subject>
<dc:identifier>http://hdl.handle.net/ficticio/2</dc:identifier>
</oai_dc:dc></metadata></record>
<resumptionToken completeListSize="3" cursor="0">TOKEN1</resumptionToken>
</ListRecords></OAI-PMH>"""

PAGE2 = """<?xml version='1.0' encoding='UTF-8'?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords>
<record><header><identifier>oai:escena.cdmae.cat:ficticio/3</identifier><datestamp>2024-01-03T00:00:00Z</datestamp></header>
<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Obra Ficticia Dos. 1960</dc:title>
<dc:subject>Zarzuela</dc:subject>
<dc:date>1960</dc:date>
<dc:identifier>http://hdl.handle.net/ficticio/3</dc:identifier>
</oai_dc:dc></metadata></record>
<resumptionToken completeListSize="3" cursor="2"/>
</ListRecords></OAI-PMH>"""


def fake_get(self, url, params=None, headers=None, **kw):
    p = params or {}
    if p.get("resumptionToken") == "TOKEN1":
        return PAGE2
    if p.get("verb") == "ListRecords":
        return PAGE1
    raise AssertionError(f"petición inesperada {url} {p}")


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES(?,?,?,?,?,0)",
                ("Q1", "work", "Obra Ficticia Uno", "[]", "{}"))
    con.commit()
    return con


def test_ingest_filters_by_genre_subject_and_links(setup):
    st = mae.ingest(setup, None)
    assert st["seen"] == 3 and st["matched"] == 2  # "Don Carlo" (Fotografies) queda fuera
    assert st["documents"] == 2 and st["linked"] == 1  # solo "Obra Ficticia Uno" coincide con el corpus
    ids = {r["stable_id"] for r in setup.execute("SELECT stable_id FROM documents")}
    assert ids == {"oai:escena.cdmae.cat:ficticio/1", "oai:escena.cdmae.cat:ficticio/3"}


def test_license_and_limit(setup):
    st = mae.ingest(setup, 1)
    assert st["matched"] == 1
    d = setup.execute("SELECT license, reuse FROM documents LIMIT 1").fetchone()
    assert d["reuse"] == "consult_only"


def test_idempotent(setup):
    mae.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    mae.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1
