// "El apuntador": componente de cita reutilizable (§8.5). Muestra fragmento (si es reutilizable), fuente, revisión, fecha y estado.
import { createContext, ReactNode, useContext, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, Evidence, SourceDoc } from "../api/client";

interface Ctx { open: (entityId: string, evidenceIds: string[], status?: string, hedge?: string | null, families?: string[]) => void }
const C = createContext<Ctx>({ open: () => {} });

const STATUS_LABEL: Record<string, string> = {
  asserted: "documentado", disputed: "en disputa", attributed: "atribuido", legendary: "leyenda", deprecated: "retirado",
  documented: "documentado", reported: "según testimonios",
};

const FAMILY: Record<string, string> = { wikimedia: "Wikimedia (Wikidata/Wikipedia)", musicbrainz: "MusicBrainz", bne: "BNE", europeana: "Europeana" };

function Panel({ entityId, ids, status, hedge, families, close }: { entityId: string; ids: string[]; status?: string; hedge?: string | null; families?: string[]; close: () => void }) {
  const { data: ev } = useQuery({ queryKey: ["evidence", entityId], queryFn: () => api<Evidence[]>(`evidence/${entityId}.json`) });
  const items = (ev ?? []).filter((e) => ids.includes(e.id));
  const docs = useQueries({ queries: [...new Set(items.map((i) => i.document_id))].map((d) => ({ queryKey: ["source", d], queryFn: () => api<SourceDoc>(`sources/${d}.json`) })) });
  const byDoc = Object.fromEntries(docs.flatMap((q) => (q.data ? [[q.data.id, q.data]] : [])));
  return (
    <aside className="apuntador" role="dialog" aria-label="Fuente del dato">
      <button className="close" onClick={close} aria-label="Cerrar">×</button>
      {status && <p className={`estado estado-${status}`}>{STATUS_LABEL[status] ?? status}{hedge ? ` · «${hedge}»` : ""}</p>}
      {families && families.length > 0 && (
        <p className="independientes">
          {families.length === 1 ? "Respaldado por 1 fuente independiente" : `Respaldado por ${families.length} fuentes independientes`}: {families.map((f) => FAMILY[f] ?? f).join(", ")}.
          {families.length === 1 && families[0] === "wikimedia" && " (Wikidata y Wikipedia se copian entre sí: cuentan como una.)"}
        </p>
      )}
      {items.length === 0 && <p>Cargando…</p>}
      {items.map((e) => {
        const d = byDoc[e.document_id];
        return (
          <figure key={e.id}>
            {e.text ? <blockquote>{e.text}</blockquote> : <p className="solo-consulta">Fuente de solo consulta: se da la referencia, no el fragmento.</p>}
            {d && (
              <figcaption>
                <a href={d.url} target="_blank" rel="noreferrer">{d.source} · revisión {d.stable_id}</a><br />
                consultado {d.retrieved_at} · licencia {d.license}
              </figcaption>
            )}
          </figure>
        );
      })}
    </aside>
  );
}

function Provider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ entityId: string; ids: string[]; status?: string; hedge?: string | null; families?: string[] } | null>(null);
  return (
    <C.Provider value={{ open: (entityId, ids, status, hedge, families) => setState({ entityId, ids, status, hedge, families }) }}>
      {children}
      {state && <Panel {...state} close={() => setState(null)} />}
    </C.Provider>
  );
}

/** Concha del apuntador junto a cada dato. */
function Shell({ entityId, ids, status, hedge, families }: { entityId: string; ids: string[]; status?: string; hedge?: string | null; families?: string[] }) {
  const { open } = useContext(C);
  return (
    <button className="concha" aria-label="Ver fuente" title="Ver fuente" onClick={() => open(entityId, ids, status, hedge, families)}>◖</button>
  );
}

export const Apuntador = { Provider, Shell };
