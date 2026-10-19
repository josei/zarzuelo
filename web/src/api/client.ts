import { QueryClient } from "@tanstack/react-query";

const BASE = `${import.meta.env.BASE_URL}api/v1`;

// mismo criterio que kb.export.fname: nombres de fichero solo ASCII (S3, GitHub Pages y Windows no toleran bien «:» ni tildes)
export const fname = (id: string) => id.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "");

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}/${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// Los JSON son inmutables dentro de cada versión de la base: caché indefinida.
export const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: 1 } } });

export interface Manifest { schema_version: string; kb_version: string; generated_at: string; counts: Record<string, number> }
export interface Evidence { id: string; document_id: string; char_start: number; char_end: number; reuse: "reusable" | "consult_only"; text: string | null }
export interface SourceDoc { id: string; source: string; url: string; stable_id: string; retrieved_at: string; license: string; reuse: string }
export interface ClaimOut {
  id: string; property: string; value: string; value_label: string | null; value_link: string | null; value_type: string; status: string; hedge: string | null;
  confidence: number; source_kind: string; alternative: boolean; source_families: string[]; wikidata_rank: string | null; references: string[]; n_claims: number; evidence_ids: string[]; relations: { claim_a: string; claim_b: string; relation: string }[];
}
export interface EntityOut { id: string; type: string; label: string; labels_alt: string[]; external_ids: Record<string, string>; claims: ClaimOut[]; narratives: NarrativeOut[]; biography: Biography | null; summary: Summary | null; scores: ScoreSet[]; videos: VideoOut[]; authored: { id: string; label: string; year: number | null; roles: string[] }[]; documents: DocItem[]; documents_total: number; rism: { label: string; url: string }[]; rism_total: number; rism_url: string | null; }
export interface NarrativeOut { id: string; type: string; title: string; summary: string; time: string | null; status: string; evidence_ids: string[] }
export interface IndexItem { id: string; label: string; year?: string | null; composer?: (string | null)[] }

export interface BioSegment { t: string; to?: string; year?: boolean }
export interface Biography { paragraphs: { passage_id: string; segments: BioSegment[] }[]; document_id: string; license: string; revision: string; url: string; history_url: string; retrieved_at: string }
export interface Summary { segments: { text: string; parts: BioSegment[]; evidence_ids: string[] }[]; model: string | null; generated_at: string; verified: boolean }
export interface ScoreFile { name: string; desc: string; edition: string; copyright: string; publisher: string; editor: string; image_type: string; url: string }
export interface ScoreSet { source: string; page_url: string; license: string; files: ScoreFile[]; retrieved_at: string }
export interface DocItem { id: string; source: string; source_label: string; title: string; creators: string[]; date: string | null; type: string | null; subjects: string[]; institution: string | null; rights: string; url: string; record_url: string; entities: { id: string; label: string; route: string }[] }

export interface VideoOut { id: string; article_url: string; title: string | null; channel: string | null; embeddable: boolean }
