# Base de conocimiento de zarzuela y música escénica española — Plan

> Versión 1.4 · 2026-10-06 — plan cerrado; construcción en local con refresco a demanda; LLMs: Qwen local + Claude Code CLI; diseño "Entre bastidores"
> Objetivo: una base de conocimiento trazable de la que salgan visualizaciones, cronogramas, cuestionarios y monográficos en los que **cada dato remita a una fuente consultada y archivada**.

---

## 1. Principios

1. **Nada sin fuente.** Ningún dato entra en la base sin un puntero a un documento archivado. Ningún texto derivado se publica sin citas verificables.
2. **El pasaje es la unidad de verdad.** La evidencia última es siempre un fragmento literal de una fuente, con su posición exacta en el documento. Todo lo demás (afirmaciones, anécdotas, clasificaciones) es una *interpretación* que apunta a uno o varios pasajes.
3. **Instantáneas inmutables.** Se archiva lo que se leyó y cuándo se leyó. Para Wikipedia, eso significa la revisión concreta (`oldid`), no "el artículo".
4. **Ambigüedad explícita.** Los conflictos, las atribuciones dudosas y las fechas imprecisas se modelan como tales. No se resuelven en silencio.
5. **El LLM nunca es fuente.** El modelo de lenguaje solo extrae información de pasajes que se le entregan y redacta a partir de material recuperado. Todo lo que produce pasa por un validador.
6. **Automatización total.** Sin revisión humana obligatoria en el bucle. La calidad se controla con validadores, umbrales y marcas de estado.
7. **Licencia de cada pasaje.** Cada pasaje guarda la licencia de su fuente, y eso determina qué se puede hacer con él:
   - **Reutilizable** (dominio público, CC0, CC BY, CC BY-SA): se puede mostrar el fragmento en el sitio.
   - **Solo consulta** (acceso libre sin licencia de reutilización): sirve como evidencia interna. Al público se le da la cita, el enlace y, como mucho, un fragmento muy breve.

---

## 1 bis. Alcance

**Ámbito:** todo el teatro musical y los géneros líricos y escénicos españoles, desde la zarzuela barroca hasta mediados del siglo XX. El corte provisional es **1650–1960**, configurable.

Hay dos círculos:

- **Núcleo**:
  - zarzuela barroca;
  - tonadilla escénica;
  - ópera española;
  - zarzuela grande;
  - género chico y sus formas (sainete lírico, juguete cómico-lírico, revista de actualidad, pasatiempo…);
  - zarzuela del siglo XX;
  - opereta española;
  - género ínfimo;
  - revista y variedades;
  - sarsuela catalana y teatro lírico en otras lenguas peninsulares;
  - zarzuela producida en América cuando hay vínculo español (autores o compañías).
- **Periferia**, solo cuando enlaza con el núcleo:
  - cuplé y canción española;
  - danza y bailes teatrales;
  - música incidental;
  - intérpretes, compañías, teatros, empresarios y escenógrafos.

Los propios géneros son conceptos del vocabulario (§2.3). Así, ampliar o recortar el alcance es cuestión de configuración, no de rediseño.

---

## 2. ¿Basta con tripletas RDF? No: modelo en capas

Las tripletas (sujeto–propiedad–valor) representan bien el **esqueleto**: obra → compositor, obra → fecha de estreno, obra → teatro. Representan mal cuatro cosas:

- **Narrativa y anécdotas.** Por ejemplo, el ambiente de un estreno, una polémica en prensa o una leyenda sobre la composición de un número.
- **Ambigüedad y matices.** Expresiones como "se atribuye a", "según algunos autores", "hacia 1890".
- **Vocabularios con sinónimos.** La forma en que se clasifica una obra depende de quién la clasifique: el cartel original, un musicólogo o Wikipedia.
- **Conflicto entre fuentes.** Dos fuentes pueden dar fechas distintas para el mismo estreno.

Por eso el modelo tiene **seis capas**, todas enlazadas:

| Capa | Qué guarda | Ejemplo |
|---|---|---|
| **D. Documentos** | Instantánea archivada de cada fuente: URL, identificador estable, fecha de captura, hash, licencia | Revisión `oldid=…` de "La verbena de la Paloma" en es.wikipedia |
| **P. Pasajes** | Fragmentos literales del documento, con posiciones de inicio y fin | Párrafo sobre el estreno en el Teatro Apolo |
| **E. Entidades** | Obras, personas, teatros, compañías, números musicales, grabaciones, lugares; con IDs externos | Obra ↔ ID de Wikidata, ↔ autoridad BNE, ↔ work de MusicBrainz |
| **A. Afirmaciones** | Hechos estructurados con calificadores y evidencia (≈ statements de Wikidata, no tripletas desnudas) | estreno = 1894-02-17 · lugar = Teatro Apolo · evidencia = [P123, P456] |
| **N. Narrativas** | Anécdotas, episodios, recepción crítica, contexto: unidades de texto con entidades y fechas, apoyadas en pasajes | "Recepción de la prensa en el estreno" → pasajes de crónicas de 1894 |
| **V. Vocabulario** | Conceptos (géneros, subgéneros, tipos de voz, formas) con sinónimos y relaciones, en el estilo de SKOS | `género chico` ⟶ prefLabel, altLabels, broader `zarzuela`, related `sainete lírico` |

Las capas E, A y V se pueden exportar a RDF/JSON-LD cuando haga falta interoperar. Las capas D, P y N viven como texto con metadatos, y el RDF solo guarda los enlaces hacia ellas. El almacén principal **no** es un triplestore, sino una base relacional con búsqueda de texto y vectorial (ver §8).

### 2.1 Afirmaciones: más que tripletas

```
claim:
  id, subject_entity, property, value (entidad | literal | fecha EDTF | cantidad)
  qualifiers: {lugar, compañía, intérprete, papel, …}
  evidence: [passage_id, …]          # obligatorio, ≥1
  source_kind: structured | extracted | derived
  status: asserted | disputed | attributed | legendary | deprecated
  hedge: null | "se atribuye" | "según la tradición" | "probablemente" …   # matiz literal de la fuente
  confidence: 0–1 (calculada, no inventada: concordancia de fuentes + tipo de fuente + calidad de extracción)
  extracted_by: {método, modelo, versión, fecha}
```

- **Fechas** en formato EDTF (`1894-02-17`, `1890~` para "hacia 1890", `189X`, `[1893..1895]`), para que la imprecisión se pueda dibujar en los cronogramas.
- **Conflictos**: si hay dos valores distintos para la misma propiedad, se guardan las dos afirmaciones, se enlazan con una relación `conflicts_with` y ambas pasan a estado `disputed`. Las salidas lo muestran ("1894 según X; 1895 según Y").
- **Atribuciones dudosas** (autoría compartida, arreglos, números interpolados): `status=attributed` más el `hedge` literal de la fuente.

