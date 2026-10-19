"""DAHR: fuente bloqueada (403 por User-Agent, confirmado en fase 0 real). No hace red; nada que simular."""
from kb.sources import dahr


def test_phase0_reports_blocked_without_network():
    r = dahr.phase0()
    assert r["blocked"] is True and r["sample"] == [] and r["validated_recommendation"] is False


def test_ingest_does_not_touch_network_or_db(con):
    st = dahr.ingest(con, 50)
    assert st == {"blocked": True, "documents": 0, "attempted": 0}
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
