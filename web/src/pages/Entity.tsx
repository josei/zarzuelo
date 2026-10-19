import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ClaimOut, EntityOut, IndexItem, ScoreFile, VideoOut } from "../api/client";
import { DocRow } from "./Documents";
import { Apuntador } from "../components/Apuntador";

const PROP: Record<string, string> = {
  composer: "Música", librettist: "Libreto", premiere_date: "Estreno", premiere_venue: "Teatro del estreno", genre: "Género",
  denomination: "Denominación original", birth_date: "Nacimiento", death_date: "Muerte", birth_place: "Lugar de nacimiento",
  death_place: "Lugar de muerte", opening_date: "Inauguración", closing_date: "Cierre", location: "Lugar", genre_concept: "Concepto de género",
  publication_date: "Publicación", acts: "Actos", company: "Compañía", performer: "Intérprete", role: "Papel", based_on: "Basada en", revival_date: "Reposición",
};
const ROUTE = (id: string) => `/obra/${id}`;

function valueOf(c: ClaimOut) {
  if (c.value_type === "entity") return c.value_label ?? c.value;
  return c.value;
}

function ScoreRow({ f }: { f: ScoreFile }) {
  const pd = f.copyright === "Public Domain";
  return (
    <li>
      <a href={f.url} target="_blank" rel="noreferrer">{f.desc || f.name}</a>
      {f.publisher && <small> · {f.publisher}</small>}{f.editor && <small> · ed. {f.editor}</small>}
      <span className={`derechos ${pd ? "pd" : "otro"}`}>{pd ? "dominio público" : f.copyright || "derechos sin indicar"}</span>
    </li>
  );
}

