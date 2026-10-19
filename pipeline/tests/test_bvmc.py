"""BVMC con HTTP simulado (reto Cloudflare en /sparql, confirmado en fase 0 real). Texto FICTICIO."""
import httpx
import pytest

from kb import http as kbhttp
from kb.sources import bvmc

CFG = {"bvmc": {"validated": True, **bvmc.DEFAULT_CFG}}
CF_CHALLENGE = "<html><head><title>Just a moment...</title></head><body>cf-mitigated</body></html>"


class _Resp:
    def __init__(self, status_code, text, headers=None):
        self.status_code, self.text, self.headers = status_code, text, headers or {}


def fake_client_get(self, url, params=None, headers=None, **kw):
    if "sparql" in url:
        return _Resp(403, CF_CHALLENGE)
    return _Resp(200, "<html>BVMC.Labs ficticio</html>", {"content-type": "text/html;charset=UTF-8"})


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    monkeypatch.setattr(httpx.Client, "get", fake_client_get)
    return con


def test_phase0_detects_cloudflare_block_without_bypassing(setup):
    r = bvmc.phase0()
    assert r["blocked"] is True and "Cloudflare" in r["blocked_detail"]
    assert r["resource_pages_reachable"] is True and r["resource_pages_expose_rdf"] is False
    assert r["validated_recommendation"] is False


def test_ingest_does_not_touch_network_or_db(setup):
    st = bvmc.ingest(setup, 50)
    assert st == {"blocked": True, "documents": 0, "attempted": 0}
    assert setup.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
