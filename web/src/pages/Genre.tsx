import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, fname } from "../api/client";

interface W { id: string; label: string; year: number | null; composers: string[] }
interface G {
  id: string; pref_label: string; alt_labels: string[]; scope_note: string | null; scope_source: string | null; note_kind: "definicion" | "uso" | "wikidata"; license: string;
  broader: { id: string; label: string }[]; narrower: { id: string; label: string }[]; denominations: { label: string; count: number }[]; works: W[];
  by_decade: { decade: number; n: number }[];
}

export default function Genre() {
  const { id = "" } = useParams();
  const { data: g } = useQuery({ queryKey: ["genre", id], queryFn: () => api<G>(`genres/${fname(id)}.json`) });
  if (!g) return <p>Cargando…</p>;
  const max = Math.max(1, ...g.by_decade.map((d) => d.n));
  return (
    <article className="ficha">
      <p className="migas"><Link to="/generos">Géneros</Link>{g.broader.map((b) => <span key={b.id}> › <Link to={`/genero/${b.id}`}>{b.label}</Link></span>)}</p>
      <h1 className="cartel">{g.pref_label}</h1>
      {g.alt_labels.length > 0 && <p className="alt">También: {g.alt_labels.join(" · ")}</p>}
      {g.scope_note && (
        <section className="relato">
          <h2>{g.note_kind === "definicion" ? "Qué es" : "Cómo lo usan las fuentes"}</h2>
          <p>{g.scope_note}</p>
          {g.scope_source && <p className="atribucion">
            {g.note_kind === "uso" && "Un pasaje donde se usa el término (no es una definición). "}
            Fuente: <a href={g.scope_source} target="_blank" rel="noreferrer">{g.scope_source.includes("wikipedia.org") ? "Wikipedia (revisión archivada)" : "Wikidata"}</a>, licencia {g.license}.
          </p>}
        </section>
      )}
      {g.denominations.length > 0 && (
        <section>
          <h2>Cómo se las llama en las fuentes</h2>
          <ul className="denominaciones">{g.denominations.map((d) => <li key={d.label}>«{d.label}» <span className="n">{d.count}</span></li>)}</ul>
        </section>
      )}
      {g.narrower.length > 0 && <p><strong>Subgéneros:</strong> {g.narrower.map((n, i) => <span key={n.id}>{i > 0 && ", "}<Link to={`/genero/${n.id}`}>{n.label}</Link></span>)}</p>}
      {g.by_decade.length > 1 && (
        <section>
          <h2>Cuándo se estrenan</h2>
          <div className="decadas" role="img" aria-label="Estrenos por década">
            {g.by_decade.map((d) => <div key={d.decade} className="dec" title={`${d.decade}s: ${d.n}`}><i style={{ height: `${(d.n / max) * 100}%` }} /><span>{d.decade}</span></div>)}
          </div>
        </section>
      )}
      <h2>Obras <small>({g.works.length})</small></h2>
      <ul className="lista-obras">
        {g.works.map((w) => (
          <li key={w.id}><span className="anio">{w.year ?? "s. f."}</span> <Link to={`/obra/${w.id}`}>{w.label}</Link>
            {w.composers.length > 0 && <small className="autor"> · {w.composers.join(", ")}</small>}</li>
        ))}
      </ul>
      {g.works.length === 0 && <p>Aún no hay obras asignadas a este género.</p>}
    </article>
  );
}
