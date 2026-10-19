"""Datos FICTICIOS."""
import json

from kb.check import run_checks
from kb.export import export_all
from kb.store import capture, segment


def _seed(con, reuse):
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('W1','work','Obra ficticia','{}')")
    text = "Este es un pasaje ficticio lo bastante largo como para detectarlo si se expusiera por error en la API pública."
    doc, _ = capture(con, source="x", url="https://ejemplo.invalid", stable_id="1", content=text, license="ficticia", reuse=reuse)
    segment(con, doc)
    p = con.execute("SELECT * FROM passages").fetchone()
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES('C1','W1','premiere_date','\"1890\"','edtf','asserted',0.9,'extracted')")
    con.execute("INSERT INTO claim_evidence VALUES('C1',?,?,0,5)", (p["id"], text[:5]))
    return text


def test_export_hides_consult_only(con, tmp_path):
    text = _seed(con, "consult_only")
    out = tmp_path / "api"
    export_all(con, out)
    ev = json.loads((out / "evidence" / "W1.json").read_text())
    assert ev[0]["text"] is None and ev[0]["reuse"] == "consult_only"
    assert text not in "".join(f.read_text() for f in out.rglob("*.json"))
    assert run_checks(con, out) == []


def test_export_shows_reusable_and_check_detects_missing_evidence(con, tmp_path):
    text = _seed(con, "reusable")
    out = tmp_path / "api"
    export_all(con, out)
    assert json.loads((out / "evidence" / "W1.json").read_text())[0]["text"] == text
    con.execute("DELETE FROM claim_evidence")
    assert any("sin evidencia" in e for e in run_checks(con, out))


def test_export_links_only_to_exported_entities(con, tmp_path):
    """Datos FICTICIOS: un compositor con ficha se enlaza; uno provisional no."""
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES
      ('W1','work','Obra ficticia','{}',0),('P1','person','Compositor ficticio','{}',0),('kb:x','other','Nombre sin resolver','{}',1);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES
      ('C1','W1','composer','"P1"','entity','asserted',0.9,'structured'),('C2','W1','librettist','"kb:x"','entity','asserted',0.5,'extracted');""")
    out = tmp_path / "api"
    export_all(con, out)
    claims = {c["id"]: c for c in json.loads((out / "works" / "W1.json").read_text())["claims"]}
    assert claims["C1"]["value_link"] == "persona" and claims["C2"]["value_link"] is None


def test_export_merges_same_fact_from_two_sources(con, tmp_path):
    """Datos FICTICIOS: Wikidata y la extracción dan el mismo compositor; la ficha lo muestra una vez con ambas evidencias."""
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES('W1','work','Obra ficticia','{}',0),('P1','person','Compositor ficticio','{}',0);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES
      ('C1','W1','composer','"P1"','entity','asserted',0.7,'structured'),('C2','W1','composer','"P1"','entity','asserted',0.5,'extracted');""")
    out = tmp_path / "api"
    export_all(con, out)
    claims = json.loads((out / "works" / "W1.json").read_text())["claims"]
    assert len(claims) == 1 and claims[0]["n_claims"] == 2 and claims[0]["source_kind"] == "structured"


def test_home_eras_one_row_per_work_chronological_structured_date_wins(con, tmp_path):
    """Datos FICTICIOS: una obra con fecha estructurada y otra extraída errónea sale una vez, con la estructurada y ordenada por año."""
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES
      ('W1','work','Obra A','{}',0),('W2','work','Obra B','{}',0);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES
      ('c1','W1','premiere_date','"1874-12-19"','edtf','disputed',0.4,'structured'),
      ('c2','W1','premiere_date','"1766"','edtf','disputed',0.5,'extracted'),
      ('c3','W2','premiere_date','"1950"','edtf','asserted',0.9,'structured');""")
    out = tmp_path / "api"
    export_all(con, out)
    home = json.loads((out / "views" / "home.json").read_text())
    eras = {e["id"]: e for e in home["eras"]}
    assert [(w["id"], w["year"]) for w in eras[3]["works"]] == [("W1", 1874)]   # 1874 gana al 1766 extraído -> época del género chico
    assert [(w["id"], w["year"]) for w in eras[5]["works"]] == [("W2", 1950)]
    assert home["total"] == 2 and eras[3]["total"] == 1


def test_duplicates_are_excluded_from_corpus_and_export(con, tmp_path):
    """Datos FICTICIOS: dos ítems para la misma obra (mismo título base, compositor y año): se queda el de más datos."""
    from kb.export import mark_duplicates
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES
      ('W1','work','Obra ficticia (zarzuela)','{}',0),('W2','work','Obra Ficticia','{}',0),('P1','person','Compositor ficticio','{}',0);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES
      ('a1','W1','composer','"P1"','entity','asserted',0.9,'structured'),('a2','W1','premiere_date','"1890"','edtf','asserted',0.9,'structured'),
      ('a3','W1','librettist','"P1"','entity','asserted',0.9,'structured'),
      ('b1','W2','composer','"P1"','entity','asserted',0.9,'structured'),('b2','W2','premiere_date','"1890"','edtf','asserted',0.9,'structured');
      INSERT INTO corpus VALUES('W1',1,'x'),('W2',1,'x');""")
    assert mark_duplicates(con) == {"W2": "W1"}
    assert con.execute("SELECT included FROM corpus WHERE entity_id='W2'").fetchone()[0] == 0
    out = tmp_path / "api"
    export_all(con, out)
    assert (out / "works" / "W1.json").exists() and not (out / "works" / "W2.json").exists()


