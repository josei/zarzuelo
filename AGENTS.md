# Zarzuelo — convenciones para agentes

Resumen de `plan.md` (leerlo para el detalle). Base de conocimiento trazable sobre zarzuela y música escénica española (1650–1960): pipeline Python local → SQLite → API JSON estática → web React.

## Reglas
- **No inventar datos**: ni en código, ni en fixtures, ni en el vocabulario. Todo dato de ejemplo en pruebas debe estar marcado como ficticio. El vocabulario inicial lleva fuente por concepto.
- `pipeline/kb/models.py` es la fuente única del esquema. La base, la API y los tipos TypeScript se derivan de él.
- Ningún conector se ejecuta sin una entrada validada en `config/sources.yaml`. Respetar el límite de peticiones y enviar un User-Agent identificable.
- Nada de llamadas al LLM fuera de `kb/llm/`. Toda llamada pasa por cola, caché y tope diario. `claude -p` siempre en un directorio temporal vacío, con `--system-prompt` propio y sin herramientas permitidas.
- Las pruebas usan un backend LLM simulado; nunca llaman a Claude ni a `llama-server`.
- `kb check` debe pasar antes de desplegar: toda afirmación con evidencia, todo segmento con cita, esquemas válidos, ningún fichero sobre el tamaño máximo, sin texto de fuentes `consult_only` expuesto.
- **Texto y metadatos se descargan siempre** (por si acaso: libretos, OCR de prensa, catálogos y datasets de texto/metadatos, comprimidos en `data/`, en streaming). **Los medios se enlazan, no se almacenan**: audio, imágenes, escaneos y PDF se guardan solo como referencia (licencia por objeto y URL/IIIF de origen). Antes de bajar un dataset de texto de más de ~15 GB (comprobar `df`: dejar siempre ≥20 GB libres), medir con HEAD y avisar al responsable.
- Las claves (Europeana, Discogs) solo en `.env` local, ignorado por git.

## Comandos
- `make setup` crea `.venv` e instala el paquete; `make test` pasa las pruebas; `make check` ejecuta `kb check`.
