"""Pruebas de internet_archive.py con HTTP simulado. Todos los datos son FICTICIOS; sin red."""
import json

from kb import http as kbhttp
from kb.sources import internet_archive as ia

CFG = {"internet_archive": {"validated": True, **ia.DEFAULT}}

TEXT_META = {"metadata": {
    "identifier": "texto-ficticio-1", "title": "Obra Ficticia", "creator": ["Compositor Ficticio"],
    "date": "1899", "language": "spa", "mediatype": "texts", "collection": ["americana"],
    "description": "Libreto ficticio de una zarzuela en un acto."},
    "files": [
        {"name": "texto-ficticio-1_djvu.txt", "source": "derivative", "format": "DjVuTXT", "size": "120"},
        {"name": "texto-ficticio-1.pdf", "source": "original", "format": "Text PDF", "size": "456789"},
    ]}

AUDIO_META = {"metadata": {
    "identifier": "78-ficticio-1", "title": "Obra Ficticia", "creator": ["Interprete Ficticio", "Compositor Ficticio"],
    "date": "1930", "mediatype": "audio", "collection": ["georgeblood"], "genre": "Zarzuela",
    "subject": ["78rpm", "Zarzuela"]},
    "files": [{"name": "78-ficticio-1.flac", "source": "original", "format": "Flac", "size": "9999999"}]}


def fake_get(self, url, params=None, headers=None, **kw):
    if url.endswith("/robots.txt"):
        return "User-agent: *\nDisallow: /control/\nDisallow: /report/"
    if url == ia.SEARCH:
        q = params["q"]
        if "georgeblood" in q and "subject" in q:
            return json.dumps({"response": {"docs": [{"identifier": "78-ficticio-1"}]}})
        if "subject" in q:
            return json.dumps({"response": {"docs": [{"identifier": "texto-ficticio-1"}]}})
        return json.dumps({"response": {"docs": []}})
    if url == f"{ia.METADATA}/texto-ficticio-1":
        return json.dumps(TEXT_META)
    if url == f"{ia.METADATA}/78-ficticio-1":
        return json.dumps(AUDIO_META)
    if url == f"{ia.DOWNLOAD}/texto-ficticio-1/texto-ficticio-1_djvu.txt":
        return "Texto OCR ficticio de la obra, acto primero."
    raise AssertionError((url, params))


def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(ia, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    return con


def seed(con):
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('P1','person','Compositor Ficticio','{}')")
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('W1','work','Obra Ficticia','{}')")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) "
                "VALUES('c1','W1','composer','\"P1\"','entity','asserted','structured')")
    con.commit()


def test_files_of_only_originals_not_derivatives():
    files = ia.files_of(TEXT_META)
    assert [f["name"] for f in files] == ["texto-ficticio-1.pdf"]  # el djvu.txt (OCR) no es "original"
    assert files[0]["url"] == f"{ia.DOWNLOAD}/texto-ficticio-1/texto-ficticio-1.pdf"


def test_license_of_defaults_to_consult_only():
    assert ia._license_of({})[1].value == "consult_only"
    assert ia._license_of({"licenseurl": "https://creativecommons.org/licenses/by/4.0/"})[1].value == "reusable"


def test_ingest_downloads_ocr_text_but_not_audio_or_scans(con, monkeypatch):
    setup(con, monkeypatch)
    seed(con)
    stats = ia.ingest(con, None)
    assert stats["documents"] == 2 and stats["linked"] == 2 and stats["skipped_unlinked"] == 0
    assert stats["texts"] == 1 and stats["audio"] == 1
    assert stats["files_referenced"] == 2  # 1 pdf (texts) + 1 flac (audio): solo referencia, nunca descargados
    texts = [r["text"] for r in con.execute("SELECT text FROM passages")]
    joined = "\n".join(texts)
    assert "Texto OCR ficticio de la obra" in joined  # el OCR SI se descarga (es texto)
    assert "texto-ficticio-1.pdf" in joined and "78-ficticio-1.flac" in joined  # solo referenciados
    subs = {r["entity_id"] for r in con.execute("SELECT * FROM doc_subjects")}
    assert subs == {"W1"}
    assert ia.ingest(con, None)["documents"] == 0  # idempotente


def test_phase0(con, monkeypatch):
    setup(con, monkeypatch)
    rep = ia.phase0(2)
    assert rep["source"] == "internet_archive" and rep["n_sample"] == 2
    idents = {r["identifier"] for r in rep["sample"]}
    assert idents == {"texto-ficticio-1", "78-ficticio-1"}
    by_id = {r["identifier"]: r for r in rep["sample"]}
    assert by_id["texto-ficticio-1"]["ocr_bytes"] == 120
    assert by_id["78-ficticio-1"]["ocr_bytes"] is None
    assert rep["robots"]["allowed"] is True  # /advancedsearch.php no está vetado