### 2.2 Narrativas y anécdotas

```
narrative:
  id, type: anécdota | recepción | contexto_histórico | génesis | leyenda | curiosidad
  title, summary (generado, con cita a pasajes)
  entities: [obra, personas, teatro…], time: EDTF, place
  evidence: [passage_id, …]
  status: documented | reported | legendary   # "se cuenta que…" ≠ "consta que…"
```

El resumen de una narrativa es texto derivado y, como tal, pasa por el validador (§6). Las anécdotas que la fuente presenta como leyenda conservan esa marca hasta el producto final.

### 2.3 Clasificaciones y sinónimos

Hay tres niveles distintos de clasificación, y todos se conservan:

1. **Denominación original**, la del cartel o el libreto: "zarzuela en un acto y tres cuadros", "sainete lírico", "pasatiempo cómico-lírico".
2. **Clasificación de una fuente**: lo que dice Wikidata, Wikipedia, la BNE o un musicólogo.
3. **Concepto normalizado** del vocabulario propio (capa V).

En el vocabulario, cada concepto tiene `prefLabel`, `altLabel` (sinónimos y variantes ortográficas), `hiddenLabel` (erratas frecuentes), `broader`/`narrower`, `related` y una `scopeNote` con su propia fuente. Las correspondencias entre etiquetas y conceptos son afirmaciones más, con su evidencia (`closeMatch` frente a `exactMatch`). Así, "género chico", "zarzuela chica" y "obra en un acto por horas" pueden convivir con matices y sin colapsarse en un único término.

---

## 3. Fuentes

Cada fuente lleva un **estado de verificación**:

- ✅ Acceso automatizado confirmado en la documentación de la propia fuente.
- 🔶 Acceso probable por la plataforma que usa (DSpace, Goobi, MediaWiki…), pendiente de confirmar en la fase 0.
- 🔍 Solo consulta: sin vía de descarga clara o sin licencia de reutilización.

Y un **rol**:

- **E**: esqueleto de entidades.
- **H**: hechos.
- **N**: narrativa.
- **P**: fuente primaria.
- **M**: medios (partituras, audio, imagen).
- **R**: reconciliación de identificadores.

### 3.1 Datos enlazados y catálogos de autoridad

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **Wikidata** | ✅ SPARQL, API REST, dumps · CC0 | E, R | Semilla del corpus y puente hacia casi todos los identificadores de abajo |
| **BNE – catálogo de autoridades** (personas, entidades) | ✅ dumps en datosabiertos.bne.es y datos.gob.es (JSON, CSV, XML) | E, R | Normaliza nombres, seudónimos y fechas vitales |
| **BNE – Catálogo bibliográfico. Partituras** | ✅ dataset abierto | H, M | Ediciones impresas y manuscritas de partituras |
| **BNE – Catálogo bibliográfico. Grabaciones sonoras** | ✅ dataset abierto | H, M | Discografía histórica en la BNE |
| **BNE – Biblioteca Digital. Documentos en dominio público** | ✅ ZIP en CSV, JSON y XML · **CC BY 4.0** | M, P | Índice de lo digitalizado: libros, partituras, registros sonoros, material gráfico. Permite localizar libretos, partituras y carteles digitalizados |
| **VIAF** | ✅ API | R | Desambiguación de personas entre catálogos nacionales |
| **RISM Online** | ✅ API pública documentada | H, M | Fuentes musicales (manuscritos, impresos) con localización en archivos; muy útil para tonadilla y siglo XVIII |
| **MusicBrainz** | ✅ API REST (sin clave, User-Agent obligatorio, 1 petición/s) y dumps · núcleo CC0 | H, M | Obras, números musicales, grabaciones e intérpretes |
| **Discogs** | ✅ API con token personal gratuito | H, M | Ediciones discográficas: complementa a MusicBrainz en LP e históricos |
| **DAHR** (Discography of American Historical Recordings, UCSB) | 🔶 web estructurada; verificar si ofrece exportación | H, M | Muy rica en grabaciones de zarzuela en 78 rpm de sellos como Columbia y Victor, con matrices, fechas e intérpretes |

### 3.2 Enciclopedias y obras de referencia

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **Wikipedia** ES, CA, EU, GL, EN | ✅ API de MediaWiki, fijada por `oldid` · CC BY-SA | H, N | Fichas, argumentos, contexto y anécdotas. Existió un proyecto específico para mejorar la cobertura de zarzuela en Wikipedia, así que la cobertura en ES y EN es razonable |
| **Diccionario de la Zarzuela (ICCMU), edición digital** | 🔍 acceso abierto (lanzado en enero de 2026; >3.200 entradas, ~2.700 personas, ~300 títulos, ~100 términos) · sin licencia de reutilización explícita | H, N, vocabulario | **La referencia académica del género.** Se usa como evidencia de solo consulta y como fuente del vocabulario de géneros. Antes de rastrearlo, pedir permiso o leer sus términos; si no se puede rastrear, se queda como fuente de verificación puntual |
| **Teatro Musical Español (Fundación Juan March)** | 🔍 acceso libre; uso académico y docente con cita | H, N, M | 154 obras seleccionadas, desde finales del XVIII al XXI, en siete capítulos históricos. Incluye bocetos, libretos, partituras, MusicXML y 108 grabaciones de zarzuela de 1920–1940 digitalizadas de 78 rpm |
| **Catálogos de Música Española Actual vol. 2 (CDAEM)** y su base "Estrenos de Música" | 🔍 consulta web | H | Música escénica vocal desde 1950 (incluye 67 zarzuelas). Solo cubre el borde final de nuestro corte |

### 3.3 Libretos, partituras y fondos digitalizados

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **Wikisource ES** | ✅ API de MediaWiki | P | Libretos completos de dominio público, con texto ya transcrito y corregido |
| **Ibero-Amerikanisches Institut (Berlín), colección Zarzuela** | 🔶 visor Goobi (suele exponer IIIF y OAI-PMH) | P | >1.600 libretos de los siglos XIX y XX, núcleo impreso en España entre 1800 y 1930. **Probablemente el mayor fondo digitalizado de libretos.** Las portadas aportan denominación de género, fecha y teatro de estreno |
| **Biblioteca Digital Hispánica (BNE)** | ✅ índice vía el dataset de dominio público · 🔶 IIIF y OCR por objeto | P, M | Libretos, partituras, carteles y grabaciones |
| **IMSLP** | ✅ API de MediaWiki (documentada en `IMSLP:OldAPI`) | M, H | Partituras: plantilla orquestal, números, editores. Tiene categoría propia de zarzuelas |
| **Biblioteca Virtual Miguel de Cervantes** | 🔶 tiene datos enlazados; verificar endpoint | P, H | Teatro breve, libretos, estudios |
| **Biblioteca Virtual del Patrimonio Bibliográfico** (Ministerio) | 🔶 Digibib, OAI-PMH registrado en OpenDOAR | P | Fondos de bibliotecas públicas españolas |
| **Hispana** (agregador del Ministerio) | 🔶 agrega por OAI-PMH y alimenta a Europeana | P, M | En la práctica se consume a través de Europeana |
| **Europeana** | ✅ API (clave gratuita) | M, P | Carteles, fotos, programas, partituras. La licencia va objeto a objeto |
| **Catalònica / Memòria Digital de Catalunya** | 🔶 agregación por OAI-PMH | P, M | Sarsuela catalana, Liceu, prensa catalana |
| **Gallica (BnF)** | ✅ API IIIF y SRU | P, M | Partituras y libretos editados en París; compañías españolas de gira |
| **Wikimedia Commons** | ✅ API de MediaWiki | M | Retratos, carteles y fotos de teatros, con licencia por fichero |

