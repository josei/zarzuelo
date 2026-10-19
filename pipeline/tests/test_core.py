"""Todos los datos de estas pruebas son FICTICIOS (AGENTS.md)."""
import json

import pytest

from kb import resolve as R
from kb.extract import extract_claims, verify_claim
from kb.generate import build_quiz
from kb.llm.core import UsageCapReached
from kb.store import capture, segment, split_spans
from kb.util import edtf_interval, is_edtf, norm
from kb.validate import validate_claims, validate_segment
from kb.models import Segment

from conftest import make_llm

TEXT = "«La obra de ejemplo», con música de Ana Ficticia, se estrenó en el Teatro Imaginario en 1890.\nSegundo párrafo ficticio sin hechos."


def seed(con):
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','La obra de ejemplo')")
    doc, _ = capture(con, source="wikipedia_es", url="https://ejemplo.invalid/w?oldid=1", stable_id="1", content=TEXT, license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    return doc


def test_edtf():
    assert is_edtf("1894-02-17") and is_edtf("1890~") and is_edtf("189X") and is_edtf("[1893..1895]")
    assert not is_edtf("ayer")
    assert edtf_interval("189X") == (1890, 1899) and edtf_interval("1890~") == (1885, 1895)


def test_segment_offsets_exact(con):
    doc = seed(con)
    for p in con.execute("SELECT * FROM passages WHERE document_id=?", (doc,)):
        assert TEXT[p["char_start"]:p["char_end"]] == p["text"]
    assert segment(con, doc) == 0  # idempotente


def test_capture_immutable(con):
    d1, n1 = capture(con, source="s", url="u", stable_id="1", content="a", license="x", reuse="reusable")
    d2, n2 = capture(con, source="s", url="u", stable_id="1", content="a", license="x", reuse="reusable")
    d3, n3 = capture(con, source="s", url="u", stable_id="1", content="b", license="x", reuse="reusable")
    assert d1 == d2 and n1 and not n2 and n3 and d3 != d1


def test_verify_claim_rejects_non_literal():
    p = "con música de Ana Ficticia, se estrenó en 1890."
    ok = {"property": "composer", "value": "Ana Ficticia", "quote": "con música de Ana Ficticia", "hedge": None, "status": "asserted"}
    assert verify_claim(ok, p)[0]
    assert not verify_claim({**ok, "quote": "con musica de Ana Ficticia"}, p)[0]       # no literal
    assert not verify_claim({**ok, "value": "Otra Persona"}, p)[0]                    # valor ausente de la cita
    assert not verify_claim({**ok, "property": "inventada"}, p)[0]
    d = {"property": "premiere_date", "value": "1890", "quote": "en 1890", "hedge": None, "status": "asserted"}
    assert verify_claim(d, p)[0]
    assert not verify_claim({**d, "value": "1895"}, p)[0]


def test_extract_keeps_only_verified_and_caches(con):
    doc = seed(con)
    good = {"property": "composer", "value": "Ana Ficticia", "quote": "con música de Ana Ficticia", "hedge": None, "status": "asserted"}
    bad = {"property": "premiere_venue", "value": "Teatro Falso", "quote": "en el Teatro Falso", "hedge": None, "status": "asserted"}
    llm = make_llm(con, lambda s, u: {"claims": [good, bad] if "con música" in u else []})
    s = extract_claims(con, llm, doc)
    assert s["kept"] == 1 and s["discarded"] == 1
    assert con.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    calls = llm.backends["mock"].calls
    extract_claims(con, llm, doc)  # reanudable: no repite llamadas
    assert llm.backends["mock"].calls == calls


def test_conflicts_and_refines(con):
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia')")
    for cid, v in (("C1", "1894"), ("C2", "1895"), ("C3", "1894-02-17")):
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES(?,?,?,?,?,?,?)",
                    (cid, "W1", "premiere_date", json.dumps(v), "edtf", "asserted", "structured"))
    assert R.detect_conflicts(con) == 2  # 1895 choca con 1894 y con 1894-02-17
    rels = {(r["claim_a"], r["claim_b"]): r["relation"] for r in con.execute("SELECT * FROM claim_relations")}
    assert rels[("C1", "C3")] == "refines"
    assert con.execute("SELECT status FROM claims WHERE id='C2'").fetchone()[0] == "disputed"
    assert con.execute("SELECT status FROM claims WHERE id='C3'").fetchone()[0] == "disputed"


