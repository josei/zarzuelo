// La compañía: quién ha trabajado con quién (compositor ↔ libretista). Burbujas con área proporcional al nº de obras, sin lienzo con desplazamiento:
// al elegir a alguien se iluminan sus colaboradores y aparece su ficha. Un deslizador de años permite ver cómo crece la compañía.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { hierarchy, pack } from "d3-hierarchy";
import { api } from "../api/client";

interface Node { id: string; label: string; works: number; roles: string[]; degree: number }
interface Work { id: string; label: string; year: number | null }
interface Edge { source: string; target: string; weight: number; works: Work[] }
interface Collab { nodes: Node[]; edges: Edge[] }

const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
type D = Node & { children?: D[] };
const SIZE = 1000;
const rolOf = (n: Node) => (n.roles.length > 1 ? "ambos" : n.roles[0] === "composer" ? "compositor" : "libretista");
const rolTxt = (n: Node) => (n.roles.length > 1 ? "compositor y libretista" : n.roles[0] === "composer" ? "compositor" : "libretista");

/** Nombre corto para la burbuja: el nombre completo si cabe, si no el último apellido. */
function corto(label: string, r: number): string[] {
  const p = label.split(" ");
  if (r >= 58 && p.length > 1) return [p.slice(0, Math.ceil(p.length / 2)).join(" "), p.slice(Math.ceil(p.length / 2)).join(" ")];
  if (r >= 30) return [p[p.length - 1]];
  return [];
}

/** Nombre completo en una o dos líneas (para el rótulo de las burbujas resaltadas). */
function nombre2(label: string): string[] {
  const p = label.split(" ");
  if (p.length < 3) return [label];
  const k = Math.ceil(p.length / 2);
  return [p.slice(0, k).join(" "), p.slice(k).join(" ")];
}