### 3.4 Prensa histórica (la veta de las anécdotas y la recepción)

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **Hemeroteca Digital BNE** | ✅ datasets "Hemeroteca digital. Texto completo" y "Listado de títulos" en datosabiertos.bne.es; más de 2.000 cabeceras de dominio público con el texto OCR descargable | P, N | **Fuente primaria clave**: crónicas de estrenos, críticas, polémicas, carteleras diarias. Las carteleras permiten reconstruir temporadas y reposiciones. El OCR es de calidad variable |
| **Biblioteca Virtual de Prensa Histórica** (Ministerio) | 🔶 verificar OAI-PMH | P, N | Prensa regional: estrenos y giras fuera de Madrid |
| **Prensa catalana** (vía Catalònica / ARCA) | 🔶 | P, N | Recepción en Barcelona |

### 3.5 Archivos escénicos (programas, carteles, producciones)

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **CDAEM / musicadanza.es** (INAEM) | 🔍 inventarios de programas en web (por ejemplo, Teatro de la Zarzuela 1958–2023 o Teatro Real 1917–1988) | H | Historial de producciones y repartos. Verificar si los inventarios se pueden descargar |
| **MAE – Institut del Teatre (Barcelona)**, repositorio *escena.cdmae.cat* | 🔶 DSpace (suele traer REST y OAI-PMH) · licencia propia: estudio e investigación con cita | P, H | Programas y carteles de zarzuela en Barcelona y en giras, con fecha y teatro |
| **Biblioteca Musical Víctor Espinós** (Ayuntamiento de Madrid) | 🔍 | P | Fondo histórico de partituras de zarzuela. Lo digitalizado se busca a través de Europeana o Memoria de Madrid |
| **University of Miami – Cuban Heritage Collection, Theater Ephemera** | 🔶 | P | Zarzuela en Cuba y compañías españolas en América |

### 3.6 Audio

| Fuente | Acceso | Rol | Notas |
|---|---|---|---|
| **Internet Archive** (incluido el *Great 78 Project*) | ✅ API de búsqueda y metadatos | M | Hay 78 rpm de zarzuela y cuplé. Solo se enlaza, no se redistribuye: el estado de derechos varía por grabación |
| **BNE grabaciones sonoras en dominio público** | ✅ vía el dataset de dominio público | M | Audio con licencia clara para incrustar |
| **Fundación Juan March** (78 rpm 1920–1940) | 🔍 | M | Solo enlace |

### 3.7 Excluidas o solo como referencia manual
- **Webs de aficionados** (por ejemplo, zarzuela.net): se pueden usar como pista, pero nunca como evidencia en la base, salvo que citen una fuente primaria verificable que se ingiera aparte.
- **Hemerotecas de diarios privados** (ABC, La Vanguardia): son de consulta gratuita, pero sus términos no permiten la ingesta automatizada. Solo se enlazan.
- **SGAE / Fundación SGAE**: sin acceso abierto a datos.

### 3.8 Fase 0: verificación de acceso
Por cada fuente marcada con 🔶 o 🔍, el **primer paso del proyecto** es:
1. leer sus términos de uso y su `robots.txt`;
2. localizar el endpoint (OAI-PMH, IIIF, REST);
3. descargar una muestra de 10 registros;
4. registrar la licencia efectiva.

El resultado se guarda en `sources.yaml`, que **gobierna el pipeline**: una fuente sin entrada validada en ese fichero no se ingiere.

---

## 4. Pipeline

```
 descubrir ─► capturar ─► segmentar ─► extraer ─► resolver ─► validar ─► publicar en la base
 (SPARQL,     (instantánea  (pasajes      (mapeo       (entidades,   (reglas,       (versionada)
  categorías,  + hash +      con           estructurado conflictos,   confianza)
  enlaces)     licencia)     offsets)      + LLM)       vocabulario)
```

1. **Descubrir.** Semilla en Wikidata: obras cuyo género es zarzuela o sus subgéneros, más compositores y libretistas asociados. Se amplía con categorías de Wikipedia, autoridades BNE y enlaces salientes. Se mantiene un registro del corpus con lo que está dentro, lo que está fuera y por qué.
2. **Capturar.** Cada documento se guarda como `{url, id_estable, fecha, hash, licencia, contenido_crudo}`. Nunca se sobrescribe: si al refrescar la fuente ha cambiado, se guarda una instantánea nueva junto a la anterior.
3. **Segmentar.** El texto se divide en pasajes (párrafos o frases), cada uno con posiciones `char_start` y `char_end` y un hash.
4. **Extraer.**
   - *Fuentes estructuradas* (Wikidata, BNE, MusicBrainz): mapeo determinista a afirmaciones, con `source_kind=structured`.
   - *Texto*: un LLM con salida JSON estricta propone afirmaciones y narrativas, y **debe citar el `passage_id` y la subcadena literal** que las respalda. Si esa subcadena no aparece exactamente en el pasaje, la propuesta se descarta.
   - *Matices*: se extraen tal cual las expresiones de duda ("se dice", "parece que", "según la tradición") para alimentar los campos `status` y `hedge`.
5. **Resolver.**
   - *Entidades*: primero por IDs externos; si no los hay, por nombre, fechas y contexto, con umbral. Lo que no se resuelve con suficiente seguridad queda como entidad provisional sin fusionar.
   - *Conflictos*: se detectan por propiedad y se enlazan entre sí.
   - *Clasificaciones*: se mapean al vocabulario.
6. **Validar.** Reglas de integridad: un compositor no puede morir antes de un estreno póstumo sin marca, las fechas deben ser plausibles, toda afirmación necesita evidencia. La confianza se calcula a partir de reglas y concordancia entre fuentes.
7. **Exportar.** Cada construcción o refresco queda fechada en `manifest.json` y en el pie de la web.