def test_validate_flags_missing_evidence_and_posthumous(con):
    con.executescript("""INSERT INTO entities(id,type,label) VALUES('W1','work','Obra'),('P1','person','Autor');
    INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES
      ('Cd','P1','death_date','"1880"','edtf','structured'),('Cp','W1','premiere_date','"1890"','edtf','structured'),
      ('Cc','W1','composer','"P1"','entity','structured');""")
    rules = {i["rule"] for i in validate_claims(con)}
    assert {"evidence", "posthumous"} <= rules


def test_segment_validator(con):
    doc = seed(con)
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES('C1','W1','premiere_date','\"1890\"','edtf','legendary','extracted')")
    ok = Segment(id="s", text="Se cuenta que se estrenó en 1890.", claim_ids=["C1"])
    assert validate_segment(con, ok, {"C1"})["ok"]
    assert "sin cita" in validate_segment(con, Segment(id="s", text="x"), set())["failures"]
    bad_num = Segment(id="s", text="Se cuenta que se estrenó en 1891.", claim_ids=["C1"])
    assert not validate_segment(con, bad_num, {"C1"})["ok"]
    no_hedge = Segment(id="s", text="Se estrenó en 1890.", claim_ids=["C1"])
    assert any("matiz" in f for f in validate_segment(con, no_hedge, {"C1"})["failures"])
    assert not validate_segment(con, ok, set())["ok"]  # id no entregado


def test_quiz_distractors_not_true(con):
    for i in range(6):
        con.execute("INSERT INTO entities(id,type,label) VALUES(?,?,?)", (f"P{i}", "person", f"Compositor ficticio {i}"))
        con.execute("INSERT INTO entities(id,type,label) VALUES(?,?,?)", (f"W{i}", "work", f"Obra ficticia {i}"))
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES(?,?,?,?,?,?,?,?)",
                    (f"Cc{i}", f"W{i}", "composer", json.dumps(f"P{i}"), "entity", "asserted", 0.9, "structured"))
    q = build_quiz(con, max_questions=10)
    assert q["questions"]
    for qu in q["questions"]:
        assert qu["claim_ids"] and len(set(qu["options"])) == len(qu["options"])
        w = con.execute("SELECT subject_id FROM claims WHERE id=?", (qu["claim_ids"][0],)).fetchone()[0]
        assert qu["options"][qu["answer_index"]] == f"Compositor ficticio {w[1:]}"


def test_claude_daily_cap(con):
    llm = make_llm(con, lambda s, u: {"claims": []})
    llm.cfg["claude"]["daily_call_cap"] = 0
    with pytest.raises(UsageCapReached):
        llm.run("extract_narratives", "x", None, engine="claude")


def test_norm():
    assert norm("  Música   de ÁNA ") == "musica de ana"


def test_resolve_splits_joint_authors(con):
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia')")
    doc, _ = capture(con, source="s", url="u", stable_id="1", content="con libreto de Ana Ficticia y Luis Inventado, estreno.", license="x", reuse="reusable")
    segment(con, doc)
    p = con.execute("SELECT * FROM passages").fetchone()
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,source_kind) VALUES('C1','W1','librettist',?, 'name','extracted')", (json.dumps("Ana Ficticia y Luis Inventado"),))
    con.execute("INSERT INTO claim_evidence VALUES('C1',?,'con libreto de Ana Ficticia y Luis Inventado',0,40)", (p["id"],))
    R.resolve_names(con)
    labels = sorted(r[0] for r in con.execute("SELECT e.label FROM claims c JOIN entities e ON e.id=json_extract(c.value_json,'$') WHERE c.property='librettist'"))
    assert labels == ["Ana Ficticia", "Luis Inventado"]


def test_verify_claim_respects_subject_type():
    p = "Nació en Madrid en 1850 y estrenó su obra en 1880."
    c = {"property": "premiere_date", "value": "1880", "quote": "en 1880", "hedge": None, "status": "asserted"}
    assert verify_claim(c, p, "work")[0]
    assert not verify_claim(c, p, "person")[0]  # una persona no tiene fecha de estreno


def test_clean_name_variants():
    assert R.clean_name("la ciudad de Huánuco", "birth_place") == ["la ciudad de Huánuco", "Huánuco"]
    assert R.clean_name("Lucca, Toscana", "birth_place")[-1] == "Lucca"
    assert R.clean_name("Ana Ficticia", "composer") == ["Ana Ficticia"]


