import { Link, NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Manifest } from "./api/client";
import Home from "./pages/Home";
import Entity from "./pages/Entity";
import Genre from "./pages/Genre";
import Genres from "./pages/Genres";
import Obras from "./pages/Obras";
import Documents from "./pages/Documents";
import Videos from "./pages/Videos";
import Timeline from "./pages/Timeline";
import Network from "./pages/Network";
import MapView from "./pages/MapView";
import Quiz from "./pages/Quiz";
import Search from "./components/Search";
import { Apuntador } from "./components/Apuntador";

function RedirectToWork() { const { id } = useParams(); return <Navigate to={`/obra/${id}`} replace />; }   // los monográficos son ahora el texto de cada ficha

export default function App() {
  const { data: m } = useQuery({ queryKey: ["manifest"], queryFn: () => api<Manifest>("manifest.json") });
  return (
    <Apuntador.Provider>
      <header className="top">
        <Link to="/" className="brand">Zarzuelo <small>entre bastidores</small></Link>
        <nav>
          <NavLink to="/cronologia">Cronología</NavLink>
          <NavLink to="/red">La compañía</NavLink>
          <NavLink to="/obras">Títulos</NavLink>
          <NavLink to="/mapa">El plano</NavLink>
          <NavLink to="/generos">Géneros</NavLink>
          <NavLink to="/documentos">Documentos</NavLink>
          <NavLink to="/videos">Vídeos</NavLink>
          <NavLink to="/cuestionarios/general">Ensayo general</NavLink>
        </nav>
        <Search />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/obra/:id" element={<Entity kind="works" />} />
          <Route path="/persona/:id" element={<Entity kind="people" />} />
          <Route path="/teatro/:id" element={<Entity kind="venues" />} />
          <Route path="/obras" element={<Obras />} />
          <Route path="/documentos" element={<Documents />} />
          <Route path="/videos" element={<Videos />} />
          <Route path="/generos" element={<Genres />} />
          <Route path="/genero/:id" element={<Genre />} />
          <Route path="/cronologia" element={<Timeline />} />
          <Route path="/red" element={<Network />} />
          <Route path="/mapa" element={<MapView />} />
          <Route path="/cuestionarios/:id" element={<Quiz />} />
          <Route path="/monograficos/:id" element={<RedirectToWork />} />
        </Routes>
      </main>
      <footer>
        {m ? <>Base v{m.kb_version} · generada {m.generated_at} · {m.counts.claims} afirmaciones, {m.counts.passages} pasajes. </> : null}
        Cada dato remite a una fuente archivada; los textos derivados de Wikipedia se publican bajo CC BY-SA.
      </footer>
    </Apuntador.Provider>
  );
}
