// El plano como máquina del tiempo: un deslizador de año cambia el plano histórico de Madrid (IGN, WMS, CC BY 4.0) y
// los teatros y estrenos visibles (los que existían y se habían estrenado hasta ese año).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CircleMarker, MapContainer, Popup, TileLayer, WMSTileLayer, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../api/client";

interface Premiere { id: string; label: string; year: number | null }
interface Venue { id: string; label: string; lat: number; lon: number; city: string | null; opening: string | null; closing: string | null; premieres: Premiere[] }

// Planos históricos de Madrid del IGN (https://www.ign.es/wms/planos, georreferenciados, CC BY 4.0): se cargan desde el IGN, no se almacenan.
const PLANOS = [
  { year: 1622, layer: "mancelliMadrid", label: "Mancelli, 1622" }, { year: 1656, layer: "texeira", label: "Texeira, 1656" },
  { year: 1761, layer: "chalmadrierMadrid", label: "Chalmandrier, 1761" }, { year: 1769, layer: "espinosaMadrid", label: "Espinosa de los Monteros, 1769" },
  { year: 1785, layer: "geometricoMadrid", label: "Tomás López, 1785" }, { year: 1848, layer: "madozMadrid", label: "Madoz y Coello, 1848" },
  { year: 1875, layer: "ibanezMadrid", label: "Ibáñez Íbero (parcelario), 1875" }, { year: 1900, layer: "facundoMadrid", label: "Facundo Cañada, 1900" },
  { year: 1910, layer: "nunezMadrid", label: "Núñez Granés, 1910" }, { year: 1929, layer: "ayuntamientoMadrid", label: "Plano del Ayuntamiento, 1929" },
  { year: 1940, layer: "parcelarioMadrid", label: "Plano parcelario, 1940–1960" },
];
const MADRID: LatLngBoundsExpression = [[40.405, -3.725], [40.43, -3.68]];
const Y_MIN = 1620, Y_MAX = 1960;
const yr = (s: string | null) => (s ? Number(s.slice(0, 4)) || null : null);

function Fit({ bounds }: { bounds: LatLngBoundsExpression }) {
  const map = useMap();
  useEffect(() => { map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 }); }, [map, bounds]);
  return null;
}

