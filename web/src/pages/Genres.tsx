import type { ReactElement } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

interface G { id: string; label: string; n: number; broader: string[]; note: string }

export default function Genres() {
  const { data = [] } = useQuery({ queryKey: ["genres"], queryFn: () => api<G[]>("genres/index.json") });
  // los conceptos sin obras en la base se pliegan aparte (salvo que sean el padre de uno que sí tiene)
  const visible = data.filter((g) => g.n > 0 || data.some((o) => o.n > 0 && o.broader.includes(g.id)));
  const empty = data.filter((g) => !visible.includes(g));
  const top = visible.filter((g) => g.broader.length === 0 || !visible.some((o) => g.broader.includes(o.id)));
  const sub = (id: string) => visible.filter((g) => g.broader.includes(id));
  const row = (g: G) => (
    <span key={g.id}>
      <Link to={`/genero/${g.id}`}>{g.label}</Link> <span className="n">{g.n} {g.n === 1 ? "obra" : "obras"}</span>
      {g.note && <small className="nota-g"> — {g.note.length >= 200 ? g.note.slice(0, 197) + "…" : g.note}</small>}
    </span>
  );
  const node = (g: G, depth = 0): ReactElement => (
    <li key={g.id}>
      {row(g)}
      {sub(g.id).length > 0 && depth < 3 && <ul className="g-sub">{sub(g.id).map((c) => node(c, depth + 1))}</ul>}
    </li>
  );
  return (
    <section>
      <h1>Géneros <small>({data.length})</small></h1>
      <p>Cómo se clasifican las obras. Los nombres vienen de las propias fuentes (carteles y artículos), agrupados en conceptos que citan su origen.
        Muchas obras solo dicen «zarzuela»: es lo que traen los datos, no una simplificación de la web.</p>
      <ul className="lista-generos">{top.map((g) => <li key={g.id} className="g-top"><div className="g-uno">{row(g)}</div>{sub(g.id).length > 0 && <ul className="g-sub">{sub(g.id).map((c) => node(c, 1))}</ul>}</li>)}</ul>
      {empty.length > 0 && (
        <details className="sin-obras">
          <summary>Conceptos del vocabulario sin obras en la base ({empty.length})</summary>
          <p>Existen en la literatura y tienen su fuente, pero ninguna obra de nuestro corpus se denomina así.</p>
          <ul>{empty.map((g) => <li key={g.id}><Link to={`/genero/${g.id}`}>{g.label}</Link></li>)}</ul>
        </details>
      )}
    </section>
  );
}
