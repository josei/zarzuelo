"""Gallica con HTTP simulado. TODOS los datos (arks, títulos, nombres) son FICTICIOS; sin red."""
import pytest

from kb import http as kbhttp
from kb.sources import gallica as ga

CFG = {"gallica": {"validated": True, "endpoint": "https://gallica.bnf.fr/SRU",
                    "license": "per-object (dc:rights)", "reuse": "consult_only", "rate_limit_per_s": 1000,
                    "queries": ["ficticia"], "page_size": 50}}


def _records_xml(records: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/">
  <srw:numberOfRecords>{len(records)}</srw:numberOfRecords>
  <srw:records>{"".join(records)}</srw:records>
</srw:searchRetrieveResponse>"""


def _record(ark, title, creator=None, rights=("domaine public", "public domain"), lang="fre"):
    creator_xml = f"<dc:creator>{creator}</dc:creator>" if creator else ""
    rights_xml = "".join(f"<dc:rights>{r}</dc:rights>" for r in rights)
    return (f"<srw:record><srw:recordData><oai_dc:dc>"
            f"<dc:title>{title}</dc:title>{creator_xml}"
            f"<dc:identifier>https://gallica.bnf.fr/ark:/12148/{ark}</dc:identifier>"
            f"<dc:language>{lang}</dc:language>{rights_xml}"
            f"</oai_dc:dc></srw:recordData></srw:record>")


ITEM1 = _record("ark0001ficticio", "Obra Ficticia Uno", creator="Ficticio, Pepe (1850-1920)")
ITEM2 = _record("ark0002ficticio", "Obra Ficticia Dos", rights=("Gallica",))  # no es dominio público
XML = _records_xml([ITEM1, ITEM2])


class FakeResp:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class FakeHttp:
    """Sustituye por completo a kb.http.Http: get()/client.get()/client.head() simulados, sin red."""

    def __init__(self):
        self.cfg = CFG["gallica"]
        self.client = self
        self.calls: list[str] = []

    def _wait(self):
        pass

    def get(self, url, params=None, headers=None, use_cache=True):
        self.calls.append(url)
        if url.endswith("/robots.txt"):  # http.client.get(...) en _robots(): espera un objeto con .text
            return FakeResp(text="User-agent: *\nCrawl-delay: 5\nDisallow: /services/engine/search/sru?*\n")
        p = params or {}
        assert url == self.cfg["endpoint"]
        if p.get("query") != "ficticia":
            return _records_xml([])
        if int(p.get("startRecord", 1)) > 2:
            return _records_xml([])
        return XML

    def head(self, url, headers=None):
        return FakeResp(200 if "ark0001ficticio" in url else 404)


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(ga, "Http", lambda name, cache=False, cfg=None: FakeHttp())
    ins = "INSERT INTO entities(id,type,label,external_ids,provisional) VALUES(?,?,?,?,0)"
    con.execute(ins, ("P1", "person", "Pepe Ficticio", "{}"))
    con.execute(ins, ("W1", "work", "Obra Ficticia Uno", "{}"))
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES"
                "('c1','W1','composer','\"P1\"','entity','structured')")
    con.commit()
    return con


def test_reuse_for():
    assert ga.reuse_for(["domaine public", "public domain"]) == ga.Reuse.reusable
    assert ga.reuse_for(["Public Domain"]) == ga.Reuse.reusable  # insensible a mayúsculas
    assert ga.reuse_for(["Gallica"]) == ga.Reuse.consult_only
    assert ga.reuse_for([]) == ga.Reuse.consult_only


def test_ark_of():
    assert ga.ark_of(["https://gallica.bnf.fr/ark:/12148/bpt6k123456x"]) == "bpt6k123456x"
    assert ga.ark_of(["NUMAUD-123", "otra cosa"]) is None


def test_ingest_captures_links_and_classifies_rights(setup):
    st = ga.ingest(setup, None)
    assert st == {"documents": 2, "seen": 2, "no_ark": 0, "linked_objects": 1, "links": 2, "reusable": 1,
                  "consult_only": 1, "iiif": 1}
    rows = {r["stable_id"]: r for r in setup.execute("SELECT * FROM documents")}
    assert rows["ark0001ficticio"]["reuse"] == "reusable"
    assert rows["ark0002ficticio"]["reuse"] == "consult_only"
    text = "".join(r["text"] for r in setup.execute("SELECT text FROM passages"))
    assert "Creador: Ficticio, Pepe (1850-1920)" in text
    assert "Derechos: domaine public, public domain" in text


def test_ingest_links_person_and_work(setup):
    ga.ingest(setup, None)
    n = setup.execute("SELECT count(*) FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id "
                      "WHERE d.stable_id='ark0001ficticio'").fetchone()[0]
    assert n == 2  # P1 (compositor, via nombre) y W1 (obra, via título+compositor)
    n2 = setup.execute("SELECT count(*) FROM doc_subjects ds JOIN documents d ON d.id=ds.document_id "
                       "WHERE d.stable_id='ark0002ficticio'").fetchone()[0]
    assert n2 == 0  # sin creador, sin compositor que lo respalde: no se enlaza


def test_ingest_respects_limit(setup):
    assert ga.ingest(setup, 1)["seen"] == 1


def test_ingest_idempotent(setup):
    ga.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM documents").fetchone()[0]
    ga.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == n1


def test_phase0_reports_robots_and_sample(monkeypatch, tmp_path):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(ga, "Http", lambda name, cache=False, cfg=None: FakeHttp())
    monkeypatch.setattr(ga.kbdb, "DATA", tmp_path)
    r = ga.phase0(2)
    assert r["sample"] == 2 and r["sample_arks"] == ["ark0001ficticio", "ark0002ficticio"]
    assert r["reuse_in_sample"] == {"reusable": 1, "consult_only": 1}
    assert r["iiif_manifest_status_first_item"] == 200
    assert (tmp_path / "phase0" / "gallica_sample.xml").exists()  # nunca en el data/ real (ver conftest)


def test_wrapper_robots_keys_present(monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(ga, "Http", lambda name, cache=False, cfg=None: FakeHttp())
    http = FakeHttp()
    http.text = "User-agent: *\nCrawl-delay: 5\nDisallow: /services/engine/search/sru?*\n"

    def fake_get(url, **kw):
        return type("R", (), {"text": http.text, "status_code": 200})()
    http.get = fake_get
    r = ga._robots(http)
    assert r["crawl_delay"] == 5 and r["disallow_services_sru"] is True and r["sru_path_used_is_disallowed"] is False