export default function MapView() {
  const { data = [] } = useQuery({ queryKey: ["map"], queryFn: () => api<Venue[]>("views/map.json") });
  const [city, setCity] = useState("Madrid");
  const [year, setYear] = useState(1894);
  const [playing, setPlaying] = useState(false);
  const [overlay, setOverlay] = useState(true);
  const [opacity, setOpacity] = useState(1);   // planos antiguos opacos por defecto
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => setYear((y) => { if (y >= Y_MAX) { setPlaying(false); return Y_MAX; } return y + 2; }), 220);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [playing]);

  const cities = useMemo(() => {
    const m = new Map<string, number>();
    data.forEach((v) => m.set(v.city ?? "Otros lugares", (m.get(v.city ?? "Otros lugares") ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  // teatros visibles en `year`: ya existían (apertura o algún estreno ≤ año) y no habían cerrado
  const view = useMemo(() => data
    .filter((v) => city === "Todos" || (v.city ?? "Otros lugares") === city)
    .map((v) => {
      const upto = v.premieres.filter((p) => p.year !== null && p.year <= year);
      const open = yr(v.opening);
      const lastPremiere = Math.max(0, ...v.premieres.map((p) => p.year ?? 0));
      // el cierre de Wikidata se ignora si hay estrenos posteriores: es evidencia de actividad y el cierre suele referirse a otro edificio o a una reforma
      const closeRaw = yr(v.closing), close = closeRaw !== null && lastPremiere > closeRaw ? null : closeRaw;
      const existed = (open !== null && open <= year) || upto.length > 0;
      const closed = close !== null && close < year;
      return { v, upto, recent: upto.filter((p) => (p.year as number) > year - 5).length, visible: existed, closed, closedSince: closed ? close : null, closeContradicted: closeRaw !== null && close === null };
    })
    .filter((x) => x.visible), [data, city, year]);

  const plano = [...PLANOS].reverse().find((p) => p.year <= year) ?? null;      // el plano más reciente hasta ese año
  const isMadrid = city === "Madrid";
  const bounds: LatLngBoundsExpression = useMemo(() => {
    const pool = data.filter((v) => city === "Todos" || (v.city ?? "Otros lugares") === city);
    if (city === "Madrid" || pool.length === 0) return MADRID;
    const la = pool.map((v) => v.lat), lo = pool.map((v) => v.lon);
    return [[Math.min(...la) - 0.01, Math.min(...lo) - 0.01], [Math.max(...la) + 0.01, Math.max(...lo) + 0.01]];
  }, [city, data]);
  const total = view.reduce((n, x) => n + x.upto.length, 0);

  return (
    <section>
      <h1>El plano <small>máquina del tiempo</small></h1>
      <div className="tiempo">
        <div className="tiempo-anio" aria-live="polite">{year}</div>
        <button className="play" onClick={() => { if (year >= Y_MAX) setYear(Y_MIN); setPlaying(!playing); }} aria-label={playing ? "Pausar" : "Reproducir"}>{playing ? "❚❚" : "▶"}</button>
        <input type="range" min={Y_MIN} max={Y_MAX} step={1} value={year} onChange={(e) => { setPlaying(false); setYear(Number(e.target.value)); }} aria-label="Año" />
      </div>
      <p className="tiempo-resumen">
        <strong>{view.filter((x) => !x.closed).length}</strong> {view.filter((x) => !x.closed).length === 1 ? "teatro abierto" : "teatros abiertos"}
        {view.some((x) => x.closed) && <> y <strong>{view.filter((x) => x.closed).length}</strong> ya cerrados</>}, con <strong>{total}</strong> estrenos hasta {year}
        {isMadrid && overlay && (plano ? <> · Plano: <strong>{plano.label}</strong></> : <> · Aún no hay plano de Madrid anterior a 1622: se ve el mapa actual</>)}
      </p>
      <div className="filtros">
        <label>Ciudad{" "}
          <select value={city} onChange={(e) => setCity(e.target.value)}>
            <option value="Todos">Todas</option>
            {cities.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
          </select>
        </label>
        {isMadrid && (
          <>
            <label><input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} /> Plano histórico</label>
            {overlay && <label>Opacidad <input type="range" min={0.2} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} /></label>}
          </>
        )}
      </div>
      <p className="leyenda-mapa"><i className="pt rojo" /> abierto <i className="pt oro" /> con estrenos en los últimos 5 años <i className="pt gris" /> cerrado ya (según Wikidata)</p>
      <div className="mapa" role="region" aria-label="Mapa de teatros">
        <MapContainer center={[40.4168, -3.7038]} zoom={15} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <Fit bounds={bounds} />
          <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
          {isMadrid && overlay && plano && (
            <WMSTileLayer key={plano.layer} url="https://www.ign.es/wms/planos" layers={plano.layer} format="image/png" transparent opacity={opacity}
              attribution='Planos históricos &copy; <a href="https://www.ign.es">Instituto Geográfico Nacional</a> (CC BY 4.0)' />
          )}
          {view.map(({ v, upto, recent, closeContradicted, closed, closedSince }) => (
            <CircleMarker key={v.id} center={[v.lat, v.lon]} radius={6 + Math.sqrt(upto.length) * 2.4}
              pathOptions={closed
                ? { color: "#555", fillColor: "#8a8a8a", fillOpacity: 0.55, weight: 2, dashArray: "4 3" }          // cerrado: gris y trazo discontinuo
                : { color: recent > 0 ? "#F2C14E" : "#C4213B", fillColor: "#C4213B", fillOpacity: 0.55, weight: recent > 0 ? 4 : 2 }}>
              <Popup>
                <strong><Link to={`/teatro/${v.id}`}>{v.label}</Link></strong>{closed && <span className="cerrado"> · cerrado</span>}<br />
                {closed && <small className="cerrado">Cerrado desde {closedSince} (según Wikidata).<br /></small>}
                {v.city && <span>{v.city}<br /></span>}
                {(v.opening || v.closing) && <span>{v.opening?.slice(0, 4) ?? "?"} – {v.closing?.slice(0, 4) ?? "hoy"}<br /></span>}
                {closeContradicted && <small>Wikidata da un cierre en {v.closing?.slice(0, 4)}, pero hay estrenos posteriores: se ignora.<br /></small>}
                {upto.length > 0 ? (
                  <>
                    <em>{upto.length} estrenos hasta {year}{recent > 0 ? ` (${recent} en los últimos 5 años)` : ""}</em>
                    <ul className="popup-estrenos">
                      {[...upto].reverse().slice(0, 10).map((p) => <li key={p.id}>{p.year} · <Link to={`/obra/${p.id}`}>{p.label}</Link></li>)}
                    </ul>
                    {upto.length > 10 && <Link to={`/teatro/${v.id}`}>y {upto.length - 10} más…</Link>}
                  </>
                ) : <em>Sin estrenos registrados hasta {year}</em>}
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
      <p className="nota-mapa">
        Se ven los teatros que ya existían en el año elegido (por su apertura o su primer estreno registrado) y que no habían cerrado. Al pasar su fecha de cierre de Wikidata, el teatro se muestra en gris y marcado como cerrado, salvo que tenga estrenos posteriores (entonces ese cierre se ignora).
        Coordenadas de Wikidata: muchos teatros han desaparecido y la posición puede ser la del solar actual. Los planos históricos de Madrid los sirve el IGN. Fuera de Madrid solo hay mapa actual.
      </p>
      <details><summary>Ver como tabla (hasta {year})</summary>
        <table><thead><tr><th>Teatro</th><th>Ciudad</th><th>Estrenos</th><th>Estado</th></tr></thead>
          <tbody>{view.map(({ v, upto, closed, closedSince }) => <tr key={v.id}><td><Link to={`/teatro/${v.id}`}>{v.label}</Link></td><td>{v.city ?? "—"}</td><td>{upto.length}</td><td>{closed ? `cerrado desde ${closedSince}` : "abierto"}</td></tr>)}</tbody></table>
      </details>
    </section>
  );
}