def test_segment_validator_splits_joint_names_and_accepts_dispute_wording(con):
    """Datos FICTICIOS."""
    con.executescript("""INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia'),('P1','person','Ana Ficticia'),('P2','person','Luis Inventado');
    INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES
      ('C1','W1','librettist','"P1"','entity','asserted','structured'),('C2','W1','librettist','"P2"','entity','asserted','structured'),
      ('C3','W1','premiere_date','"1890"','edtf','disputed','extracted'),('C4','W1','premiere_date','"1895"','edtf','disputed','extracted');""")
    both = Segment(id="s", text="El libreto es de Ana Ficticia y Luis Inventado.", claim_ids=["C1", "C2"])
    assert validate_segment(con, both, {"C1", "C2"})["ok"]
    disputed = Segment(id="s", text="Las fuentes no coinciden: 1890 frente a 1895.", claim_ids=["C3", "C4"])
    assert validate_segment(con, disputed, {"C3", "C4"})["ok"]
    wrong = Segment(id="s", text="El libreto es de Ana Ficticia y Pedro Falso.", claim_ids=["C1", "C2"])
    assert not validate_segment(con, wrong, {"C1", "C2"})["ok"]


def test_source_families_count_wikimedia_once():
    from kb.util import source_family
    assert {source_family(x) for x in ("wikidata", "wikipedia_es", "wikipedia_ca", "wikisource_es", "commons")} == {"wikimedia"}
    assert source_family("musicbrainz") == "musicbrainz" and source_family("bne_persona") == "bne"


def test_confidence_rewards_independent_families_not_copies(con):
    """Datos FICTICIOS: Wikidata + Wikipedia (misma familia) no suben la confianza; Wikidata + MusicBrainz (independientes) sí."""
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia')")
    def doc_p(source, n):
        d, _ = capture(con, source=source, url=f"u{n}", stable_id=str(n), content=f"texto ficticio {n} " * 5, license="x", reuse="reusable")
        segment(con, d)
        return con.execute("SELECT id FROM passages WHERE document_id=?", (d,)).fetchone()[0]
    for cid, prop, sources in (("cA", "composer", ("wikidata", "wikipedia_es")), ("cB", "librettist", ("wikidata", "musicbrainz"))):
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES(?,?,?,?,?,?,?)", (cid, "W1", prop, '"X"', "name", "asserted", "structured"))
        for i, src in enumerate(sources):
            con.execute("INSERT INTO claim_evidence VALUES(?,?,?,0,5)", (cid, doc_p(src, hash((cid, src)) % 10**6), "texto"))
    R.compute_confidence(con)
    a = con.execute("SELECT confidence FROM claims WHERE id='cA'").fetchone()[0]
    b = con.execute("SELECT confidence FROM claims WHERE id='cB'").fetchone()[0]
    assert b > a


def test_extraction_rejects_lowercase_person_names():
    p = "la partitura, perdida, de música de Ana Ficticia."
    ok = {"property": "composer", "value": "Ana Ficticia", "quote": "música de Ana Ficticia", "hedge": None, "status": "asserted"}
    assert verify_claim(ok, p)[0]
    assert not verify_claim({**ok, "value": "perdida", "quote": "perdida"}, p)[0]


def test_partial_name_and_provisional_people_merge(con):
    """Datos FICTICIOS: "Ficticio" se resuelve al compositor ya conocido de la obra; el artista provisional sin tilde se une al real."""
    con.executescript("""INSERT INTO entities(id,type,label,provisional) VALUES('W1','work','Obra ficticia',0),('P1','person','Ana Ficticio Más',0),('MBA:x','person','ana ficticio mas',1);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES
      ('c1','W1','composer','"P1"','entity','asserted','structured'),('c2','W1','composer','"Ficticio"','name','asserted','extracted'),
      ('c3','W1','librettist','"MBA:x"','entity','asserted','structured');""")
    st = R.resolve_names(con)
    assert st["resolved"] == 1 and con.execute("SELECT value_json FROM claims WHERE id='c2'").fetchone()[0] == '"P1"'
    assert R.merge_provisional_people(con) == 1
    assert con.execute("SELECT value_json FROM claims WHERE id='c3'").fetchone()[0] == '"P1"'
    assert con.execute("SELECT COUNT(*) FROM entities WHERE id='MBA:x'").fetchone()[0] == 0


