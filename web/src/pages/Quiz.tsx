import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Apuntador } from "../components/Apuntador";

interface Q { id: string; prompt: string; options: string[]; answer_index: number; claim_ids: string[]; entity_id: string | null; evidence_ids: string[] }
interface QuizData { id: string; title: string; questions: Q[] }

export default function Quiz() {
  const { id = "general" } = useParams();
  const { data } = useQuery({ queryKey: ["quiz", id], queryFn: () => api<QuizData>(`quizzes/${id}.json`) });
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [score, setScore] = useState(0);
  if (!data) return <p>Cargando…</p>;
  const q = data.questions[i];
  if (!q) return <section><h1>{data.title}</h1><p>Puntuación: {score} / {data.questions.length}</p><button onClick={() => { setI(0); setScore(0); setPicked(null); }}>Otra vez</button></section>;
  return (
    <section>
      <h1>{data.title} <small>{i + 1}/{data.questions.length}</small></h1>
      <p className="pregunta">{q.prompt}</p>
      <ul className="opciones">
        {q.options.map((o, k) => (
          <li key={k}><button disabled={picked !== null} className={picked !== null ? (k === q.answer_index ? "ok" : k === picked ? "ko" : "") : ""}
            onClick={() => { setPicked(k); if (k === q.answer_index) setScore(score + 1); }}>{o}</button></li>
        ))}
      </ul>
      {picked !== null && (
        <p>{picked === q.answer_index ? "¡Correcto!" : "No era esa."} Fuente: {q.entity_id && <Apuntador.Shell entityId={q.entity_id} ids={q.evidence_ids} />}
          <button onClick={() => { setI(i + 1); setPicked(null); }}>Siguiente</button></p>
      )}
    </section>
  );
}