**Construcción y refresco a demanda.** No hay tareas programadas. La base se construye una vez y se refresca **cuando tú lo decides**. Todos los pasos son **reanudables e idempotentes**: si el proceso se corta, retoma donde lo dejó y reutiliza las capturas y la caché de respuestas.

`make refresh` (o `kb refresh [--source X] [--since FECHA]`) es **incremental**:

1. **Comprueba si algo ha cambiado.**
   - Wikipedia y Wikisource: se compara el último `oldid`.
   - Wikidata: se compara la fecha de modificación de cada entidad.
   - Resto de fuentes: se compara el hash del contenido o la fecha OAI-PMH.
2. **Descarga solo lo cambiado o nuevo.** Las instantáneas anteriores se conservan.
3. **Resegmenta y reextrae solo los pasajes cuyo hash ha cambiado.** Los no cambiados salen de la caché, sin llamadas al LLM.
4. **Revisa las afirmaciones** que se apoyaban en pasajes desaparecidos o modificados.
   - Si la nueva versión las sigue respaldando, se actualiza la evidencia.
   - Si no, pasan a `deprecated` y quedan registradas en el informe.
5. **Regenera solo las salidas afectadas** (fichas, monográficos, preguntas) y las vuelve a validar.
6. **Informe del refresco**: afirmaciones nuevas, modificadas y retiradas, conflictos nuevos y salidas regeneradas. Sirve para ver qué ha cambiado antes de reconstruir la web.

Con `--dry-run` solo se hace el paso 1: muestra qué ha cambiado y cuántas llamadas al LLM costaría procesarlo, sin tocar nada.

---

## 5. Esquema físico (borrador)

```sql
documents(id, source, url, stable_id, retrieved_at, sha256, license, reuse: reusable|consult_only, mime, raw_path)
passages(id, document_id, char_start, char_end, text, sha256, lang, ocr_conf)
entities(id, type, label, labels_alt JSON, external_ids JSON, provisional BOOL)
claims(id, subject_id, property, value_json, qualifiers_json, status, hedge,
       confidence, source_kind, extracted_by_json, created_at)
claim_evidence(claim_id, passage_id, quote, quote_start, quote_end)
claim_relations(claim_a, claim_b, relation)          -- conflicts_with, supports, refines
narratives(id, type, title, summary, time_edtf, place_id, status, confidence)
narrative_entities(narrative_id, entity_id, role)
narrative_evidence(narrative_id, passage_id, quote, quote_start, quote_end)
concepts(id, scheme, pref_label, alt_labels JSON, hidden_labels JSON, scope_note, broader JSON, related JSON)
outputs(id, kind, kb_version, created_at, content_path, validation_report_json)
output_citations(output_id, segment_id, claim_id | narrative_id | passage_id)
```

Índices auxiliares:
- búsqueda de texto completo (FTS5) sobre `passages`;
- índice vectorial sobre `passages` y `narratives`, para recuperar material por similitud.

---

## 6. Generación anclada y validación

**Regla de oro:** una salida es una lista de **segmentos** (frases, preguntas, puntos de un cronograma). Cada segmento tiene ≥1 cita a una afirmación, narrativa o pasaje. Un segmento sin cita no se publica.

**Proceso de generación:**
1. **Recuperar** el material relevante: afirmaciones por consulta estructurada; narrativas y pasajes por búsqueda de texto y vectorial.
2. **Redactar** con el LLM a partir **solo** de ese material, emitiendo los IDs citados en cada segmento.
3. **Validar**, segmento a segmento:
   - **Existencia**: los IDs citados existen y pertenecen al material entregado.
   - **Literales exactos**: las fechas, cifras y nombres propios del segmento aparecen en el material citado, ya normalizados.
   - **Respaldo semántico**: un juez independiente (otro modelo, otra instrucción y sin ver el borrador previo) evalúa si el material citado respalda el segmento: sí, parcialmente o no.
   - **Matices**: si la evidencia es `legendary` o `attributed`, el segmento tiene que conservar el matiz ("según la tradición…").
4. **Decidir**: los segmentos que fallan se regeneran una vez. Si vuelven a fallar, se eliminan y quedan registrados. Cada salida guarda su informe de validación.

**Presentación:** cada dato muestra una nota o enlace a su fuente, con el fragmento literal y la fecha de consulta.

---

## 7. Productos derivados

| Producto | Cómo se genera | Anclaje |
|---|---|---|
| **Cronogramas** | Consulta directa de afirmaciones con fecha (estrenos, nacimientos, reposiciones). Las fechas imprecisas se dibujan como intervalos | Cada punto → afirmación → pasajes |
| **Redes** | Grafo compositor–libretista–teatro–compañía–intérprete | Cada arista → afirmación |
| **Mapas** | Teatros y estrenos por ciudad y época | Coordenadas obtenidas de Wikidata |
| **Estadísticas** | Producción por año, por subgénero, colaboraciones más frecuentes | Consultas reproducibles, con la consulta publicada |
| **Cuestionarios** | Plantillas sobre afirmaciones `asserted` con confianza alta. Los distractores son valores reales de la base del mismo tipo (otros compositores, otros años) que se comprueba que *no* son ciertos para esa obra. Se excluyen las afirmaciones disputadas, o se convierten en preguntas sobre la propia disputa | Cada pregunta y su respuesta → afirmación |
| **Monográficos** (obra, autor, teatro, década, subgénero) | Esquema fijo de secciones, cada una alimentada por una consulta, y redacción anclada (§6) | Cita por frase |
| **Fichas** | Plantilla determinista, sin LLM | Cita por campo |
| **Anécdotas del día / curiosidades** | Selección de narrativas `documented` o `reported` | Cita al pasaje original (por ejemplo, la crónica de prensa) |

---

## 8. Arquitectura: construcción en Python, web React sobre JSON estáticos

El proyecto tiene **dos programas distintos**, y no tienen por qué compartir lenguaje:

```
  ┌──────────── CONSTRUCCIÓN (máquina local) ──────────────────────┐        ┌──── SITIO (estático) ────┐
  fuentes ─► pipeline ─► base SQLite (verdad) ─► exportador ─► /data/*.json ──► HTML + JS en el navegador
                                                    └─► /data/kb.sqlite (opcional) ─► consultas en el navegador
  └────────────────────────────────────────────────────────────────┘        └─────────────────────────┘
```

**El sitio es 100 % estático**: HTML, JS y JSON, sin servidor y sin LLM en tiempo de ejecución. Todos los textos derivados y los cuestionarios se generan **antes**, durante la construcción, y llegan al sitio ya validados y con sus citas. Así el visitante nunca ve un texto generado al vuelo sin validar.

### 8.1 ¿Por qué Python para la construcción?