def test_collaborations_view(con, tmp_path):
    """Datos FICTICIOS: un compositor y un libretista que firman dos obras forman una arista con peso 2."""
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES
      ('W1','work','Obra uno','{}',0),('W2','work','Obra dos','{}',0),('P1','person','Compositor ficticio','{}',0),('P2','person','Libretista ficticio','{}',0);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind) VALUES
      ('c1','W1','composer','"P1"','entity','asserted',0.9,'structured'),('c2','W1','librettist','"P2"','entity','asserted',0.9,'structured'),
      ('c3','W2','composer','"P1"','entity','asserted',0.9,'structured'),('c4','W2','librettist','"P2"','entity','asserted',0.9,'structured');""")
    out = tmp_path / "api"
    export_all(con, out)
    v = json.loads((out / "views" / "collaborations.json").read_text())
    assert len(v["edges"]) == 1 and v["edges"][0]["weight"] == 2
    assert {n["id"] for n in v["nodes"]} == {"P1", "P2"}


def test_biography_links_known_names_and_highlights_years(con, tmp_path):
    """Datos FICTICIOS: el texto de Wikipedia se muestra con enlaces a fichas conocidas y los años resaltados; el propio nombre no se enlaza."""
    from kb.store import capture, segment
    con.executescript("""INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES
      ('P1','person','Ana Ficticia','[]','{}',0),('W1','work','Obra Inventada','[]','{}',0),('P2','person','Luis Imaginario','[]','{}',0);""")
    text = "Ana Ficticia nació en 1850 y estrenó Obra Inventada en 1890, con libreto de Luis Imaginario, en un teatro de provincias donde triunfó."
    doc, _ = capture(con, source="wikipedia_es", url="https://ejemplo.invalid/w/index.php?title=Ana_Ficticia&oldid=7", stable_id="7", content=text,
                     license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "P1"))
    out = tmp_path / "api"
    export_all(con, out)
    bio = json.loads((out / "people" / "P1.json").read_text())["biography"]
    segs = bio["paragraphs"][0]["segments"]
    assert {"t": "Obra Inventada", "to": "/obra/W1"} in segs and {"t": "Luis Imaginario", "to": "/persona/P2"} in segs
    assert any(s.get("year") and s["t"] == "1850" for s in segs)
    assert not any(s.get("to") == "/persona/P1" for s in segs)          # no se enlaza a sí mismo
    assert bio["revision"] == "7" and "action=history" in bio["history_url"]
    ev = json.loads((out / "evidence" / "P1.json").read_text())
    assert any(i["id"] == bio["paragraphs"][0]["passage_id"] and i["text"] for i in ev)   # la concha puede mostrar el fragmento


def test_alt_labels_deduplicated_ignoring_accents():
    from kb.export import _clean_alt_labels
    assert _clean_alt_labels("Ana Ficticia", ["Ana Ficticia", "Ana Fictícia", "ana ficticia", "Ana Ficticia Pérez", "Ana Ficticia Perez"]) == ["Ana Ficticia Pérez"]


def test_genre_pages_list_works_even_with_accented_concept_ids(con, tmp_path):
    """Datos FICTICIOS: un concepto con tilde en el id (kb:genre:sainete-lírico) recupera sus obras y recuenta las denominaciones."""
    from kb.db import jdump
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES('W1','work','Obra ficticia','{}',0);
      INSERT INTO concepts(id,scheme,pref_label,alt_labels,hidden_labels,scope_note,scope_source,broader,related) VALUES
      ('kb:genre:sainete-lírico','genres','sainete lírico','[]','[]','Un pasaje ficticio.','https://es.wikipedia.org/w/index.php?title=X&oldid=1','[]','[]');""")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,qualifiers_json,status,confidence,source_kind) VALUES('g1','W1','genre_concept',?,'entity',?,'asserted',0.8,'derived')",
                (jdump("kb:genre:sainete-lírico"), jdump({"label": "sainete lírico en un acto"})))
    out = tmp_path / "api"
    export_all(con, out)
    g = json.loads((out / "genres" / "kb_genre_sainete-lirico.json").read_text())
    assert [w["id"] for w in g["works"]] == ["W1"] and g["denominations"] == [{"label": "sainete lírico en un acto", "count": 1}] and g["note_kind"] == "uso"


