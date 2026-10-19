// Documentos digitalizados: libretos, partituras y prensa de fuentes abiertas (Europeana/Hispana, Biblioteca de Catalunya, Gallica, BVPH).
// Solo metadatos y enlaces al original: nada se almacena aquí.
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, DocItem } from "../api/client";

const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const year = (d: string | null) => Number((d ?? "").match(/\d{4}/)?.[0]) || 9999;

export function DocRow({ d }: { d: DocItem }) {
  return (
    <li className="doc">
      <a href={d.url} target="_blank" rel="noreferrer" className="doc-titulo">{d.title}</a>
      <div className="doc-meta">
        {[d.creators.join("; "), d.date, d.type].filter(Boolean).join(" · ")}
        {d.institution && <> · <em>{d.institution}</em></>}
      </div>
      <div className="doc-pie">
        <span className="derechos pd">{d.rights}</span> <span className="origen">{d.source_label}</span>
        {d.source === "europeana" && <> · <a href={d.record_url} target="_blank" rel="noreferrer">ver en Europeana</a></>}
        {d.entities.length > 0 && <> · sobre {d.entities.map((e, i) => <span key={e.id}>{i > 0 && ", "}<Link to={`/${e.route}/${e.id}`}>{e.label}</Link></span>)}</>}
      </div>
    </li>
  );
}

export default function Documents() {
  const { data = [] } = useQuery({ queryKey: ["documents"], queryFn: () => api<DocItem[]>("views/documents.json") });
  const [params] = useSearchParams();
  const entidad = params.get("entidad");
  const [q, setQ] = useState("");
  const [src, setSrc] = useState("");
  const [only, setOnly] = useState(false);
  const [n, setN] = useState(60);
  const sources = useMemo(() => [...new Map(data.map((d) => [d.source, d.source_label])).entries()], [data]);
  const rows = useMemo(() => {
    const f = strip(q);
    return data.filter((d) => (!entidad || d.entities.some((e) => e.id === entidad)) && (!src || d.source === src) && (!only || d.entities.length > 0)
      && (!f || strip(`${d.title} ${d.creators.join(" ")} ${d.subjects.join(" ")}`).includes(f)))
      .sort((a, b) => year(a.date) - year(b.date) || a.title.localeCompare(b.title, "es"));
  }, [data, q, src, only, entidad]);
  return (
    <section>
      <h1>Documentos digitalizados <small>({rows.length})</small></h1>
      <p>Libretos, partituras y otros fondos de bibliotecas con licencia abierta (CC0, dominio público o CC BY). Cada título enlaza al original en su institución; aquí solo guardamos los datos del registro.</p>
      {entidad && <p><Link to="/documentos">Ver todos los documentos</Link></p>}
      <div className="filtros">
        <input type="search" value={q} onChange={(e) => { setQ(e.target.value); setN(60); }} placeholder="Buscar por título, autor o materia…" aria-label="Buscar" />
        <label>Fuente <select value={src} onChange={(e) => setSrc(e.target.value)}><option value="">Todas</option>{sources.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Solo los enlazados a una ficha</label>
      </div>
      <ul className="lista-docs">{rows.slice(0, n).map((d) => <DocRow key={d.id} d={d} />)}</ul>
      {n < rows.length && <button onClick={() => setN(n + 100)}>Mostrar más ({rows.length - n} restantes)</button>}
    </section>
  );
}