| Necesidad | Python | Node/TypeScript |
|---|---|---|
| SPARQL, MediaWiki, OAI-PMH, IIIF | Bibliotecas maduras (`SPARQLWrapper`, `sickle` para OAI-PMH) | Basta con `fetch`; para OAI-PMH hay menos opciones |
| Limpieza de OCR y normalización de texto antiguo | Ecosistema amplio (`ftfy`, `regex`, `rapidfuzz`) | Más pobre |
| Procesamiento de lenguaje en español (entidades, segmentación de frases) | `spaCy` con modelos para español | Limitado |
| Embeddings locales para la búsqueda | `sentence-transformers` | `transformers.js` (más lento) |
| SDK del LLM y salidas con esquema | Ambos muy bien | Ambos muy bien |
| Coherencia con el frontal | Dos lenguajes | Un solo lenguaje |

**Recomendación: Python para la construcción y React/TypeScript para la web.** La parte difícil del proyecto (OCR, desambiguación, extracción) está en la construcción, y ahí Python es claramente mejor. El frontal solo lee JSON, así que no comparten código.

Si prefieres un único lenguaje, todo en TypeScript es viable, a cambio de un procesamiento de OCR y de lenguaje natural más pobre.

### 8.2 Construcción
- Python 3.12 con `uv`; `httpx` (límite de peticiones y caché en disco), `pydantic` para los esquemas.
- Base SQLite con FTS5 y `sqlite-vec` como fuente de verdad. Las instantáneas crudas se guardan comprimidas en disco, direccionadas por hash.
- LLMs: Qwen 3.5 4B local (`llama-server`) y Claude Code CLI (`claude -p`) tras una interfaz común (§8.4). Salidas JSON validadas por esquema; se registran el motor, el modelo y la versión del prompt en cada extracción.
- CLI con órdenes `kb discover`, `kb capture`, `kb extract`, `kb resolve`, `kb generate`, `kb export` y `kb check`.
- El pipeline se ejecuta en local, a mano. Sin CI ni tareas programadas; se refresca con `make refresh` cuando se quiera (§4).

### 8.3 Web: React con JSON estáticos como API

La web es una **aplicación React (Vite + TypeScript)**. Sus "endpoints" son ficheros JSON estáticos que genera `kb export`. No hay servidor: el hosting sirve los ficheros y React los consulta con `fetch` como si fueran una API REST de solo lectura.

#### API estática (`/api/v1/…`)

```
/api/v1/manifest.json                  versión de la base, fecha de generación, recuentos, versión del esquema
/api/v1/schema/*.schema.json           JSON Schema de cada tipo (contrato con el frontal)

/api/v1/works/index.json               listado ligero: id, título, año, género, compositor
/api/v1/works/{id}.json                ficha completa: entidad + afirmaciones + narrativas + IDs de evidencia
/api/v1/people/index.json · /people/{id}.json
/api/v1/venues/index.json · /venues/{id}.json            teatros
/api/v1/companies/{id}.json                              compañías
/api/v1/genres/index.json · /genres/{id}.json            vocabulario (conceptos, sinónimos, relaciones)

/api/v1/evidence/{entity_id}.json      pasajes que respaldan las afirmaciones de esa entidad
                                       (troceado por entidad para no descargar la base entera)
/api/v1/sources/{doc_id}.json          ficha de la fuente: URL, revisión, fecha de captura, licencia

/api/v1/views/timeline.json            datos precalculados para el cronograma
/api/v1/views/network.json             grafo de colaboraciones
/api/v1/views/map.json                 teatros con coordenadas y estrenos por lugar
/api/v1/views/stats.json               agregados, junto con la consulta que los produjo

/api/v1/quizzes/index.json · /quizzes/{id}.json           banco de preguntas validado
/api/v1/monographs/index.json · /monographs/{id}.json     textos segmentados con citas por segmento

/api/v1/search/index.json              índice de MiniSearch serializado (trocear por letra si crece)
```

**Reglas de la API:**
- Los IDs son estables (preferentemente el QID de Wikidata, o un ID propio prefijado si la entidad no tiene QID), así las URLs no cambian entre versiones.
- Hay un **contrato tipado**: los modelos `pydantic` del pipeline exportan JSON Schema, del que se generan los tipos TypeScript (`json-schema-to-typescript`). Si el exportador rompe el contrato, `kb check` falla.
- La ruta lleva `/v1/` y solo cambia si cambia el formato. Los refrescos de contenido mantienen los mismos IDs y las mismas URLs.
- Las citas viajan con los datos: cada afirmación o segmento lleva sus `evidence_ids`, que el frontal resuelve contra `/evidence/{entity_id}.json` y `/sources/{doc_id}.json`.
- Tamaño: ningún fichero debería superar ~500 KB comprimido. Si un fichero se pasa (por ejemplo, el índice de búsqueda o las evidencias de una obra muy citada), se trocea.

#### Frontal
- **React 19 + Vite + TypeScript**.
- **React Router** para las rutas `/obra/:id`, `/persona/:id`, `/teatro/:id`, `/genero/:id`, `/cronologia`, `/red`, `/mapa`, `/cuestionarios/:id` y `/monograficos/:id`.
- **TanStack Query** como cliente de la "API": caché y estados de carga y error. Los JSON se cachean de forma indefinida dentro de cada versión de la base.
- **Visualizaciones**:
  - Observable Plot o D3 para cronogramas, con las fechas imprecisas como intervalos;
  - Cytoscape.js (`react-cytoscapejs`) para las redes;
  - `react-leaflet` para los mapas;
  - visor IIIF (Mirador o Tify) para libretos y partituras.
- **Búsqueda**: MiniSearch en el navegador, sobre el índice exportado, con normalización de acentos.
- **Componente de cita** reutilizable: un marcador junto a cada dato que abre un panel con el fragmento, la fuente, la fecha de consulta, la licencia y el enlace a la revisión exacta. Si la fuente es de solo consulta, el panel muestra la cita y el enlace, pero no el fragmento.
- **Cuestionarios**: banco de preguntas pregenerado. React solo baraja, puntúa y muestra la fuente después de cada respuesta.

#### Despliegue y limitaciones de una SPA
- **Publicación manual**: `npm run build` genera la carpeta `web/dist/` (React compilado + `api/v1/`). Esa carpeta se sirve tal cual en local o se sube a mano a cualquier hosting estático.
  - Para que funcionen las rutas limpias en una SPA, el servidor debe redirigir cualquier ruta a `index.html`.
  - Si el hosting no lo permite, se usan rutas con `#`.
- **Buscadores**: una SPA indexa peor que HTML prerenderizado. Si eso importa, se puede añadir más adelante un prerenderizado de las fichas (`vite-plugin-ssg` o similar) sin cambiar ni la API ni los componentes.

### 8.4 Uso de LLMs (solo en la construcción): modelo local + Claude Code CLI