def _ent(p31=(), p131=()):
    def stm(ids):
        return [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": i}}}} for i in ids]
    return {"claims": {"P31": stm(p31), "P131": stm(p131)}}


def test_climb_from_neighbourhood_to_municipality():
    """Datos FICTICIOS: barrio -> distrito -> municipio. Si no hay municipio en la cadena, se queda donde estaba."""
    from kb.sources.wikidata import climb_to_municipality
    ents = {"Qbarrio": _ent(p31=["Qbarrio_tipo"], p131=["Qdistrito"]), "Qdistrito": _ent(p31=["Qdistrito_tipo"], p131=["Qciudad"]),
            "Qciudad": _ent(p31=["Q2074737"], p131=["Qprovincia"]), "Qprovincia": _ent(p31=["Qprov_tipo"]),
            "Qestado": _ent(p31=["Qestado_tipo"], p131=["Qpais"]), "Qpais": _ent()}
    assert climb_to_municipality("Qbarrio", ents) == "Qciudad"
    assert climb_to_municipality("Qciudad", ents) == "Qciudad"                # ya es municipio
    assert climb_to_municipality("Qestado", ents) == "Qestado"                # sin municipio en la cadena
    assert climb_to_municipality("Qdesconocido", ents) == "Qdesconocido"


def test_summary_keeps_validated_sentences_and_drops_invented_ones(con):
    """Datos FICTICIOS: el modelo propone 3 frases; la que inventa un nombre y la que no cita nada se descartan."""
    from kb.generate import build_summary
    con.execute("INSERT INTO entities(id,type,label) VALUES('P1','person','Ana Ficticia')")
    text = "Ana Ficticia nació en Villaimaginaria en 1850 y estudió con Luis Imaginario. Estrenó La obra de ejemplo en 1890 con un gran éxito."
    doc, _ = capture(con, source="wikipedia_es", url="https://ejemplo.invalid/w?oldid=1", stable_id="1", content=text, license="CC-BY-SA-4.0", reuse="reusable")
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "P1"))
    seg = lambda t, c: {"text": t, "cites": c}
    def resp(system, user):
        pid = user.split("[")[1].split("]")[0]
        return {"segments": [seg("Ana Ficticia nació en Villaimaginaria en 1850 y estudió con Luis Imaginario.", [pid]),
                             seg("Estrenó La obra de ejemplo en 1890 con un gran éxito.", [pid]),
                             seg("Fue amiga de Pedro Falso en 1899.", [pid]),            # nombre y año ausentes del material
                             seg("Frase sin cita.", [])]}
    segment(con, doc)   # el texto se alarga abajo para que cuente como prosa
    con.execute("UPDATE passages SET text=? WHERE document_id=?", (text + " " + "Texto de relleno ficticio para alcanzar la longitud mínima de un párrafo de prosa. " * 2, doc))
    out = build_summary(con, make_llm(con, resp), "P1")
    texts = [s["text"] for s in out["content"]["segments"]]
    assert len(texts) == 2 and all("Pedro Falso" not in t for t in texts)
    assert sorted(d["text"] for d in out["report"]["dropped"]) == ["Frase sin cita.", "Fue amiga de Pedro Falso en 1899."]


def test_judge_summaries_drops_unapproved_sentences_and_withdraws_weak_summaries(con):
    """Datos FICTICIOS: el juez aprueba 2 de 3 frases (se quita la tercera); si aprueba menos de 2, el resumen se retira."""
    from kb.generate import judge_summaries, save_output
    con.execute("INSERT INTO entities(id,type,label) VALUES('P1','person','Ana Ficticia'),('P2','person','Luis Inventado')")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content="Ana Ficticia nació en 1850. Estudió música. Estrenó una obra. " * 3, license="x", reuse="reusable")
    segment(con, doc)
    pid = con.execute("SELECT id FROM passages").fetchone()[0]
    for eid in ("P1", "P2"):
        segs = [{"id": f"s{i}", "text": f"Frase {i} de {eid}.", "claim_ids": [], "narrative_ids": [], "passage_ids": [pid]} for i in range(3)]
        save_output(con, f"summary:{eid}", "summary", {"id": eid, "title": eid, "segments": segs}, {"kept": 3, "dropped": []})
    answers = iter([{"verdicts": [{"summary": 0, "index": 0, "verdict": "yes"}, {"summary": 0, "index": 1, "verdict": "partial"}, {"summary": 0, "index": 2, "verdict": "yes"},
                                  {"summary": 1, "index": 0, "verdict": "no"}, {"summary": 1, "index": 1, "verdict": "partial"}, {"summary": 1, "index": 2, "verdict": "yes"}]}])
    st = judge_summaries(con, make_llm(con, lambda s, u: next(answers)), max_calls=5)
    assert st["calls"] == 1 and st["summaries_verified"] == 2 and st["withdrawn"] == 1 and st["segments_dropped"] == 3   # 2 resúmenes en UNA llamada
    import json as _j
    from pathlib import Path as _P
    paths = {r["id"]: _P(r["content_path"]) for r in con.execute("SELECT id,content_path FROM outputs")}
    counts = sorted(len(_j.loads(p.read_text())["segments"]) for p in paths.values())
    assert counts == [0, 2]
    assert judge_summaries(con, make_llm(con, lambda s, u: next(answers)), max_calls=5)["calls"] == 0   # ya revisados: no se repite


