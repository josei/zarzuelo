import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import MiniSearch from "minisearch";
import { api } from "../api/client";

interface Doc { id: string; label: string; type: string; alt: string }
const ROUTE: Record<string, string> = { work: "obra", person: "persona", venue: "teatro" };
const strip = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

export default function Search() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  useEffect(() => { api<Doc[]>("search/index.json").then(setDocs).catch(() => {}); }, []);
  const ms = useMemo(() => {
    const m = new MiniSearch<Doc>({ fields: ["label", "alt"], storeFields: ["label", "type"], processTerm: strip, searchOptions: { prefix: true, fuzzy: 0.15 } });
    m.addAll(docs);
    return m;
  }, [docs]);
  const hits = q.length > 1 ? ms.search(q).slice(0, 8) : [];
  return (
    <div className="search">
      <input type="search" placeholder="Buscar obra, persona, teatro…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar" />
      {hits.length > 0 && (
        <ul>
          {hits.map((h) => (
            <li key={h.id}><button onClick={() => { nav(`/${ROUTE[h.type] ?? "obra"}/${h.id}`); setQ(""); }}>{h.label} <small>{h.type}</small></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}