def test_summary_segments_carry_links_and_year_highlights(con, tmp_path):
    """Datos FICTICIOS: el resumen hereda los enlaces a otras fichas y los años resaltados."""
    from kb.generate import save_output
    from kb.store import capture, segment
    con.executescript("""INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES
      ('P1','person','Ana Ficticia','[]','{}',0),('W1','work','Obra Inventada','[]','{}',0);""")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content="Ana Ficticia estrenó Obra Inventada en 1890 con gran éxito de crítica y público.", license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    pid = con.execute("SELECT id FROM passages").fetchone()[0]
    segs = [{"id": f"s{i}", "text": "Ana Ficticia estrenó Obra Inventada en 1890.", "claim_ids": [], "narrative_ids": [], "passage_ids": [pid]} for i in range(2)]
    save_output(con, "summary:P1", "summary", {"id": "P1", "title": "x", "segments": segs}, {"kept": 2, "dropped": [], "haiku_checked": True})
    out = tmp_path / "api"
    export_all(con, out)
    parts = json.loads((out / "people" / "P1.json").read_text())["summary"]["segments"][0]["parts"]
    assert {"t": "Obra Inventada", "to": "/obra/W1"} in parts and any(p.get("year") and p["t"] == "1890" for p in parts)


def test_unverified_summaries_are_not_published_by_default(con, tmp_path):
    """Datos FICTICIOS: un resumen que Haiku aún no ha revisado no sale en la API; con la revisión hecha, sí."""
    from kb.generate import save_output
    from kb.store import capture, segment
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES('P1','person','Ana Ficticia','[]','{}',0)")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content="Ana Ficticia nació en 1850 y estrenó una obra de éxito en un teatro de provincias.", license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    pid = con.execute("SELECT id FROM passages").fetchone()[0]
    segs = [{"id": f"s{i}", "text": f"Frase {i}.", "claim_ids": [], "narrative_ids": [], "passage_ids": [pid]} for i in range(2)]
    save_output(con, "summary:P1", "summary", {"id": "P1", "title": "x", "segments": segs}, {"kept": 2, "dropped": []})
    out = tmp_path / "api"
    export_all(con, out)
    assert json.loads((out / "people" / "P1.json").read_text())["summary"] is None
    save_output(con, "summary:P1", "summary", {"id": "P1", "title": "x", "segments": segs}, {"kept": 2, "dropped": [], "haiku_checked": True})
    export_all(con, out)
    s = json.loads((out / "people" / "P1.json").read_text())["summary"]
    assert s and s["verified"] is True


def test_timeline_one_date_per_entity_and_kind_with_route_and_alternatives(con, tmp_path):
    """Datos FICTICIOS: una persona con tres fechas de nacimiento sale una vez (la preferida), con ruta a su ficha y las otras como alternativas."""
    con.executescript("""INSERT INTO entities(id,type,label,external_ids,provisional) VALUES('P1','person','Persona ficticia','{}',0),('V1','place','Ciudad ficticia','{}',0);
      INSERT INTO claims(id,subject_id,property,value_json,value_type,status,confidence,source_kind,qualifiers_json) VALUES
      ('a','P1','birth_date','"1562-11-25"','edtf','asserted',0.9,'structured','{"wikidata_rank":"preferred"}'),
      ('b','P1','birth_date','"1562"','edtf','asserted',0.7,'structured','{"wikidata_rank":"normal"}'),
      ('c','P1','birth_date','"1589"','edtf','asserted',0.5,'extracted','{}'),
      ('d','V1','opening_date','"1238"','edtf','asserted',0.7,'structured','{}');""")
    out = tmp_path / "api"
    export_all(con, out)
    tl = json.loads((out / "views" / "timeline.json").read_text())
    assert [(r["entity_id"], r["edtf"], r["route"], r["alternatives"]) for r in tl] == [("P1", "1562-11-25", "persona", ["1589"])]   # 1562 se pliega; 1589 es alternativa; la ciudad no sale


