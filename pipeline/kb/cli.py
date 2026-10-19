"""CLI `kb` (plan §8.2)."""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import pipeline as P
from .db import DATA, connect
from .llm.core import default_llm

app = typer.Typer(no_args_is_help=True, help="Base de conocimiento de zarzuela")
vocab_app = typer.Typer(help="Vocabulario de géneros")
app.add_typer(vocab_app, name="vocab")
source_app = typer.Typer(help="Conectores de fuentes (fase 0 y descarga)")
app.add_typer(source_app, name="source")


def _echo(x):
    typer.echo(json.dumps(x, ensure_ascii=False, indent=1) if not isinstance(x, str) else x)


def _connector(name: str):
    import importlib
    return importlib.import_module(f"kb.sources.{name}")


@source_app.command("phase0")
def source_phase0(name: str, sample: int = 10):
    """Fase 0 de una fuente: términos, endpoint, muestra de N registros y licencia efectiva (contrato: `phase0(sample)` en kb/sources/<name>.py)."""
    _echo(_connector(name).phase0(sample))


@source_app.command("ingest")
def source_ingest(name: str, limit: Optional[int] = None):
    """Descarga/captura de una fuente validada (contrato: `ingest(con, limit)` en kb/sources/<name>.py)."""
    from .http import require_source
    require_source(name)
    _echo(_connector(name).ingest(connect(), limit))


@app.command()
def init():
    """Crea la base SQLite y carga el vocabulario semilla."""
    from .vocab import load_into_db
    con = connect()
    _echo(f"base lista en {DATA / 'kb.sqlite'}; conceptos cargados: {load_into_db(con)}")


@app.command()
def phase0():
    """Fase 0: comprueba cada fuente validada con una muestra de 10 registros."""
    from .http import Http, load_sources
    rep = {}
    for name, cfg in load_sources().items():
        if not cfg.get("validated"):
            rep[name] = "pendiente de validar"
            continue
        h = Http(name, cfg=cfg)
        try:
            if name == "wikidata":
                n = len(h.json(cfg["sparql"], {"query": "SELECT ?w WHERE { ?w wdt:P136 wd:Q148163 } LIMIT 10", "format": "json"}, {"Accept": "application/sparql-results+json"})["results"]["bindings"])
            else:
                n = len(h.json(cfg["endpoint"], {"action": "query", "list": "allpages", "aplimit": 10, "format": "json"})["query"]["allpages"])
            rep[name] = {"ok": True, "sample": n, "license": cfg["license"]}
        except Exception as e:  # noqa: BLE001
            rep[name] = {"ok": False, "error": str(e)}
    _echo(rep)


@app.command()
def discover(limit: int = 50):
    """Semilla en Wikidata: obras de zarzuela y género chico."""
    con = connect()
    q = P.discover(con, limit)
    _echo(f"{len(q)} obras en el corpus")


@app.command()
def capture(langs: str = "es,ca,en,eu,gl", wikisource: bool = True):
    """Captura instantáneas (Wikidata, Wikipedia en varios idiomas y Wikisource ES) y las segmenta en pasajes."""
    con = connect()
    out = {"wikidata": P.capture_wikidata(con)}
    for lang in langs.split(","):
        out[f"wikipedia_{lang}"] = P.capture_wikipedia(con, lang=lang)
    if wikisource:
        out["wikisource_es"] = P.capture_wikisource(con)
    _echo(out)


@app.command("wikidata-reingest")
def wikidata_reingest():
    """Vuelve a procesar las entidades del corpus desde la caché de Wikidata (sin red si no han cambiado): reclasifica, completa IDs y afirmaciones."""
    from .http import Http
    from .sources import wikidata
    con = connect()
    qids = [r["entity_id"] for r in con.execute("SELECT entity_id FROM corpus WHERE included=1")]
    _echo(wikidata.ingest(con, Http("wikidata"), qids, depth=1))


@app.command("capture-wikisource")
def capture_wikisource(limit: Optional[int] = None):
    """Busca libretos en Wikisource ES por título exacto de cada obra del corpus."""
    _echo(P.capture_wikisource_search(connect(), limit))


@app.command("discover-categories")
def discover_categories(category: list[str] = typer.Argument(..., help="p. ej. 'Categoría:Zarzuelas'"), depth: int = 2):
    """Amplía el corpus con miembros de categorías de es.wikipedia."""
    _echo(f"{P.discover_categories(connect(), category, depth)} entidades nuevas en el corpus")


@app.command()
def segment():
    """Segmenta documentos que aún no tienen pasajes (idempotente)."""
    from .store import segment as seg
    con = connect()
    n = sum(seg(con, d["id"]) for d in con.execute("SELECT id FROM documents").fetchall())
    _echo(f"{n} pasajes nuevos")