export default function Network() {
  const { data } = useQuery({ queryKey: ["collab"], queryFn: () => api<Collab>("views/collaborations.json") });
  const [sel, setSel] = useState<string | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [play, setPlay] = useState(false);
  const [q, setQ] = useState("");
  const [movil, setMovil] = useState(() => window.matchMedia("(max-width: 800px)").matches);
  useEffect(() => { const m = window.matchMedia("(max-width: 800px)"); const f = () => setMovil(m.matches); m.addEventListener("change", f); return () => m.removeEventListener("change", f); }, []);

  const byId = useMemo(() => Object.fromEntries((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const years = useMemo(() => (data?.edges ?? []).flatMap((e) => e.works.map((w) => w.year).filter((y): y is number => !!y)), [data]);
  const [minY, maxY] = useMemo(() => [Math.min(...years, 9999), Math.max(...years, 0)], [years]);
  const now = year ?? maxY;
  // primer año de actividad de cada persona (sin datos de año: visible siempre)
  const first = useMemo(() => {
    const m: Record<string, number> = {};
    for (const e of data?.edges ?? []) for (const w of e.works) if (w.year) for (const id of [e.source, e.target]) m[id] = Math.min(m[id] ?? 9999, w.year);
    return m;
  }, [data]);
  const visible = (id: string) => (first[id] ?? 0) <= now;

  const circles = useMemo(() => {
    if (!data) return [];
    const root = hierarchy<D>({ children: data.nodes } as unknown as D).sum((d) => (d.children ? 0 : Math.max(d.works, 1) + 1)).sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return pack<D>().size([SIZE, SIZE]).padding(3)(root).leaves().map((l) => ({ n: l.data as Node, x: l.x, y: l.y, r: l.r }));
  }, [data]);

  const current = sel ?? data?.nodes[0]?.id ?? null;
  const mine = useMemo(() => (data?.edges ?? []).filter((e) => e.source === current || e.target === current), [data, current]);
  const partners = useMemo(() => new Set(mine.map((e) => (e.source === current ? e.target : e.source))), [mine, current]);

  const resaltadas = useMemo(() => {
    const base = circles.filter(({ n }) => (n.id === current || partners.has(n.id)) && visible(n.id)).sort((a, b) => b.r - a.r);
    // los rótulos se separan unos de otros (relajación sencilla sobre cajas estimadas) para que no se pisen
    const fs0 = movil ? 30 : 21;
    const L = base.map((c) => { const main = c.n.id === current; const fs = (main ? 1.45 : 1) * fs0; const lines = nombre2(c.n.label); return { c, main, w: Math.max(...lines.map((t) => t.length)) * fs * 0.52, h: lines.length * fs * 1.1, lx: c.x, ly: c.y }; });
    for (let it = 0; it < 80; it++) {
      let moved = false;
      for (let i = 0; i < L.length; i++) for (let j = i + 1; j < L.length; j++) {
        const A = L[i], B = L[j];
        const ox = (A.w + B.w) / 2 - Math.abs(A.lx - B.lx), oy = (A.h + B.h) / 2 - Math.abs(A.ly - B.ly);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        if (oy <= ox) { const d = (A.ly <= B.ly ? 1 : -1) * (oy / 2 + 0.5); if (!A.main) A.ly -= d; if (!B.main) B.ly += d; }
        else { const d = (A.lx <= B.lx ? 1 : -1) * (ox / 2 + 0.5); if (!A.main) A.lx -= d; if (!B.main) B.lx += d; }
      }
      if (!moved) break;
    }
    return L.map(({ c, lx, ly }) => ({ ...c, lx: lx - c.x, ly: ly - c.y }));
  }, [circles, current, partners, now, movil]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {                                   // «▶ Ver la historia»: avanza los años hasta el final
    if (!play) return;
    const step = Math.max(1, Math.round((maxY - minY) / 90));
    const t = setInterval(() => setYear((y) => { const v = (y ?? minY) + step; if (v >= maxY) { setPlay(false); return null; } return v; }), 130);
    return () => clearInterval(t);
  }, [play, minY, maxY]);

  if (!data || !current) return <p>Cargando…</p>;
  const hits = q.length > 1 ? data.nodes.filter((n) => strip(n.label).includes(strip(q))).slice(0, 8) : [];
  const me = byId[current];
  const nVisibles = mine.filter((e) => e.works.some((w) => !w.year || w.year <= now)).length;
  const shown = data.nodes.filter((n) => visible(n.id)).length;
  const pick = (id: string) => { setSel(id); setPlay(false); };

  return (
    <section className="compania">
      <h1>La compañía</h1>
      <p>Cada burbuja es una persona; su tamaño, las obras que firmó. <span className="clave"><i className="b comp" /> compositores</span> <span className="clave"><i className="b libr" /> libretistas</span> <span className="clave"><i className="b amb" /> ambas cosas</span>. Toca una para ver con quién trabajó.</p>
      <div className="busca-persona">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar una persona…" aria-label="Buscar persona" />
        {hits.length > 0 && <ul>{hits.map((h) => <li key={h.id}><button onClick={() => { pick(h.id); setQ(""); }}>{h.label} <small>{h.degree} colaboradores</small></button></li>)}</ul>}
      </div>
      <div className="tiempo">
        <button type="button" onClick={() => { if (!play) setYear(minY); setPlay((p) => !p); }} aria-pressed={play}>{play ? "❚❚ Pausa" : "▶ Ver la historia"}</button>
        <input type="range" min={minY} max={maxY} value={now} onChange={(e) => { setPlay(false); setYear(Number(e.target.value)); }} aria-label="Hasta el año" />
        <output>hasta {now} · {shown} personas</output>
      </div>
      <div className="compania-cuerpo">
        <svg className="burbujas" viewBox={`0 0 ${SIZE} ${SIZE}`} role="group" aria-label="Compositores y libretistas">
          {circles.map(({ n, x, y, r }) => {
            const on = visible(n.id);
            const state = n.id === current ? "elegida" : partners.has(n.id) ? "socia" : "";
            const lines = state ? [] : corto(n.label, r);
            return (
              <g key={n.id} className={`burbuja ${rolOf(n)} ${state ? "base-resaltada" : ""} ${on ? "" : "oculta"} ${current && !state ? "tenue" : ""}`} transform={`translate(${x},${y})`}
                 role="button" tabIndex={on && !state ? 0 : -1} aria-label={`${n.label}, ${rolTxt(n)}, ${n.works} obras`} aria-pressed={n.id === current}
                 onClick={() => on && pick(n.id)} onKeyDown={(e) => { if (on && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(n.id); } }}>
                <title>{n.label} · {n.works} obras</title>
                <circle r={r} />
                {lines.map((t, i) => <text key={i} y={(i - (lines.length - 1) / 2) * 1.05 * Math.min(18, r / 3.2) + Math.min(18, r / 3.2) * 0.35} fontSize={Math.min(18, r / 3.2)}>{t}</text>)}
              </g>
            );
          })}
          {resaltadas.map(({ n, x, y, r, lx, ly }, i) => {
            const main = n.id === current;
            const sc = main ? Math.max(1.15, 44 / r) : Math.min(3.4, Math.max(1.2, 27 / r));
            return (
              <g key={`${current}-${n.id}`} className={`resalte ${rolOf(n)} ${main ? "principal" : "socia"}`} transform={`translate(${x},${y})`} style={{ animationDelay: `${main ? 0 : 60 + i * 28}ms` }}
                 role="button" tabIndex={0} aria-label={`${n.label}, ${rolTxt(n)}, ${n.works} obras`} onClick={() => pick(n.id)}
                 onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(n.id); } }}>
                <title>{n.label} · {n.works} obras</title>
                <g className="pop" style={{ ["--s" as string]: sc, animationDelay: `${main ? 0 : 60 + i * 28}ms` }}>
                  {main && <circle className="aro" r={r + 5 / sc} />}
                  <circle className="disco" r={r} />
                </g>
              </g>
            );
          })}
          <g className="rotulos" aria-hidden="true">
            {resaltadas.map(({ n, lx, ly, x, y }, i) => {
              const main = n.id === current;
              const partes = nombre2(n.label);
              const fs = (main ? 1.45 : 1) * (movil ? 30 : 21);
              return (
                <text key={`${current}-${n.id}`} className="rotulo" fontSize={fs} style={{ animationDelay: `${main ? 0 : 60 + i * 28}ms` }}>
                  {partes.map((t, k) => <tspan key={k} x={x + lx} y={y + ly + (k - (partes.length - 1) / 2) * fs * 1.05 + fs * 0.35}>{t}</tspan>)}
                </text>
              );
            })}
          </g>
        </svg>
        <aside className="ficha-compania" aria-live="polite">
          <p className="rol">{rolTxt(me)}</p>
          <h2><Link to={`/persona/${current}`}>{me.label}</Link></h2>
          <p className="cifras"><b>{me.works}</b> obras · <b>{nVisibles}</b> {nVisibles === 1 ? "colaborador" : "colaboradores"}{nVisibles < mine.length && <small> (de {mine.length} en total)</small>}</p>
          <ul className="parejas">
            {[...mine].sort((a, b) => b.weight - a.weight || (byId[a.source === current ? a.target : a.source]?.label ?? "").localeCompare(byId[b.source === current ? b.target : b.source]?.label ?? "", "es")).map((e) => {
              const p = byId[e.source === current ? e.target : e.source];
              const ws = e.works.filter((w) => !w.year || w.year <= now).sort((a, b) => (a.year ?? 0) - (b.year ?? 0));
              if (ws.length === 0) return null;
              return (
                <li key={e.source + e.target} className={rolOf(p)}>
                  <button type="button" className="socio" onClick={() => pick(p.id)}>{p.label}</button>
                  <span className="n">{ws.length} {ws.length === 1 ? "obra" : "obras"}</span>
                  <div className="obras">{ws.map((w) => <Link key={w.id} to={`/obra/${w.id}`}>{w.label}{w.year ? <small> {w.year}</small> : null}</Link>)}</div>
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </section>
  );
}