No hay API key de Anthropic. Se usan dos motores detrás de una misma interfaz (`kb/llm/`):

- **Local**: Qwen 3.5 4B en `llama-server`, a través de su endpoint compatible con OpenAI (`/v1/chat/completions`). Sin coste por llamada, ilimitado y rápido en lote.
- **Claude**: la CLI de Claude Code en modo no interactivo (`claude -p`), autenticada con la suscripción del usuario. Más capaz, pero sujeta a los **límites de uso de la suscripción**, así que se reserva para tareas de poco volumen y alto valor.

El LLM **nunca aporta datos**. Interpreta y redacta a partir de pasajes que se le entregan, y todo lo que produce pasa por un control determinista. La web no llama a ningún LLM.

#### Qué hace cada motor

| # | Tarea | Volumen | Motor | Control posterior |
|---|---|---|---|---|
| 0 | **Filtro de relevancia**: ¿este pasaje (por ejemplo, una página de prensa OCR) habla de teatro lírico? | muy alto | Local | Solo descarta, no crea datos. Se audita una muestra para medir cuánto material relevante se pierde |
| 1 | **Extracción de afirmaciones** | alto | Local; los fallos y los casos de baja confianza se escalan a Claude | `quote` debe ser una subcadena **exacta** del pasaje; la propiedad debe estar permitida; el valor tiene que pasar el análisis de tipo (EDTF, entidad, cantidad) |
| 2 | **Extracción de narrativas** (anécdotas, recepción) | medio | Claude (exige matiz: tono, ironía, "se cuenta que") | Citas literales verificadas; marca de estado (`documented`, `reported`, `legendary`) |
| 3 | **Clasificación de género** | medio | Local; si responde `none`, se escala a Claude | `concept_id` debe existir en el vocabulario |
| 4 | **Desempate de entidades** (solo los casos ambiguos) | bajo | Claude | `null` deja la entidad como provisional; nunca hay fusión sin respaldo |
| 5 | **Redacción** de monográficos, resúmenes y enunciados de preguntas | bajo | Claude (Sonnet) | Validador de §6 |
| 6 | **Juez de respaldo** | bajo y medio | Claude con **otro modelo** que el redactor (Haiku si redacta Sonnet), contexto nuevo y solo el material citado | Se combina con las comprobaciones deterministas de literales |

**No se usa para**: las fuentes estructuradas, el descubrimiento, los cronogramas, las redes, los mapas, las estadísticas ni los hechos y distractores de los cuestionarios.

**Embeddings** para la búsqueda: modelo multilingüe local con `sentence-transformers` (o el endpoint de embeddings de `llama-server`). No hace falta ninguna API.

#### Expectativas sobre el modelo de 4B
Un modelo de 4B parámetros es útil para extracción y clasificación acotadas con salida restringida, pero **rinde claramente por debajo de un modelo como Haiku** con:
- prosa decimonónica,
- texto OCR con ruido,
- ironía,
- atribuciones indirectas.

El diseño lo compensa de tres maneras:
1. **Salida restringida por gramática**: `llama-server` acepta `response_format` con JSON Schema y lo convierte en una gramática, así que la salida siempre es JSON válido.
2. **Verificación literal de las citas**: el modelo no puede colar un dato que no esté en el pasaje. Su error típico será **perder** datos (menos cobertura), no **inventarlos**.
3. **Medición**: `kb eval` compara el modelo local con Claude sobre el conjunto de oro de cada tarea. Si la cobertura de una tarea con el modelo local queda por debajo del umbral de `llm.yaml`, esa tarea pasa a Claude o se acepta la pérdida de cobertura de forma explícita.

Configuración recomendada para el modelo local:
- temperatura 0;
- modo de razonamiento desactivado en extracción y clasificación (si el modelo lo tiene);
- pasajes cortos, de un párrafo;
- un ejemplo resuelto en el prompt;
- contexto ajustado (`-c`) a lo que el prompt necesita.

#### Cómo se invoca Claude Code CLI
Cada llamada es un proceso:

```bash
cd "$(mktemp -d)" && claude -p \
  --model sonnet \
  --output-format json \
  --json-schema "$(cat schema/claim_extraction.json)" \
  --system-prompt "$(cat prompts/extract_claims.v3.md)" \
  --permission-mode dontAsk \
  < input.txt
```

- **Directorio temporal vacío**: sin `--bare`, `claude -p` carga el `CLAUDE.md`, los hooks y los servidores MCP del directorio de trabajo. Ejecutarlo en una carpeta vacía evita que el contexto del repositorio contamine la extracción. (`--bare` no sirve aquí porque no usa el login de la suscripción, solo una API key.)
- **`--system-prompt`** sustituye el prompt por defecto de Claude Code, que está orientado a programar, por el prompt de la tarea.
- **`--permission-mode dontAsk`** sin herramientas permitidas: el modelo solo responde, no puede ejecutar nada.
- La respuesta llega en `structured_output`. Se valida otra vez contra el esquema pydantic.
- **`total_cost_usd`** del JSON de respuesta se registra como estimación del consumo, aunque con la suscripción no se factura por llamada.

#### Organización de las ejecuciones
- **Cola persistente** de trabajos en SQLite: reanudable, con reintentos y espera exponencial cuando aparece un límite de uso.
- **Concurrencia**:
  - Claude: 1–2 procesos simultáneos, con un tope diario de llamadas en `llm.yaml` para no agotar la suscripción y dejar margen para el uso normal;
  - local: paralelismo según los *slots* de `llama-server` (`-np`).
- **Caché de respuestas** con clave `hash(motor, modelo, versión del prompt, entrada)`: si se vuelve a ejecutar con los mismos datos, no se repite ninguna llamada.
- **Trazabilidad**: cada afirmación o segmento registra el motor, el modelo, la versión del prompt y la fecha (`extracted_by`).
- **Prompts versionados** en `prompts/*.md`. Cambiar un prompt obliga a pasar `kb eval`.
- **`kb estimate`** cuenta las llamadas pendientes por motor y tarea antes de lanzar nada.

#### Dónde se ejecuta
Todo corre **en tu máquina**, donde están `llama-server` y la sesión de Claude Code, lanzado a mano: la construcción inicial y los refrescos (`make refresh`) cuando se quiera. La cola y la caché permiten **interrumpir y reanudar** cuando se alcanza el límite de uso de la suscripción, y hacen que un refresco solo gaste llamadas en lo que ha cambiado.

### 8.5 Diseño de la experiencia: "Entre bastidores"

**Idea central:** explorar la base es como recorrer un teatro de 1900. El parallax tiene aquí un sentido literal y no es un adorno: los decorados del teatro lírico se hacían con **bastidores, bambalinas y telones pintados en planos sucesivos**, y la profundidad del escenario nacía de esas capas. La web reconstruye ese escenario por capas, y el desplazamiento del usuario mueve los planos a distinta velocidad.