@app.command()
def extract(engine: Optional[str] = typer.Option(None, help="local | claude"), narratives: bool = False,
            limit_docs: Optional[int] = None):
    """Extrae afirmaciones (y narrativas) con el LLM; verificación literal obligatoria."""
    con = connect()
    llm = default_llm(con)
    _echo(P.extract_all(con, llm, narratives=narratives, engine=engine, limit_docs=limit_docs,
                        progress=lambda s: typer.echo(f"\r{s}", nl=False)))


@app.command()
def escalate(max_calls: int = 100):
    """Reextrae con Claude (haiku) los pasajes con afirmaciones en disputa. Respeta el tope diario."""
    from .extract import escalate_disputed
    con = connect()
    _echo(escalate_disputed(con, default_llm(con), max_calls))


@app.command()
def resolve(tiebreak: bool = False):
    """Entidades, conflictos, confianza y clasificación de géneros."""
    from . import resolve as R
    con = connect()
    llm = default_llm(con) if tiebreak else None
    out = {"invalid_names_dropped": R.drop_invalid_names(con), "off_subject_dates": R.reject_off_subject_dates(con), "names": R.resolve_names(con, llm), "provisional_people_merged": R.merge_provisional_people(con), "conflicts": R.detect_conflicts(con), "genres_from_leads": R.detect_genres_in_leads(con), "genres": R.map_genres(con, llm)}
    R.compute_confidence(con)
    from .export import mark_duplicates
    out["duplicates_excluded"] = len(mark_duplicates(con))
    P.bump_version(con)
    _echo(out)


@app.command()
def validate():
    """Reglas de integridad de las afirmaciones."""
    from .validate import validate_claims
    con = connect()
    issues = validate_claims(con)
    out = DATA / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1))
    _echo(f"{len(issues)} incidencias (data/reports/validation.json)")


@vocab_app.command("seed")
def vocab_seed():
    """Amplía el vocabulario con config/vocab_seed.yaml (artículos y pasajes de Wikipedia, con su fuente) y lo carga en la base."""
    from .http import Http
    from .vocab import build_from_seed, load_into_db
    con = connect()
    st = build_from_seed(con, Http("wikipedia_es"))
    _echo({**st, "cargados": load_into_db(con)})


@vocab_app.command("build")
def vocab_build():
    """Amplía vocab/genres.yaml desde Wikidata y lo carga en la base."""
    from .http import Http
    from .sources.wikidata import GENRE_ROOTS
    from .vocab import build_from_wikidata, load_into_db
    n = build_from_wikidata(Http("wikidata"), GENRE_ROOTS)
    _echo(f"{n} conceptos; cargados {load_into_db(connect())}")


@app.command()
def generate():
    """Cuestionario (sin LLM: preguntas con distractores reales comprobados)."""
    from . import generate as G
    _echo({"quiz": len(G.build_quiz(connect())["questions"])})


@app.command()
def summarize(types: str = "person,work,venue", limit: Optional[int] = None, judge_sample: float = 0.0, redo: bool = False, only_unreviewed: bool = False):
    """Resúmenes narrativos con el modelo local (Qwen), validados frase a frase. `--judge-sample 0.1` revisa el 10 % con Haiku."""
    import random
    from . import generate as G
    con = connect()
    llm = default_llm(con)
    rng = random.Random(7)
    ids = [r["id"] for r in con.execute(
        f"SELECT e.id FROM entities e WHERE e.provisional=0 AND e.type IN ({','.join('?' * len(types.split(',')))}) "
        "AND e.id NOT IN (SELECT entity_id FROM corpus WHERE included=0) ORDER BY e.type, e.id", types.split(","))]
    done = {r[0].split(":", 1)[1] for r in con.execute("SELECT id FROM outputs WHERE kind='summary'")}
    if only_unreviewed:      # re-escribir solo resúmenes que Haiku aún no ha revisado (no se pierde ninguna revisión): sirve para probar un prompt nuevo
        import json as _j
        unrev = set()
        for r in con.execute("SELECT id, validation_report_json FROM outputs WHERE kind='summary'"):
            if not _j.loads(r["validation_report_json"] or "{}").get("haiku_checked"):
                unrev.add(r["id"].split(":", 1)[1])
        todo = [i for i in ids if i in unrev][:limit]
        redo = True
    else:
        todo = [i for i in ids if redo or i not in done][:limit]
    st = {"requested": len(todo), "written": 0, "no_material_or_invalid": 0, "segments_kept": 0, "segments_dropped": 0}
    for n, eid in enumerate(todo, 1):
        out = G.build_summary(con, llm, eid, judge_sample, rng)
        if not out or not out["content"]:
            st["no_material_or_invalid"] += 1
        else:
            st["written"] += 1
        if out:
            st["segments_kept"] += out["report"]["kept"]
            st["segments_dropped"] += len(out["report"]["dropped"])
        if n % 25 == 0:
            typer.echo(f"{n}/{len(todo)} {st}")
    _echo(st)