def test_denomination_normalisation_and_prefix_mapping():
    from kb.resolve import denomination_key, _longest_prefix
    assert denomination_key("Sainete lírico en un acto y tres cuadros") == "sainete lirico"
    assert denomination_key("zarzuela (género chico)") == "zarzuela"
    by = {"zarzuela": "Z", "sainete lirico": "SL", "sainete": "S"}
    assert _longest_prefix("sainete lirico", by) == "SL" and _longest_prefix("zarzuela comica", by) == "Z" and _longest_prefix("opera", by) is None


def test_genre_detected_in_lead_with_literal_quote(con):
    """Datos FICTICIOS: «es una ópera española en tres actos» en el primer párrafo da una denominación con cita literal."""
    con.executescript("""INSERT INTO entities(id,type,label,provisional) VALUES('W1','work','Obra ficticia',0);
      INSERT INTO concepts(id,scheme,pref_label,alt_labels,hidden_labels,scope_note,scope_source,broader,related) VALUES
      ('K1','genres','ópera española','[]','[]',NULL,'u','[]','[]'),('K2','genres','zarzuela','[]','[]',NULL,'u','[]','[]');""")
    text = "Obra ficticia es una ópera española en tres actos, con música de un compositor imaginario y libreto de otro autor ficticio."
    doc, _ = capture(con, source="wikipedia_es", url="https://ejemplo.invalid/w?oldid=1", stable_id="1", content=text, license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    assert R.detect_genres_in_leads(con) == {"works": 1, "claims": 1}
    row = con.execute("SELECT c.value_json, ce.quote FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id WHERE c.property='denomination'").fetchone()
    assert json.loads(row[0]) == "ópera española" and row[1] in text            # la cita es subcadena exacta
    assert R.map_genres(con)["mapped"] == 1
    assert con.execute("SELECT json_extract(value_json,'$') FROM claims WHERE property='genre_concept'").fetchone()[0] == "K1"


def test_off_subject_dates_are_rejected_unless_in_lead_or_matching_wikidata(con):
    """Datos FICTICIOS: la fecha del párrafo inicial y la que coincide con Wikidata se conservan; la de un hijo citado más abajo, no."""
    con.execute("INSERT INTO entities(id,type,label) VALUES('P1','person','Ana Ficticia')")
    text = ("Ana Ficticia nació en 1562 y fue una compositora imaginaria de enorme fama en su tiempo y en todo el lugar donde vivió y trabajó.\n"
            "Su hija, nacida probablemente en 1589, heredó el taller. Su hijo fue bautizado en 1603 en la parroquia.\n"
            "Ana murió de forma repentina en 1635, según los cronistas de la época y de su ciudad natal.")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content=text, license="x", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "P1"))
    pids = {r["text"][:12]: r["id"] for r in con.execute("SELECT id,text FROM passages")}
    def claim(cid, prop, val, kind, pid=None):
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES(?,?,?,?,?,?,?)", (cid, "P1", prop, json.dumps(val), "edtf", "asserted", kind))
        if pid:
            con.execute("INSERT INTO claim_evidence VALUES(?,?,?,0,5)", (cid, pid, "x"))
    claim("w1", "birth_date", "1562-11-25", "structured")
    claim("w2", "death_date", "1635-08-27", "structured")
    claim("a", "birth_date", "1562", "extracted", pids["Ana Ficticia"])      # párrafo inicial
    claim("b", "birth_date", "1589", "extracted", pids["Su hija, nac"])        # otra persona
    claim("c", "birth_date", "1603", "extracted", pids["Su hija, nac"])        # otra persona
    claim("d", "death_date", "1635", "extracted", pids["Ana murió de"])        # coincide con Wikidata
    st = R.reject_off_subject_dates(con)
    assert st["rejected"] == 2 and st["kept_lead"] == 1 and st["kept_agree"] == 1
    status = {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM claims")}
    assert status["b"] == status["c"] == "deprecated" and status["a"] == status["d"] == "asserted" and status["w1"] == "asserted"


def test_redo_with_haiku_confirms_retires_and_adds(con):
    """Datos FICTICIOS: Haiku confirma el compositor de Qwen, retira la fecha errónea y añade el lugar de estreno que Qwen no vio."""
    from kb.extract import redo_with_haiku
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia')")
    text = "Obra ficticia es una zarzuela con música de Ana Ficticia, estrenada en el Teatro Imaginario en 1890; una reposición en 1950 tuvo menos éxito."
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content=text, license="x", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    pid = con.execute("SELECT id FROM passages").fetchone()[0]
    def qwen(cid, prop, val, quote):
        con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind,extracted_by_json) VALUES(?,?,?,?,?,?,?,?)",
                    (cid, "W1", prop, json.dumps(val), "name", "asserted", "extracted", json.dumps({"engine": "local"})))
        con.execute("INSERT INTO claim_evidence VALUES(?,?,?,?,?)", (cid, pid, quote, text.find(quote), text.find(quote) + len(quote)))
    qwen("q1", "composer", "Ana Ficticia", "música de Ana Ficticia")
    qwen("q2", "premiere_date", "1950", "en 1950")                      # error: es una reposición
    claim = lambda p, v, q: {"property": p, "value": v, "quote": q, "hedge": None, "status": "asserted"}
    answer = {"items": [{"index": 0, "claims": [claim("composer", "Ana Ficticia", "música de Ana Ficticia"), claim("premiere_date", "1890", "en 1890"),
                                                  claim("premiere_venue", "Teatro Imaginario", "en el Teatro Imaginario")]}]}
    st = redo_with_haiku(con, make_llm(con, lambda s, u: answer), max_calls=2, batch=8)
    assert (st["confirmed"], st["retired"], st["new"], st["passages"]) == (1, 1, 2, 1) and st["qwen_precision_estimate"] == 0.5
    status = {r["id"]: r["status"] for r in con.execute("SELECT id,status FROM claims WHERE id IN ('q1','q2')")}
    assert status == {"q1": "asserted", "q2": "deprecated"}
    assert redo_with_haiku(con, make_llm(con, lambda s, u: answer), max_calls=2)["calls"] == 0          # no se repite