export default function Entity({ kind }: { kind: "works" | "people" | "venues" }) {
  const { id = "" } = useParams();
  const { data: e, error } = useQuery({ queryKey: [kind, id], queryFn: () => api<EntityOut>(`${kind}/${id}.json`) });
  const { data: works = [] } = useQuery({ queryKey: ["works"], queryFn: () => api<IndexItem[]>("works/index.json"), enabled: kind === "works" });
  if (error) return <p className="error">No se pudo cargar la ficha.</p>;
  if (!e) return <p>Cargando…</p>;
  const byProp: Record<string, ClaimOut[]> = {};
  for (const c of e.claims) if (c.status !== "deprecated") (byProp[c.property] ??= []).push(c);
  const groups = Object.entries(byProp);
  const refLabel = (r: string) => (r.startsWith("http") ? new URL(r).hostname : r);
  const composers = e.claims.filter((c) => c.property === "composer").map((c) => c.value_label);
  const hilo = kind === "works" ? works.filter((w) => w.id !== id && w.composer?.some((c) => composers.includes(c))).slice(0, 6) : [];
  return (
    <article className="ficha">
      <h1 className="cartel">{e.label}</h1>
      <p className="alt">{e.labels_alt.join(" · ")}</p>
      {e.summary && (
        <section className="resumen">
          <h2>{kind === "works" ? "La obra en pocas líneas" : kind === "people" ? "En pocas líneas" : "El teatro en pocas líneas"}</h2>
          <p>
            {e.summary.segments.map((sg, i) => (
              <span key={i}>
                {sg.parts.map((pt, j) => pt.to ? <Link key={j} to={pt.to}>{pt.t}</Link> : pt.year ? <mark key={j} className="anio-res">{pt.t}</mark> : <span key={j}>{pt.t}</span>)}
                <Apuntador.Shell entityId={e.id} ids={sg.evidence_ids} status="asserted" families={["wikimedia"]} />{" "}
              </span>
            ))}
          </p>
          <p className="atribucion">
            {e.summary.verified
              ? "Resumen generado por un modelo de lenguaje a partir del texto y los datos citados, y revisado por un segundo modelo independiente: solo se muestran las frases que respalda su cita."
              : "Resumen generado por un modelo de lenguaje local a partir del texto y los datos citados. Cada frase pasó un control automático de nombres, cifras y citas, pero aún no la ha revisado un segundo modelo: puede contener imprecisiones."}
            {" "}Pulsa la concha para ver el fragmento de origen; lo fiable es el texto de abajo.
          </p>
        </section>
      )}
      {e.biography && (
        <details className="relato" open={!e.summary}>
          <summary><h2>{e.summary ? "Leer el texto completo de Wikipedia" : kind === "people" ? "Biografía" : kind === "works" ? "Sobre la obra" : "Sobre el teatro"}</h2></summary>
          {e.biography.paragraphs.map((p) => (
            <p key={p.passage_id}>
              {p.segments.map((sg, i) => sg.to ? <Link key={i} to={sg.to}>{sg.t}</Link> : sg.year ? <mark key={i} className="anio-res">{sg.t}</mark> : <span key={i}>{sg.t}</span>)}
              <Apuntador.Shell entityId={e.id} ids={[p.passage_id]} status="asserted" families={["wikimedia"]} />
            </p>
          ))}
          <p className="atribucion">
            Texto de <a href={e.biography.url} target="_blank" rel="noreferrer">Wikipedia</a> (revisión {e.biography.revision}, consultada {e.biography.retrieved_at.slice(0, 10)}),
            bajo licencia <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.es" target="_blank" rel="noreferrer">CC BY-SA 4.0</a>;
            <a href={e.biography.history_url} target="_blank" rel="noreferrer"> autores en el historial</a>. Los nombres enlazados llevan a otras fichas de Zarzuelo.
          </p>
        </details>
      )}
      <h2 className="datos-h">Datos</h2>
      <dl>
        {groups.map(([prop, cs]) => (
          <div key={prop}>
            <dt>{PROP[prop] ?? prop}</dt>
            <dd>
              <ul>
                {cs.filter((c) => !c.alternative).map((c) => (
                  <li key={c.id} className={`estado-${c.status}`}>
                    {c.hedge && <em>{c.hedge} </em>}
                    {c.value_link ? <Link to={`/${c.value_link}/${c.value}`}>{valueOf(c)}</Link> : valueOf(c)}
                    <Apuntador.Shell entityId={e.id} ids={c.evidence_ids} status={c.status} hedge={c.hedge} families={c.source_families} />
                  </li>
                ))}
              </ul>
              {cs.some((c) => c.alternative) && (
                <details className="alternativas">
                  <summary>Otras versiones en las fuentes ({cs.filter((c) => c.alternative).length})</summary>
                  <ul>
                    {cs.filter((c) => c.alternative).map((c) => (
                      <li key={c.id} className="estado-disputed">
                        {c.value_link ? <Link to={`/${c.value_link}/${c.value}`}>{valueOf(c)}</Link> : valueOf(c)}
                        <small> {c.wikidata_rank === "normal" ? "(Wikidata, no preferida)" : ""} {c.references.length ? `· ref.: ${c.references.map(refLabel).join(", ")}` : ""}</small>
                        <Apuntador.Shell entityId={e.id} ids={c.evidence_ids} status={c.status} hedge={c.hedge} families={c.source_families} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {e.authored && e.authored.length > 0 && (
        <section className="obras-autor">
          <h2>Obras <small>({e.authored.length})</small></h2>
          <ul className="lista-obras">
            {e.authored.map((w) => (
              <li key={w.id}>
                <Link to={`/obra/${w.id}`}>{w.label}</Link>
                {w.year ? <small> {w.year}</small> : null}
                <small className="rol-obra"> · {w.roles.map((r) => (r === "composer" ? "música" : "libreto")).join(" y ")}</small>
              </li>
            ))}
          </ul>
        </section>
      )}
      {e.scores && e.scores.length > 0 && (
        <section className="partituras">
          <h2>Partituras <small>({e.scores.reduce((n, sc) => n + sc.files.length, 0)})</small></h2>
          {e.scores.map((sc) => (
            <div key={sc.page_url}>
              <p className="atribucion">Según <a href={sc.page_url} target="_blank" rel="noreferrer">{sc.source}</a>. Se enlazan, no se almacenan: cada fichero tiene su propio estado de derechos.</p>
              {Object.entries(sc.files.reduce<Record<string, ScoreFile[]>>((m, f) => { (m[f.edition] ??= []).push(f); return m; }, {})).map(([ed, fs]) => (
                <div key={ed} className="edicion">
                  <h3>{ed.replace(" · ", " · ")} <small>({fs.length})</small></h3>
                  <ul className="lista-partituras">{fs.slice(0, 8).map((f) => <ScoreRow key={f.name} f={f} />)}</ul>
                  {fs.length > 8 && <details><summary>Ver los {fs.length - 8} restantes</summary><ul className="lista-partituras">{fs.slice(8).map((f) => <ScoreRow key={f.name} f={f} />)}</ul></details>}
                </div>
              ))}
            </div>
          ))}
        </section>
      )}
      {e.videos && e.videos.length > 0 && (
        <section className="videos">
          <h2>Vídeos <small>({e.videos.length})</small></h2>
          <p className="atribucion">Enlazados desde <a href={e.videos[0].article_url} target="_blank" rel="noreferrer">el artículo de Wikipedia</a>; pueden ser fragmentos, entrevistas o referencias, no necesariamente la obra completa. No se almacenan: la miniatura y el vídeo se piden a YouTube.</p>
          <div className="rejilla-videos">{e.videos.map((v) => <VideoCard key={v.id} v={v} />)}</div>
        </section>
      )}
      {e.documents && e.documents.length > 0 && (
        <section className="documentos">
          <h2>Documentos digitalizados <small>({e.documents_total})</small></h2>
          <p className="atribucion">Libretos, partituras y otros fondos con licencia abierta. Se enlazan al original; no se almacenan.</p>
          <ul className="lista-docs">{e.documents.map((d) => <DocRow key={d.id} d={d} />)}</ul>
          {e.documents_total > e.documents.length && <p><Link to={`/documentos?entidad=${e.id}`}>Ver los {e.documents_total} documentos</Link></p>}
        </section>
      )}
      {e.rism && e.rism.length > 0 && (
        <section className="rism">
          <h2>Fuentes musicales en RISM <small>({e.rism_total})</small></h2>
          <p className="atribucion">Manuscritos e impresos conservados en archivos y bibliotecas, según <a href="https://rism.online" target="_blank" rel="noreferrer">RISM</a> (licencia CC BY-NC-SA: solo consulta; se muestra la referencia y se enlaza).</p>
          <ul className="lista-docs">{e.rism.map((r) => <li key={r.url} className="doc"><a href={r.url} target="_blank" rel="noreferrer">{r.label}</a></li>)}</ul>
          {e.rism_url && <p><a href={e.rism_url} target="_blank" rel="noreferrer">Ver todas en RISM</a></p>}
        </section>
      )}
      {e.narratives.length > 0 && (
        <section>
          <h2>Anécdotas y contexto</h2>
          {e.narratives.map((n) => (
            <div key={n.id} className={`narrativa narr-${n.status}`}>
              <h3>{n.title} {n.status === "legendary" && <span className="sello">leyenda</span>}</h3>
              <p>{n.summary}<Apuntador.Shell entityId={e.id} ids={n.evidence_ids} status={n.status} /></p>
            </div>
          ))}
        </section>
      )}
      {hilo.length > 0 && (
        <section>
          <h2>Tirar del hilo</h2>
          <ul>{hilo.map((w) => <li key={w.id}><Link to={ROUTE(w.id)}>{w.label}</Link> <small>mismo compositor</small></li>)}</ul>
        </section>
      )}
    </article>
  );
}

export function VideoCard({ v }: { v: VideoOut }) {
  const [on, setOn] = useState(false);
  const [sinMiniatura, setSinMiniatura] = useState(false);
  const pie = (v.title || v.channel) && (
    <p className="video-pie">{v.title && <a href={`https://www.youtube.com/watch?v=${v.id}`} target="_blank" rel="noreferrer">{v.title}</a>}{v.channel && <small> · {v.channel}</small>}</p>
  );
  if (on) {
    return (
      <figure className="video-tarjeta">
        <div className="video"><iframe src={`https://www.youtube-nocookie.com/embed/${v.id}?autoplay=1`} title={v.title ?? "Vídeo de YouTube"} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen loading="lazy" /></div>
        {pie}
      </figure>
    );
  }
  return (
    <figure className="video-tarjeta">
      <div className={sinMiniatura ? "video compacto" : "video"}>
        <button type="button" onClick={() => (v.embeddable ? setOn(true) : window.open(`https://www.youtube.com/watch?v=${v.id}`, "_blank", "noopener"))} aria-label={v.embeddable ? "Cargar vídeo de YouTube" : "Ver en YouTube"}>
          {!sinMiniatura && <img src={`https://i.ytimg.com/vi/${v.id}/hqdefault.jpg`} alt="" loading="lazy" onError={() => setSinMiniatura(true)} />}
          <span className="reproducir">▶{sinMiniatura ? (v.embeddable ? " Cargar vídeo" : " Ver en YouTube") : ""}</span>
        </button>
      </div>
      {pie}
      {!v.embeddable && <p className="video-pie"><small>Se abre en YouTube (el autor no permite incrustarlo).</small></p>}
    </figure>
  );
}
