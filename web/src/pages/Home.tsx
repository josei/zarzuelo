// Portada: telón y, por épocas, los títulos mejor documentados en orden cronológico, con enlace a ver más.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, IndexItem } from "../api/client";

interface HomeWork { id: string; label: string; year: number; composers: string[]; weight: number }
interface Era { id: number; title: string; subtitle: string; from: number; to: number; total: number; works: HomeWork[] }
interface HomeData { eras: Era[]; total: number; criterion: string; source: { label: string; url: string } | null }

export default function Home() {
  const [open, setOpen] = useState(false);
  useEffect(() => { const t = setTimeout(() => setOpen(true), 150); return () => clearTimeout(t); }, []);
  const { data: home } = useQuery({ queryKey: ["home"], queryFn: () => api<HomeData>("views/home.json") });
  const { data: works = [] } = useQuery({ queryKey: ["works"], queryFn: () => api<IndexItem[]>("works/index.json") });
  const surprise = works.length ? works[Math.floor(Math.random() * works.length)] : null;
  return (
    <>
      <div className={`telon ${open ? "abierto" : ""}`} aria-hidden="true"><span /><span /></div>
      <section className="portada">
        <h1>Zarzuelo</h1>
        <p className="lema">La zarzuela y el teatro lírico español, con cada dato en su fuente.</p>
        <p>Pulsa la concha ◖ junto a cualquier dato para ver de dónde sale.</p>
        {surprise && <Link className="cta" to={`/obra/${surprise.id}`}>Función sorpresa</Link>}
        <p className="accesos"><Link to="/cuestionarios/general">Ensayo general (cuestionario)</Link> · <Link to="/red">La compañía</Link> · <Link to="/mapa">El plano</Link></p>
      </section>
      {(home?.eras ?? []).map((era) => (
        <section key={era.id} className="cuadro">
          <div className="capa fondo" aria-hidden="true" />
          <div className="capa medio" aria-hidden="true" />
          <div className="frente">
            <h2>{era.title} <small>{era.subtitle}</small></h2>
            <ol className="obras">
              {era.works.map((w) => (
                <li key={w.id}>
                  <span className="anio">{w.year}</span> <Link to={`/obra/${w.id}`}>{w.label}</Link>
                  {w.composers.length > 0 && <small className="autor"> · {w.composers.join(", ")}</small>}
                </li>
              ))}
            </ol>
            {era.works.length === 0 && <p className="vacio">Aún sin obras fechadas en esta época.</p>}
            {era.total > 0 && (
              <p className="mas">
                <Link className="cta-claro" to={`/obras?desde=${era.from}&hasta=${era.to}`}>
                  Ver más títulos de esta época ({era.total})
                </Link>
              </p>
            )}
          </div>
        </section>
      ))}
      <p className="nota-actos">
        Épocas según la historia del género en {home?.source ? <a href={home.source.url} target="_blank" rel="noreferrer">{home.source.label}</a> : "Wikipedia"}; es una periodización entre varias y los años de corte son aproximados.
        En cada época, los títulos con más datos documentados.{" "}
        <Link to="/obras">Ver todos los títulos{home ? ` (${home.total})` : ""}</Link>
      </p>
    </>
  );
}