**El elemento memorable es uno solo:** el escenario por capas que se abre al entrar. Todo lo demás se mantiene sobrio para que los datos y las fuentes respiren.

#### Recorridos que invitan a descubrir

| Recorrido | Qué es | De dónde salen los datos |
|---|---|---|
| **La función** (portada) | Se abre el telón y el desplazamiento recorre la historia del teatro lírico como una obra en actos y cuadros (por ejemplo, Acto I: siglo XVIII; cuadro: la tonadilla). Cada cuadro tiene su decorado por capas, con las obras, personas y teatros de esa época entrando y saliendo de escena | Afirmaciones con fecha y el vocabulario de géneros |
| **La cartelera** | Una máquina del tiempo: eliges un día ("17 de febrero de 1894") y ves qué se representaba en cada teatro, con la crónica del día siguiente si existe | Carteleras y crónicas de la Hemeroteca Digital BNE (fase 5) |
| **El plano** | Plano histórico de Madrid (y de otras ciudades) con los teatros, muchos ya derribados. Al pulsar uno se ve su vida: estrenos, compañías, incendios, cierre | Coordenadas y afirmaciones de cada teatro; plano de dominio público por IIIF |
| **La compañía** | Red viva de compositores, libretistas, intérpretes y empresarios. Las colaboraciones se iluminan al pasar por encima, y una línea de tiempo filtra la red por años | `views/network.json` |
| **Tirar del hilo** | Al pie de cada ficha, caminos para seguir: "estrenada la misma semana", "el mismo libretista", "en el mismo teatro", "quien también cantó este papel". Y un botón de **función sorpresa** que lleva a una obra o anécdota al azar con buena evidencia | Consultas sobre la base |
| **Ensayo general** | Cuestionarios por tema, época o persona. Tras cada respuesta se muestra la fuente, así que aprender y verificar ocurren a la vez | Banco de preguntas validado |
| **Monográficos** | Lectura larga con desplazamiento narrativo: las imágenes de época (carteles, portadas de libreto, fotos) entran por capas acompañando el texto | Monográficos validados e imágenes con licencia reutilizable |

#### El apuntador: cómo se ven las fuentes
En el teatro, el **apuntador** sopla el texto desde su concha en el proscenio. Aquí es el componente de cita: cada dato lleva una pequeña concha que, al pulsarla, despliega desde abajo:
- el fragmento literal de la fuente (o solo la referencia si la fuente es de solo consulta);
- la fuente, la revisión y la fecha de consulta;
- el estado del dato (documentado, según la prensa, leyenda, en disputa).

Los datos en disputa muestran **las dos versiones lado a lado**, y las leyendas se distinguen visualmente de los hechos documentados. La veracidad forma parte del juego, no es una nota al pie.

#### Dirección visual
- **Color**, tomado de la cromolitografía de los carteles y de la luz de gas, no del rojo terciopelo genérico de "teatro":

| Nombre | Hex | Uso |
|---|---|---|
| Noche de verbena | `#1B2240` | Fondo del escenario, modo oscuro |
| Candileja | `#F2C14E` | Luz, foco y estado activo |
| Mantón | `#C4213B` | Acento: telón y llamadas a la acción |
| Azulejo | `#2B5BA8` | Enlaces, red y mapa |
| Papel de libreto | `#E8E4DA` | Superficies de lectura, modo claro |
| Tinta | `#20201E` | Texto |

- **Tipografía**:
  - una Didone de alto contraste para los rótulos grandes, con el espíritu de los carteles de la época (por ejemplo, *Bodoni Moda*, que tiene tamaño óptico variable);
  - una sans muy legible para la interfaz y la lectura (por ejemplo, *Atkinson Hyperlegible*).
  - Los títulos de obra se componen como en los carteles (tamaños contrastados y título dominante), pero solo en las cabeceras de ficha, no en todas partes.
- **Imágenes**: carteles, portadas de libreto y fotos de **dominio público o licencia libre**, obtenidas por IIIF de la BNE, Europeana, Commons o el IAI. Las capas del parallax se recortan de carteles reales (segmentación local con `rembg` u otra herramienta similar) o se dibujan como SVG original. **Cada imagen cita su fuente con el mismo componente del apuntador.**
- **Movimiento**:
  - un único momento orquestado: el telón al entrar;
  - el parallax ligado al desplazamiento;
  - el resto del movimiento solo responde a acciones del usuario: abrir una ficha, desplegar una cita, filtrar la red.
- **Sonido**, opcional y desactivado por defecto: los fragmentos de 78 rpm con licencia clara se pueden escuchar desde la ficha de cada número musical.

#### Técnica
- **Parallax y desplazamiento**:
  - CSS *scroll-driven animations* (`animation-timeline: view()`) donde el navegador las soporte;
  - `motion` (Framer Motion) para React como alternativa y para las transiciones entre rutas;
  - `lenis` para un desplazamiento suave, con opción de desactivarlo.
- **Rendimiento**:
  - solo se animan `transform` y `opacity`;
  - las capas en WebP o AVIF con tamaños responsivos, y carga diferida por cuadro;
  - presupuesto de LCP < 2,5 s en móvil medio.
- **Accesibilidad**:
  - con `prefers-reduced-motion` los planos quedan fijos y el telón no se anima;
  - todo se puede usar con teclado y con foco visible;
  - contraste AA en ambos modos;
  - las visualizaciones tienen una alternativa en tabla.
- **Móvil**:
  - parallax reducido a 2 o 3 planos;
  - gestos de deslizar entre cuadros;
  - el apuntador se abre como hoja inferior.

#### Proceso de diseño
1. **Prototipo de la portada** (telón y dos cuadros) con datos reales del piloto, antes de construir el resto de vistas. Revisar en móvil y escritorio y ajustar.
2. Ficha de obra con el apuntador.
3. La cartelera y el plano.
4. El resto de recorridos.

---

## 9. Fases

