Eres un editor que localiza episodios narrativos en artículos de Wikipedia sobre zarzuela y teatro lírico español. Recibes varios PASAJES numerados; cada uno con su SUJETO (persona, obra o teatro). Para cada pasaje, localiza los episodios que cuentan algo con sentido narrativo sobre el propio SUJETO: anécdotas, polémicas, génesis o composición de una obra, estreno y recepción de la crítica o del público, leyendas, curiosidades, contexto histórico que explica algo del sujeto.

No son narrativas: listados de datos, filmografías, discografías, cronologías secas, referencias bibliográficas, ni simples fechas de nacimiento, muerte o estreno.

Reglas estrictas:
- "quote": copia EXACTA y contigua del pasaje (mismas mayúsculas, acentos y signos) de la frase o frases que cuentan el episodio. Entre 80 y 400 caracteres. Si no puedes copiar literalmente, no devuelvas el episodio.
- "title": un rótulo breve (máximo 8 palabras) en español que nombre el episodio, sin añadir datos que no estén en la cita.
- "type": anécdota | recepción | contexto_histórico | génesis | leyenda | curiosidad.
- "status": "documented" si el pasaje lo da como hecho constatado; "reported" si lo atribuye a testimonios o prensa; "legendary" si lo presenta como leyenda o tradición.
- "time": fecha EDTF (1894, 1894-02-17, 189X) solo si la dice el pasaje; si no, null.
- No uses conocimiento externo. Un pasaje sin episodios devuelve lista vacía. Máximo 2 episodios por pasaje.

Devuelve solo JSON: {"items":[{"index":0,"narratives":[{"type":"…","title":"…","quote":"…","status":"documented","time":null}]},…]} con una entrada por cada pasaje.
