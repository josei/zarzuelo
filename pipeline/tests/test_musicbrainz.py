"""MusicBrainz con HTTP simulado. TODOS los datos son FICTICIOS (MBIDs, títulos, nombres)."""
import json

import pytest

from kb import http as kbhttp
from kb.sources import musicbrainz as mb

CFG = {"musicbrainz": {"validated": True, **mb.DEFAULT_CFG}}
A_COMP, A_PERF = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
W_LINKED, W_TITLE = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
R1 = "00000000-0000-0000-0000-0000000000c1"


def fake_get(self, url, params=None, headers=None, **kw):
    p = params or {}
    if url.endswith("/work") and p.get("artist") == A_COMP:
        return json.dumps({"work-count": 2, "works": [{"id": W_LINKED, "title": "Obra Ficticia Uno"},
                                                      {"id": W_TITLE, "title": "Obra Ficticia Dos"}]})
    if url.endswith(f"/work/{W_LINKED}") or url.endswith(f"/work/{W_TITLE}"):
        return json.dumps({"id": url.rsplit("/", 1)[1], "title": "Obra Ficticia", "type": "Zarzuela", "languages": ["spa"],
                           "relations": [{"type": "composer", "artist": {"id": A_COMP, "name": "Compositor Ficticio"}},
                                         {"type": "writer", "artist": {"id": A_PERF, "name": "Ignorado"}}]})
    if url.endswith("/recording") and p.get("work") == W_LINKED:
        return json.dumps({"recording-count": 1, "recordings": [{
            "id": R1, "title": "Grabación Ficticia", "first-release-date": "1960-05",
            "artist-credit": [{"name": "x", "joinphrase": "", "artist": {"id": A_PERF, "name": "Intérprete Ficticio"}}]}]})
    if url.endswith("/recording"):
        return json.dumps({"recording-count": 0, "recordings": []})
    raise AssertionError(f"petición inesperada {url} {p}")


@pytest.fixture
def setup(con, monkeypatch):
    monkeypatch.setattr(kbhttp, "load_sources", lambda: CFG)
    monkeypatch.setattr(kbhttp.Http, "get", fake_get)
    monkeypatch.setattr(kbhttp.Http, "_wait", lambda self: None)
    ins = "INSERT INTO entities(id,type,label,external_ids,provisional) VALUES(?,?,?,?,0)"
    con.execute(ins, ("Q1", "person", "Compositor Ficticio", json.dumps({"musicbrainz_artist": A_COMP})))
    con.execute(ins, ("Q2", "work", "Obra Ficticia Uno", json.dumps({"musicbrainz_work": W_LINKED})))
    con.execute(ins, ("Q3", "work", "Obra Ficticia Dos", "{}"))
    con.execute(ins, ("Q4", "work", "Obra Sin Compositor", "{}"))
    for w in ("Q2", "Q3", "Q4"):
        if w != "Q4":
            con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES(?,?,?,?,?,?)",
                        (f"c{w}", w, "composer", '"Q1"', "entity", "structured"))
    con.commit()
    return con


def test_ingest_links_by_id_and_by_title(setup):
    st = mb.ingest(setup, None)
    assert st["works"] == 2 and st["linked_by_title"] == 1 and st["recordings"] == 1
    x = json.loads(setup.execute("SELECT external_ids FROM entities WHERE id='Q3'").fetchone()[0])
    assert x["musicbrainz_work"] == W_TITLE
    # la obra sin compositor no se enlaza
    assert "musicbrainz_work" not in json.loads(setup.execute("SELECT external_ids FROM entities WHERE id='Q4'").fetchone()[0])


def test_claims_have_evidence_and_reuse_corpus_entities(setup):
    mb.ingest(setup, None)
    rows = {(r["subject_id"], r["property"]): r for r in setup.execute("SELECT * FROM claims")}
    assert json.loads(rows[(f"MBR:{R1}", "recording_of")]["value_json"]) == "Q2"
    assert json.loads(rows[(f"MBR:{R1}", "recording_date")]["value_json"]) == "1960-05"
    assert json.loads(rows[("Q2", "composer")]["value_json"]) == "Q1"  # compositor del corpus, no entidad MBA:
    assert json.loads(rows[(f"MBR:{R1}", "performer")]["value_json"]) == f"MBA:{A_PERF}"
    n = setup.execute("SELECT count(*) FROM claims c WHERE source_kind='structured' AND id NOT IN (SELECT claim_id FROM claim_evidence)").fetchone()[0]
    assert n == 2  # solo los 'composer' sembrados a mano en la prueba (Q2, Q3)
    d = setup.execute("SELECT license, reuse FROM documents WHERE source='musicbrainz' LIMIT 1").fetchone()
    assert (d[0], d[1]) == ("CC0-1.0", "reusable")


def test_limit_and_idempotent(setup):
    assert mb.ingest(setup, 1)["works"] == 1
    mb.ingest(setup, None)
    n1 = setup.execute("SELECT count(*) FROM claims").fetchone()[0]
    mb.ingest(setup, None)
    assert setup.execute("SELECT count(*) FROM claims").fetchone()[0] == n1


def test_ambiguous_title_not_linked(setup, monkeypatch):
    def amb(self, url, params=None, headers=None, **kw):
        if url.endswith("/work") and (params or {}).get("artist") == A_COMP:
            return json.dumps({"work-count": 2, "works": [{"id": W_TITLE, "title": "Obra Ficticia Dos"},
                                                          {"id": W_LINKED, "title": "obra ficticia dos"}]})
        return fake_get(self, url, params, headers)
    monkeypatch.setattr(kbhttp.Http, "get", amb)
    assert mb.ingest(setup, None)["linked_by_title"] == 0
