import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, IndexItem } from "../api/client";

const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

export default function Obras() {
  const { data = [] } = useQuery({ queryKey: ["works"], queryFn: () => api<IndexItem[]>("works/index.json") });
  const [params] = useSearchParams();
  const desde = Number(params.get("desde")) || null, hasta = Number(params.get("hasta")) || null;   // filtro por época (años de estreno)
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"anio" | "titulo">("anio");
  const [n, setN] = useState(100);
  const rows = useMemo(() => {
    const f = strip(q);
    const yr = (w: IndexItem) => Number(w.year?.slice(0, 4)) || null;
    const r = data.filter((w) => (!f || strip(w.label).includes(f) || (w.composer ?? []).some((c) => c && strip(c).includes(f)))
      && (!desde || (yr(w) !== null && yr(w)! >= desde)) && (!hasta || (yr(w) !== null && yr(w)! <= hasta)));
    return [...r].sort((a, b) => sort === "titulo" ? a.label.localeCompare(b.label, "es")
      : (Number(a.year?.slice(0, 4)) || 9999) - (Number(b.year?.slice(0, 4)) || 9999) || a.label.localeCompare(b.label, "es"));
  }, [data, q, sort, desde, hasta]);
  return (
    <section>
      <h1>{desde || hasta ? `Títulos de ${desde ?? "…"} a ${hasta ?? "…"}` : "Todos los títulos"} <small>({rows.length})</small></h1>
      {(desde || hasta) && <p><Link to="/obras">Ver todos los títulos</Link></p>}
      <div className="filtros">
        <input type="search" value={q} onChange={(e) => { setQ(e.target.value); setN(100); }} placeholder="Filtrar por título o compositor…" aria-label="Filtrar" />
        <button aria-pressed={sort === "anio"} onClick={() => setSort("anio")}>Por año</button>
        <button aria-pressed={sort === "titulo"} onClick={() => setSort("titulo")}>Por título</button>
      </div>
      <ul className="lista-obras">
        {rows.slice(0, n).map((w) => (
          <li key={w.id}>
            <span className="anio">{w.year?.slice(0, 4) ?? "s. f."}</span> <Link to={`/obra/${w.id}`}>{w.label}</Link>
            {w.composer && w.composer.length > 0 && <small className="autor"> · {[...new Set(w.composer)].join(", ")}</small>}
          </li>
        ))}
      </ul>
      {n < rows.length && <button onClick={() => setN(n + 200)}>Mostrar más ({rows.length - n} restantes)</button>}
    </section>
  );
}
