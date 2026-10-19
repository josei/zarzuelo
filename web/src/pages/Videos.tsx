// Vídeos de YouTube enlazados desde los artículos de Wikipedia de cada ficha. Solo se guarda el identificador; el vídeo se carga al pulsar.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, VideoOut } from "../api/client";
import { VideoCard } from "./Entity";

interface VideoGroup { id: string; label: string; type: string; route: string; article_url: string; videos: VideoOut[] }
const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const TIPOS: Record<string, string> = { work: "Obras", person: "Personas", venue: "Teatros" };

export default function Videos() {
  const { data = [] } = useQuery({ queryKey: ["videos"], queryFn: () => api<VideoGroup[]>("views/videos.json") });
  const [q, setQ] = useState("");
  const [tipo, setTipo] = useState("");
  const rows = useMemo(() => data.filter((g) => (!tipo || g.type === tipo) && (!q || strip(g.label).includes(strip(q)))), [data, q, tipo]);
  const total = rows.reduce((n, g) => n + g.videos.length, 0);
  return (
    <section>
      <h1>Vídeos <small>({total} en {rows.length} fichas)</small></h1>
      <p>Vídeos de YouTube que los editores de Wikipedia han enlazado desde el artículo de cada ficha. Pueden ser fragmentos, entrevistas o referencias, no necesariamente la obra completa. No se almacenan: la miniatura y el vídeo se piden a YouTube.</p>
      <div className="filtros">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar ficha…" aria-label="Buscar" />
        <label>Tipo <select value={tipo} onChange={(e) => setTipo(e.target.value)}><option value="">Todos</option>{Object.entries(TIPOS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>
      {rows.map((g) => (
        <section key={g.id} className="videos">
          <h2><Link to={`/${g.route}/${g.id}`}>{g.label}</Link> <small>({g.videos.length})</small></h2>
          <p className="atribucion">Enlazados desde <a href={g.article_url} target="_blank" rel="noreferrer">el artículo de Wikipedia</a></p>
          <div className="rejilla-videos">{g.videos.map((v) => <VideoCard key={v.id} v={v} />)}</div>
        </section>
      ))}
      {rows.length === 0 && <p>No hay fichas que coincidan.</p>}
    </section>
  );
}