@app.command("summary-stats")
def summary_stats():
    """Calidad de los resúmenes por versión de prompt: de las frases que revisó Haiku, qué parte quitó."""
    import json as _j
    from pathlib import Path as _P
    con = connect()
    by: dict[str, dict] = {}
    for o in con.execute("SELECT content_path, validation_report_json FROM outputs WHERE kind='summary'"):
        rep = _j.loads(o["validation_report_json"] or "{}")
        if not rep.get("haiku_checked"):
            continue
        c = _j.loads(_P(o["content_path"]).read_text())
        v = c.get("prompt") or "?"
        d = by.setdefault(v, {"summaries": 0, "kept": 0, "removed_by_judge": 0, "withdrawn": 0})
        d["summaries"] += 1
        d["kept"] += len(c["segments"])
        d["withdrawn"] += int(not c["segments"])
        d["removed_by_judge"] += sum(1 for x in rep.get("dropped", []) if any(f.startswith("el juez responde") for f in x["failures"]))
    for v, d in by.items():
        t = d["kept"] + d["removed_by_judge"]
        d["removal_rate"] = round(d["removed_by_judge"] / t, 3) if t else None
    _echo(by)


@app.command("redo-haiku")
def redo_haiku(max_calls: int = 8, batch: int = 8, shard: int = 0, shards: int = 1, gaps: bool = False):
    """Rehace con Haiku la extracción de los pasajes de los que Qwen sacó datos (varios pasajes por llamada; respeta el tope diario)."""
    from .extract import redo_with_haiku
    con = connect()
    _echo(redo_with_haiku(con, default_llm(con), max_calls, batch, shard, shards, gaps))


@app.command("narratives")
def narratives(max_calls: int = 8, batch: int = 8, entities: int = 60, shard: int = 0, shards: int = 1):
    """Narrativas (anécdotas, génesis, recepción) de las fichas más ricas con Haiku, por lotes; se guarda la cita literal verificada."""
    from .extract import narratives_with_haiku
    con = connect()
    _echo(narratives_with_haiku(con, default_llm(con), max_calls, batch, entities, shard=shard, shards=shards))


@app.command("judge-summaries")
def judge_summaries(max_calls: int = 20, per_call: int = 3, prefer_prompt: Optional[str] = typer.Option(None, help="revisar primero los resúmenes de esta versión de prompt, p. ej. v2")):
    """Haiku revisa los resúmenes (una llamada por resumen), empezando por las fichas con más datos. Respeta el tope diario."""
    from . import generate as G
    con = connect()
    _echo(G.judge_summaries(con, default_llm(con), max_calls, prefer_prompt, per_call))


@app.command("check-summaries")
def check_summaries(calibrate: bool = False, redo: bool = False):
    """Segundo filtro local (Qwen) sobre los resúmenes. `--calibrate` lo compara con los veredictos de Haiku."""
    from . import generate as G
    con = connect()
    llm = default_llm(con)
    _echo(G.calibrate_local_check(con, llm) if calibrate else G.check_summaries(con, llm, redo))


@app.command()
def export():
    """Escribe la API estática en web/public/api/v1."""
    from .export import export_all
    _echo(export_all(connect()))


@app.command()
def check():
    """Controles previos al despliegue."""
    from .check import run_checks
    errs = run_checks(connect())
    if errs:
        for e in errs:
            typer.echo(f"✗ {e}")
        raise typer.Exit(1)
    typer.echo("✓ kb check en verde")


@app.command()
def estimate():
    """Llamadas LLM pendientes por motor y tarea."""
    con = connect()
    _echo(P.estimate(con, default_llm(con)))


@app.command("gold-draft")
def gold_draft(n: int = 40):
    """Genera gold/extract_claims.draft.yaml con pasajes reales y los hechos propuestos, para revisar a mano."""
    _echo(f"{P.gold_draft(connect(), n)} pasajes en gold/extract_claims.draft.yaml")


@app.command("eval")
def eval_(task: str = "extract_claims", engine: Optional[str] = None, silver: bool = False):
    """Evalúa contra el conjunto de oro (o la concordancia con Wikidata con --silver)."""
    con = connect()
    _echo(P.eval_silver(con) if silver else P.eval_gold(con, default_llm(con), task, engine))


@app.command()
def refresh(source: Optional[list[str]] = typer.Option(None, "--source"), dry_run: bool = False):
    """Refresco incremental: solo lo cambiado. --dry-run no toca nada."""
    con = connect()
    _echo(P.refresh(con, default_llm(con), source or None, dry_run))


if __name__ == "__main__":
    app()
