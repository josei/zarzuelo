// La compañía: colaboraciones compositor ↔ libretista, de una persona cada vez (red centrada), para poder navegarla.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { api } from "../api/client";

interface Node { id: string; label: string; works: number; roles: string[]; degree: number }
interface Edge { source: string; target: string; weight: number; works: { id: string; label: string; year: number | null }[] }
interface Collab { nodes: Node[]; edges: Edge[] }
const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

export default function Network() {
  const { data } = useQuery({ queryKey: ["collab"], queryFn: () => api<Collab>("views/collaborations.json") });
  const [focus, setFocus] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const byId = useMemo(() => Object.fromEntries((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const current = focus ?? data?.nodes[0]?.id ?? null;       // por defecto, la persona con más colaboradores
  const go = (id: string) => { if (current) setHistory((h) => [...h, current]); setFocus(id); };
  const back = () => setHistory((h) => { const x = h[h.length - 1]; if (x) setFocus(x); return h.slice(0, -1); });
  const mine = useMemo(() => (data?.edges ?? []).filter((e) => e.source === current || e.target === current), [data, current]);

  useEffect(() => {
    if (!current || !ref.current) return;
    const edges = data?.edges ?? [];
    const lvl1 = new Set<string>([current]);
    mine.forEach((e) => { lvl1.add(e.source); lvl1.add(e.target); });
    // segundo nivel: colaboradores de los colaboradores (los más trabajadores de cada uno), translúcidos, para invitar a seguir navegando
    const lvl2 = new Set<string>(); const lvl2Edges: Edge[] = [];
    for (const n of lvl1) {
      if (n === current) continue;
      const cand = edges.filter((e) => (e.source === n || e.target === n)).map((e) => ({ e, other: e.source === n ? e.target : e.source }))
        .filter((x) => !lvl1.has(x.other)).sort((a, b) => b.e.weight - a.e.weight || (byId[b.other]?.degree ?? 0) - (byId[a.other]?.degree ?? 0)).slice(0, 3);
      for (const x of cand) { if (lvl2.size >= 60 && !lvl2.has(x.other)) continue; lvl2.add(x.other); lvl2Edges.push(x.e); }
    }
    const among = edges.filter((e) => lvl1.has(e.source) && lvl1.has(e.target) && e.source !== current && e.target !== current);
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...[...lvl1].map((id) => ({ data: { id, label: byId[id]?.label ?? id, lvl: id === current ? 0 : 1, deg: byId[id]?.works ?? 1 } })),
        ...[...lvl2].map((id) => ({ data: { id, label: byId[id]?.label ?? id, lvl: 2, deg: byId[id]?.works ?? 1 } })),
        ...[...mine, ...among].map((e, i) => ({ data: { id: `a${i}`, source: e.source, target: e.target, w: e.weight, lvl: 1 } })),
        ...lvl2Edges.map((e, i) => ({ data: { id: `b${i}`, source: e.source, target: e.target, w: e.weight, lvl: 2 } })),
      ],
      layout: { name: "concentric", concentric: (n) => 3 - (n.data("lvl") as number), levelWidth: () => 1, minNodeSpacing: 22, animate: false },
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 11, "text-wrap": "wrap", "text-max-width": "90", "background-color": "#2B5BA8", color: "#20201E", width: "mapData(deg, 1, 30, 14, 34)", height: "mapData(deg, 1, 30, 14, 34)" } },
        { selector: "node[lvl = 0]", style: { "background-color": "#C4213B", "font-weight": 700, "font-size": 13 } },
        { selector: "node[lvl = 2]", style: { opacity: 0.4, "font-size": 9, "text-opacity": 0.75, "background-color": "#5d7fb8" } },
        { selector: "edge", style: { width: "mapData(w, 1, 8, 1, 5)", "line-color": "#999", opacity: 0.7 } },
        { selector: "edge[lvl = 2]", style: { opacity: 0.25, "line-style": "dashed", width: 1 } },
        { selector: "node:active, node:selected", style: { "overlay-opacity": 0.08 } },
      ],
    });
    cy.on("tap", "node", (e) => { const id = e.target.id(); if (id !== current) go(id); });
    cy.on("mouseover", "node", (e) => { e.target.style("opacity", 1); ref.current && (ref.current.style.cursor = "pointer"); });
    cy.on("mouseout", "node", (e) => { e.target.removeStyle("opacity"); ref.current && (ref.current.style.cursor = "default"); });
    return () => cy.destroy();
  }, [data, current, mine, byId]);

  if (!data || !current) return <p>Cargando…</p>;
  const hits = q.length > 1 ? data.nodes.filter((n) => strip(n.label).includes(strip(q))).slice(0, 8) : [];
  const me = byId[current];
  const partner = (e: Edge) => byId[e.source === current ? e.target : e.source];
  return (
    <section>
      <h1>La compañía</h1>
      <p>Quién ha trabajado con quién (compositor ↔ libretista). Pulsa un nombre del gráfico para ir a su entorno.</p>
      <div className="busca-persona">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar una persona…" aria-label="Buscar persona" />
        {hits.length > 0 && <ul>{hits.map((h) => <li key={h.id}><button onClick={() => { go(h.id); setQ(""); }}>{h.label} <small>{h.degree} colaboradores</small></button></li>)}</ul>}
      </div>
      <h2><Link to={`/persona/${current}`}>{me?.label}</Link> <small>{me?.roles.map((r) => (r === "composer" ? "compositor" : "libretista")).join(" y ")} · {mine.length} colaboradores</small></h2>
      <div className="red-barra">
        {history.length > 0 && <button className="enlace" onClick={back}>← Volver a {byId[history[history.length - 1]]?.label}</button>}
        <span className="leyenda"><i className="dot rojo" /> persona elegida <i className="dot azul" /> colaboradores <i className="dot tenue" /> los colaboradores de sus colaboradores: púlsalos para seguir navegando</span>
      </div>
      <div ref={ref} className="red" role="img" aria-label={`Colaboradores de ${me?.label}`} />
      <h3>Colaboraciones</h3>
      <ul className="colabs">
        {[...mine].sort((a, b) => b.weight - a.weight).map((e) => {
          const p = partner(e);
          return (
            <li key={e.source + e.target}>
              <button className="enlace" onClick={() => go(p.id)}>{p.label}</button> — {e.works.sort((a, b) => (a.year ?? 0) - (b.year ?? 0)).map((w, i) => (
                <span key={w.id}>{i > 0 && ", "}<Link to={`/obra/${w.id}`}>{w.label}</Link>{w.year ? ` (${w.year})` : ""}</span>))}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
