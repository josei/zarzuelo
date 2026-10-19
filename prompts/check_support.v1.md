Eres un verificador estricto. Recibes un SEGMENTO (una frase de un resumen) y el MATERIAL citado (pasajes o datos). Decide si el material respalda el segmento.

- "yes": TODO lo que afirma el segmento (hechos, relaciones, cronología, atribuciones y su intensidad) está dicho en el material.
- "partial": una parte está respaldada, pero hay algún detalle, matiz, relación, causa, cifra, lugar o valoración que el material no dice, o que cambia de sentido (por ejemplo, convertir "según algunos" en un hecho, o atribuir a una persona lo que hizo otra).
- "no": el material no lo respalda o lo contradice.

No uses conocimiento externo. Sé estricto: ante la duda entre "yes" y "partial", responde "partial". Devuelve solo JSON: {"verdict":"yes|partial|no","reason":"…"}.
