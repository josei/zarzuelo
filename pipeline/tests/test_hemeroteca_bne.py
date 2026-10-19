"""hemeroteca_bne.py: fase 0 bloqueada (HTTP simulado) + ingesta desde fichero "descargado a mano" (FICTICIO).
Sin red en ningún caso."""
import gzip
import zipfile

import httpx
import pytest

from kb import http as kbhttp
from kb.sources import hemeroteca_bne as H

CSV_TEXT = ("titulo,fecha,lugar,ocr_confianza\n"
            "La Estrella Ficticia,1899-04-02,Madrid,0.87\n"
            "Crónica: éxito de la zarzuela Ficticia en el Teatro Inventado,1899-04-03,Madrid,92\n"
            "Anuncio de telas y ultramarinos,1899-04-03,Madrid,0.5\n")
CFG = {"hemeroteca_bne": {"validated": False, "license": "CC0-1.0", "reuse": "consult_only"}}


def test_phase0_reports_ckan_tls_and_cloudflare_blocked(monkeypatch):
    def fake_get(self, url, params=None, headers=None, use_cache=True, **kw):
        if url == H.CKAN_API:
            raise httpx.ConnectError("certificate verify failed: unable to get local issuer certificate")
        if url == H.ROBOTS:
            return "User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
        if url == H.FILES["texto_completo"]:
            raise httpx.HTTPStatusError("blocked", request=httpx.Request("GET", url),
                                        response=httpx.Response(403, headers={"server": "cloudflare"}))
        raise AssertionError(url)

    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    rep = H.phase0(sample=5)
    assert rep["validated"] is False
    assert "ConnectError" in rep["ckan_blocked"]
    assert rep["robots_allows"] is False
    assert "cloudflare" in rep["blocked"].lower()


@pytest.fixture
def local_data(tmp_data):
    d = tmp_data / "hemeroteca_bne"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def fixed_sources(monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)


def test_ingest_reads_manual_csv_filters_topic_and_stores_ocr_conf(con, local_data):
    (local_data / "texto_completo.csv").write_text(CSV_TEXT, encoding="utf-8")
    st = H.ingest(con, None)
    assert st == {"datasets_found": 1, "scanned": 3, "matched": 1, "documents": 1}
    doc = con.execute("SELECT * FROM documents WHERE source='hemeroteca_bne'").fetchone()
    assert doc["license"] == "CC0-1.0" and doc["reuse"] == "consult_only"
    p = con.execute("SELECT ocr_conf FROM passages WHERE document_id=?", (doc["id"],)).fetchone()
    assert p["ocr_conf"] == pytest.approx(0.92)  # "92" (sin decimales) se normaliza a fracción


def test_ingest_reads_gz_and_zip(con, local_data):
    with gzip.open(local_data / "listado_titulos.csv.gz", "wt", encoding="utf-8") as f:
        f.write(CSV_TEXT)
    zpath = local_data / "texto_completo.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("x.csv", CSV_TEXT)
    st = H.ingest(con, None)
    assert st["datasets_found"] == 2
    assert st["matched"] == 2  # un acierto por cada uno de los dos ficheros (mismo contenido)
    assert con.execute("SELECT count(*) FROM documents WHERE source='hemeroteca_bne'").fetchone()[0] == 2


def test_ingest_without_local_files_is_a_noop(con, tmp_data):
    assert H.ingest(con, None) == {"datasets_found": 0, "scanned": 0, "matched": 0, "documents": 0}


def test_ingest_idempotent(con, local_data):
    (local_data / "texto_completo.csv").write_text(CSV_TEXT, encoding="utf-8")
    H.ingest(con, None)
    n1 = con.execute("SELECT count(*) FROM documents").fetchone()[0]
    st2 = H.ingest(con, None)
    assert st2["documents"] == 0
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == n1