def test_scores_from_imslp_are_exported_as_links_with_copyright(con, tmp_path):
    """Datos FICTICIOS: la ficha de la obra recibe sus partituras de IMSLP como enlaces, con el copyright de cada fichero."""
    from kb.store import capture, segment
    wikitext = ("{{Information}}\n*****WORK INFO*****\n|Work Title=Obra Ficticia\n|Language=Spanish\n*****COMMENTS*****\nNada.\n*****END OF TEMPLATE*****\n"
                "{{#fte:imslpfile\n|File Name=Obra_Ficticia_vocal_score.pdf\n|File Description=Partitura vocal ficticia\n|Copyright=Public Domain\n|Editor=Editor Ficticio\n}}\n")
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES('W1','work','Obra ficticia','[]','{}',0)")
    doc, _ = capture(con, source="imslp", url="https://imslp.org/wiki/Obra_Ficticia_(Compositor_Ficticio)?oldid=100", stable_id="1@100", content="IMSLP: Obra ficticia",
                     license="CC-BY-SA-4.0", reuse="reusable", raw=wikitext.encode())
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    out = tmp_path / "api"
    export_all(con, out)
    sc = json.loads((out / "works" / "W1.json").read_text())["scores"]
    assert len(sc) == 1 and sc[0]["page_url"] == "https://imslp.org/wiki/Obra_Ficticia_(Compositor_Ficticio)"
    f = sc[0]["files"][0]
    assert f["copyright"] == "Public Domain" and f["editor"] == "Editor Ficticio" and f["desc"] == "Partitura vocal ficticia" and f["url"] == "https://imslp.org/wiki/File:Obra_Ficticia_vocal_score.pdf"


def test_scores_generic_titles_become_numbers_grouped_by_edition(con, tmp_path):
    """Datos FICTICIOS: «Complete Score» repetido se sustituye por el número deducido del nombre del fichero y se agrupa por edición."""
    from kb.store import capture, segment
    def f(name):
        return f"{{{{#fte:imslpfile\n|File Name={name}\n|File Description=Complete Score\n|Copyright=Public Domain\n|Publisher Information=Madrid, n.d.[1870]\n}}}}\n"
    wikitext = ("{{Information}}\n*****WORK INFO*****\n|Work Title=Obra Ficticia\n*****COMMENTS*****\nNada.\n*****END OF TEMPLATE*****\n"
                + f("PMLP1-Autor_A-Obra_2.pdf") + f("PMLP1-Autor_A-Obra_15D.pdf") + f("PMLP1-Otro_B-Obra_3.pdf") + f("PMLP1-Autor_A-Obra_13+13bis.pdf"))
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES('W1','work','Obra ficticia','[]','{}',0)")
    doc, _ = capture(con, source="imslp", url="https://imslp.org/wiki/X?oldid=1", stable_id="1@1", content="x", license="CC-BY-SA-4.0", reuse="reusable", raw=wikitext.encode())
    segment(con, doc)
    con.execute("INSERT INTO doc_subjects VALUES(?,?)", (doc, "W1"))
    out = tmp_path / "api"
    export_all(con, out)
    files = json.loads((out / "works" / "W1.json").read_text())["scores"][0]["files"]
    assert [(x["edition"].split(" · ")[0], x["desc"]) for x in files if x["edition"].startswith("Autor A")] == [("Autor A", "Núm. 2"), ("Autor A", "Núm. 13 + 13 bis"), ("Autor A", "Núm. 15 D")]
    assert any(x["edition"].startswith("Otro B") and x["desc"] == "Núm. 3" for x in files)