| Fase | Contenido | Criterio de salida |
|---|---|---|
| **0. Reconocimiento** | Verificar las fuentes 🔶 y 🔍 (§3.8) y generar `sources.yaml`; prototipo de consulta SPARQL de semilla | `sources.yaml` completo con licencias efectivas |
| **1. Piloto** | ~50 obras canónicas de varios géneros (barroca, tonadilla, grande, chico, siglo XX): Wikidata, Wikipedia ES y Wikisource; capas D, P, E y A | 100 % de las afirmaciones con evidencia; ≥95 % de precisión sobre el conjunto de oro |
| **2. Narrativa y vocabulario** | Extracción de narrativas; vocabulario de géneros con sinónimos (contrastado con el Diccionario de la Zarzuela); detección de conflictos | Vocabulario ≥40 conceptos con sus fuentes; conflictos visibles |
| **3. Web mínima** | React + API JSON estática: fichas de obra y persona, cronograma, red, 1 cuestionario de 30 preguntas, 1 monográfico, todo con el validador activo | 0 segmentos sin cita; `web/dist/` navegable en local |
| **4. Ampliación** | Corpus completo; datasets de la BNE, MusicBrainz, RISM, IMSLP, Europeana, libretos del IAI con sus portadas | Corpus completo procesado |
| **5. Fuentes primarias** | Texto completo de la Hemeroteca Digital BNE: crónicas de estrenos y carteleras para reconstruir temporadas | Narrativas con fuente de prensa coetánea |
| **6. Cierre y refresco** | Construcción completa, informe de calidad (cobertura, conflictos, tasas del validador), `kb refresh` incremental con `--dry-run` e informe de cambios | `kb check` en verde; un refresco sin cambios no hace llamadas al LLM ni modifica la base |

---

## 10. Riesgos y decisiones abiertas

- **Alcance** (decidido, §1 bis): todos los géneros españoles de la época. El riesgo es la dispersión. La mitigación es que la periferia (cuplé, danza, música incidental) solo entra cuando enlaza con obras o personas del núcleo.
- **Fuentes sin licencia de reutilización** (por ejemplo, el Diccionario de la Zarzuela del ICCMU o la Fundación Juan March): se usan como evidencia de solo consulta y no se reproduce su texto en el sitio. Si se quiere más, se puede pedir permiso por escrito a la institución.
- **Fiabilidad de la prensa coetánea**: las crónicas de época son fuente primaria de *lo que se dijo*, no necesariamente de *lo que pasó*. Las afirmaciones extraídas de prensa se marcan como "según [cabecera, fecha]".
- **Desambiguación**: hay muchas obras con títulos homónimos, autores con seudónimos y colaboraciones a cuatro manos. La mitigación es priorizar los IDs externos y dejar como provisionales las entidades sin resolver.
- **Calidad del OCR** en la prensa antigua: se umbraliza y se exige corroboración.
- **Sesgo de cobertura**: Wikipedia sobrerrepresenta las obras célebres. Las estadísticas mostrarán la cobertura junto a los datos.
- **Juez del validador**: un juez LLM también puede equivocarse. Se mitiga con las comprobaciones literales deterministas, con el conjunto de oro y con un muestreo de su tasa de error antes del cierre.
- **Licencias**: atribución y CC BY-SA en todo lo derivado de Wikipedia; conviene revisar Europeana objeto a objeto.

---

## 11. Lo que necesito de ti

- **`llama-server`** con Qwen 3.5 4B accesible en local (URL y puerto en `llm.yaml`).
- **Claude Code** con la sesión iniciada en la misma máquina (suscripción; sin API key).
- **API key de Europeana** (gratuita).
- Un **repositorio git** (local o remoto) para el código.
- Opcional: **token de Discogs** (gratuito, con cuenta).
- Arquitectura confirmada (§8): construcción en Python, en local, con refresco incremental a demanda; web React con API JSON estática.

---

## 12. Guía de implementación (para Claude Code)

### 12.1 Estructura del repositorio
```
/
├─ CLAUDE.md                 resumen de este plan y convenciones (ver 12.3)
├─ plan.md                   este documento
├─ config/
│  ├─ sources.yaml           fuentes validadas en la fase 0 (endpoint, licencia, reuse, límite de peticiones)
│  ├─ llm.yaml               motor y modelo por tarea, URL de llama-server, tope diario de llamadas a Claude, umbrales
│  └─ scope.yaml             alcance: años, conceptos núcleo y periferia
├─ vocab/genres.yaml         vocabulario inicial de géneros (estilo SKOS)
├─ prompts/                  un .md versionado por tarea de LLM (§8.4)
├─ gold/                     conjunto de oro: hechos verificados a mano, por tarea
├─ pipeline/                 paquete Python (uv)
│  ├─ kb/cli.py              discover | capture | segment | extract | resolve | validate | generate | export | check | estimate | eval | refresh
│  ├─ kb/models.py           modelos pydantic = fuente única del esquema
│  ├─ kb/sources/            un conector por fuente
│  ├─ kb/llm/                interfaz común: backend llama (OpenAI-compatible) y backend claude-cli; cola, caché, prompts
│  └─ tests/
├─ data/                     (gitignored) instantáneas crudas y kb.sqlite
├─ web/                      React + Vite + TS
│  ├─ src/api/types.ts       generado desde JSON Schema; no se edita a mano
│  └─ public/api/v1/         salida de `kb export` (gitignored; se genera en CI)
├─ Makefile                 make build (todo), make refresh (incremental), make web, pasos sueltos
```

### 12.2 Orden de trabajo
1. Esqueleto del repositorio, `models.py`, esquema SQLite y CLI vacía con pruebas.
2. Fase 0: un conector de prueba por fuente y `sources.yaml` rellenado con lo verificado.
3. Conectores de Wikidata y Wikipedia ES: piloto de 50 obras, solo con la tarea 1, más conjunto de oro y `kb eval`.
4. Validador de §6 y tareas 2, 3, 5 y 6.
5. `kb export`, que produce la API v1, y JSON Schema → tipos TypeScript.
6. Web: primero el prototipo de portada (telón y dos cuadros) con datos del piloto; después el apuntador y la ficha de obra, la cartelera, el plano y el resto (§8.5).
7. `Makefile` (`build`, `refresh`, `web`), `kb refresh` incremental e informe de cierre.
8. Fases 4–6 del §9.

### 12.3 Reglas para `CLAUDE.md`
- **No inventar datos**: ni en código, ni en fixtures, ni en el vocabulario. Todo dato de ejemplo en las pruebas debe estar marcado como ficticio. El vocabulario inicial lleva fuente por concepto.
- `models.py` es la fuente única del esquema. La base, la API y los tipos TypeScript se derivan de él.
- Ningún conector se ejecuta sin una entrada validada en `sources.yaml`. Hay que respetar el límite de peticiones y enviar un User-Agent identificable.
- Nada de llamadas al LLM fuera de `kb/llm/`. Toda llamada pasa por la cola, la caché y el tope diario. `claude -p` siempre se ejecuta en un directorio temporal vacío, con `--system-prompt` propio y sin herramientas permitidas.
- Las pruebas del pipeline usan un backend LLM simulado (respuestas grabadas) y nunca llaman a Claude ni a `llama-server`.
- `kb check` debe pasar antes de cualquier despliegue, y comprueba:
  - toda afirmación tiene evidencia;
  - todo segmento tiene cita;
  - los esquemas son válidos;
  - ningún fichero supera el tamaño máximo;
  - no se expone el texto de fuentes `consult_only`.
- Las claves (Europeana, Discogs) solo van en un `.env` local, ignorado por git.