def test_narratives_with_haiku_store_the_literal_quote_and_skip_the_lead(con):
    """Datos FICTICIOS: el episodio se guarda con la cita literal; la cita inventada se descarta; el párrafo inicial no se procesa."""
    from kb.extract import narratives_with_haiku
    con.execute("INSERT INTO entities(id,type,label) VALUES('W1','work','Obra ficticia')")
    lead = "Obra ficticia es una zarzuela imaginaria en tres actos, compuesta en el siglo XIX para un teatro de provincias que nunca existió. " * 2
    body = ("Según se cuenta, durante el ensayo general el tenor perdió la voz y el compositor tuvo que cantar su papel desde el foso, entre las risas del público. "
            "El estreno fue un gran éxito de crítica y la prensa local lo calificó de acontecimiento musical del año en toda la provincia.")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content=lead + "\n" + body, license="x", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    good = "durante el ensayo general el tenor perdió la voz y el compositor tuvo que cantar su papel desde el foso, entre las risas del público"
    answer = {"items": [{"index": 0, "narratives": [
        {"type": "anécdota", "title": "El tenor sin voz", "quote": good, "status": "reported", "time": None},
        {"type": "recepción", "title": "Cita inventada", "quote": "La crítica internacional aclamó la obra durante décadas enteras", "status": "documented", "time": None}]}]}
    st = narratives_with_haiku(con, make_llm(con, lambda s, u: answer), max_calls=2, entities=5)
    assert (st["calls"], st["narratives"], st["discarded"], st["passages"]) == (1, 1, 1, 1)
    row = con.execute("SELECT summary,status FROM narratives").fetchone()
    assert row["summary"] == good and row["status"] == "reported"
    assert narratives_with_haiku(con, make_llm(con, lambda s, u: answer), max_calls=2, entities=5)["calls"] == 0     # no se repite