def test_documents_view_filters_by_topic_cleans_authors_and_types(con, tmp_path):
    """Datos FICTICIOS: solo salen los registros temáticos; los autores sin nombre (solo URL) se omiten y el tipo se traduce."""
    from kb.store import capture, segment
    def doc(source, title, extra):
        text = f"Registro {title}\nTítulo: {title}\n" + extra
        d, _ = capture(con, source=source, url=f"https://ejemplo.invalid/{title[:8]}", stable_id=title, content=text, license="http://creativecommons.org/publicdomain/zero/1.0/", reuse="reusable")
        segment(con, d)
    doc("europeana", "Zarzuela ficticia en dos actos", "Creador: Ana Ficticia <https://ejemplo.invalid/a1>\nCreador: https://ejemplo.invalid/agent/9\nTipo: TEXT\nFecha: 1890\n")
    doc("bvph", "El Periódico Ficticio de la Provincia", "Tipo: Periódicos y Revistas\nFecha: 1892\n")
    out = tmp_path / "api"
    export_all(con, out)
    docs = json.loads((out / "views" / "documents.json").read_text())
    assert [d["title"] for d in docs] == ["Zarzuela ficticia en dos actos"]
    assert docs[0]["creators"] == ["Ana Ficticia"] and docs[0]["type"] == "Texto" and docs[0]["rights"] == "CC0"


def test_documents_view_merges_the_same_item_arriving_twice(con, tmp_path):
    """Datos FICTICIOS: el mismo libreto por dos registros (uno sin autor) sale una vez, con el autor."""
    from kb.store import capture, segment
    for i, extra in enumerate(("Tipo: TEXT\nFecha: 1732\n", "Creador: Ana Ficticia <https://ejemplo.invalid/a>\nTipo: TEXT\nFecha: 1732\n")):
        d, _ = capture(con, source="europeana", url=f"https://ejemplo.invalid/{i}", stable_id=str(i), content=f"Registro {i}\nTítulo: Zarzuela nueva ficticia\n{extra}",
                       license="http://creativecommons.org/publicdomain/zero/1.0/", reuse="reusable")
        segment(con, d)
    out = tmp_path / "api"
    export_all(con, out)
    docs = json.loads((out / "views" / "documents.json").read_text())
    assert len(docs) == 1 and docs[0]["creators"] == ["Ana Ficticia"]


def test_filenames_are_ascii_safe_for_any_static_host():
    from kb.export import fname
    assert fname("Q148163") == "Q148163" and fname("kb:genre:sainete-lírico") == "kb_genre_sainete-lirico"
    assert fname("kb:genre:género-ínfimo") == "kb_genre_genero-infimo"


def test_summary_sentences_citing_retired_claims_are_not_published(con, tmp_path):
    """Datos FICTICIOS: una frase que cita una afirmación retirada no sale; si quedan menos de 2 frases, el resumen tampoco."""
    from kb.generate import save_output
    from kb.store import capture, segment
    con.execute("INSERT INTO entities(id,type,label,labels_alt,external_ids,provisional) VALUES('P1','person','Ana Ficticia','[]','{}',0)")
    doc, _ = capture(con, source="wikipedia_es", url="u", stable_id="1", content="Ana Ficticia nació en 1850 y estrenó una obra de éxito en un teatro de provincias.", license="CC-BY-SA-4.0", reuse="reusable")
    segment(con, doc)
    pid = con.execute("SELECT id FROM passages").fetchone()[0]
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES('Cdead','P1','birth_date','\"1849\"','edtf','deprecated','extracted')")
    segs = [{"id": "s0", "text": "Frase uno.", "claim_ids": [], "narrative_ids": [], "passage_ids": [pid]},
            {"id": "s1", "text": "Frase dos.", "claim_ids": [], "narrative_ids": [], "passage_ids": [pid]},
            {"id": "s2", "text": "Frase tres, con un dato retirado.", "claim_ids": ["Cdead"], "narrative_ids": [], "passage_ids": [pid]}]
    save_output(con, "summary:P1", "summary", {"id": "P1", "title": "x", "segments": segs}, {"kept": 3, "dropped": [], "haiku_checked": True})
    out = tmp_path / "api"
    export_all(con, out)
    assert [s["text"] for s in json.loads((out / "people" / "P1.json").read_text())["summary"]["segments"]] == ["Frase uno.", "Frase dos."]
    save_output(con, "summary:P1", "summary", {"id": "P1", "title": "x", "segments": segs[1:]}, {"kept": 2, "dropped": [], "haiku_checked": True})
    export_all(con, out)
    assert json.loads((out / "people" / "P1.json").read_text())["summary"] is None
