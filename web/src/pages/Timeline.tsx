// Cronograma: fechas imprecisas como intervalos (barra) y exactas como punto. Con tabla alternativa (accesibilidad).
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

interface TL { claim_id: string; entity_id: string; label: string; route: string; kind: string; edtf: string; from: number; to: number; status: string; alternatives: string[] }
const STATUS: Record<string, string> = { asserted: "documentado", disputed: "en disputa", attributed: "atribuido", legendary: "leyenda" };
const KINDS: Record<string, string> = { premiere_date: "Estrenos", birth_date: "Nacimientos", death_date: "Muertes", opening_date: "Aperturas", closing_date: "Cierres", revival_date: "Reposiciones" };

export default function Timeline() {
  const { data: all = [] } = useQuery({ queryKey: ["timeline"], queryFn: () => api<TL[]>("views/timeline.json") });
  const [kind, setKind] = useState("premiere_date");
  const [table, setTable] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 700px)").matches);   // en móvil, tabla por defecto: el gráfico SVG queda ilegible
  const seen = new Set<string>();   // el mismo hecho dado por varias fuentes se dibuja una vez
  const rows = all.filter((t) => t.kind === kind).sort((a, b) => a.from - b.from).filter((t) => {
    const k = `${t.entity_id}|${t.edtf}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const min = Math.min(1650, ...rows.map((r) => r.from)), max = Math.max(1960, ...rows.map((r) => r.to));
  const W = 1000, x = (y: number) => 40 + ((y - min) / (max - min)) * (W - 200);   // margen derecho para las etiquetas
  // carriles: cada elemento va al primer carril donde su etiqueta no solapa con la anterior
  const laneEnd: number[] = [];
  const lane = rows.map((r) => {
    const start = x(r.from) - 6, end = x(r.to) + 12 + r.label.length * 5.2;
    let l = laneEnd.findIndex((e) => e < start);
    if (l < 0) { l = laneEnd.length; laneEnd.push(0); }
    laneEnd[l] = end;
    return l;
  });
  const H = Math.max(120, laneEnd.length * 16 + 40);
  return (
    <section>
      <h1>Cronología</h1>
      <div className="filtros">
        {Object.entries(KINDS).map(([k, l]) => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
        <button onClick={() => setTable(!table)}>{table ? "Ver gráfico" : "Ver tabla"}</button>
      </div>
      {table ? (
        <table><thead><tr><th>Fecha</th><th>Entidad</th><th>Estado</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.claim_id}><td>{r.edtf}</td><td><Link to={`/${r.route}/${r.entity_id}`}>{r.label}</Link></td><td>{STATUS[r.status] ?? r.status}{r.alternatives.length > 0 && <small> · otras versiones: {r.alternatives.join(", ")}</small>}</td></tr>)}</tbody></table>
      ) : (
        <svg className="grafico" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Cronología de ${KINDS[kind]}`}>
          {Array.from({ length: Math.ceil((max - min) / 50) + 1 }, (_, i) => min + i * 50).map((y) => (
            <g key={y}><line x1={x(y)} x2={x(y)} y1={20} y2={H} stroke="currentColor" opacity=".12" /><text x={x(y)} y={14} fontSize="10" textAnchor="middle">{y}</text></g>
          ))}
          {rows.map((r, i) => (
            <a key={r.claim_id} href={`#/${r.route}/${r.entity_id}`}>
              <title>{r.label} · {r.edtf}{r.alternatives.length > 0 ? ` (otras versiones: ${r.alternatives.join(", ")})` : ""}</title>
              {r.to > r.from ? <rect x={x(r.from)} y={28 + lane[i] * 16} width={Math.max(4, x(r.to) - x(r.from))} height={8} rx="4" className={`barra ${r.status}`} />
                : <circle cx={x(r.from)} cy={32 + lane[i] * 16} r="4" className={`punto ${r.status}`} />}
              <text x={x(r.to) + 8} y={36 + lane[i] * 16} fontSize="10">{r.label}</text>
            </a>
          ))}
        </svg>
      )}
    </section>
  );
}
