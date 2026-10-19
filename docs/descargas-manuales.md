# Descargas y gestiones pendientes (actualizado 2026-10-07)

Rutas relativas a la raíz del repositorio. Texto y metadatos se descargan; audio, imágenes, escaneos y PDF no (se enlazan).
Disco: ≥20 GB libres siempre; medir antes de bajar algo > ~15 GB.

## A. Descargas a mano con navegador (Cloudflare bloquea a clientes automáticos)

| # | Dataset | URL | Guardar como |
|---|---|---|---|
| 1 | BNE autoridades: personas | https://www.bne.es/media/datosgob/catalogo-autoridades/persona/persona-UTF8.zip | `data/bne/bne_autoridades/persona-csv.zip` |
| 2 | BNE autoridades: entidades | https://www.bne.es/media/datosgob/catalogo-autoridades/entidad/entidad-UTF8.zip | `data/bne/bne_autoridades/entidad-csv.zip` |
| 3 | BNE partituras | https://www.bne.es/media/datosgob/catalogo-bibliografico/partituras/musicaescrita-UTF8.zip | `data/bne/bne_partituras/partituras-csv.zip` |
| 4 | BNE grabaciones sonoras | https://www.bne.es/media/datosgob/catalogo-bibliografico/grabaciones-sonoras/grabsonoras-UTF8.zip | `data/bne/bne_grabaciones/grabaciones-csv.zip` |
| 5 | BNE Biblioteca Digital (dominio público) | https://www.bne.es/media/datosgob/bdh/dominiopublico/dominiopublico_csv-utf8.zip | `data/bne/bne_biblioteca_digital/dominiopublico-csv.zip` |
| 6 | Hemeroteca Digital: texto completo (CSV) | https://hemerotecadigital.bne.es/hd/es/fulltext-csv | `data/hemeroteca_bne/texto_completo.csv` (o `.csv.gz` / `.zip`) |
| 7 | Hemeroteca Digital: listado de títulos | misma página (buscar el enlace «Listado de títulos»; si no aparece, catálogo de datasets en https://datosabiertos.bne.es/ o datos.gob.es) | `data/hemeroteca_bne/listado_titulos.csv` |

Notas: las URLs 1–5 vienen de datos.gob.es y no se han podido probar desde el servidor; si alguna da 404, buscarla en datos.gob.es («BNE catálogo de autoridades», etc.). Tamaños desconocidos: mirar antes de bajar. #6 es probablemente el más grande (fase 5 del plan).
Después: `kb source phase0 <nombre>` y `kb source ingest <nombre>` (lo hago yo cuando estén los ficheros); con la primera muestra real se ajustan los nombres de columna y se marcan `validated: true`.

## B. Credenciales

- Discogs aparcado (2026-10-07): se prueba primero si MusicBrainz basta. Si hiciera falta: `DISCOGS_TOKEN` en `.env` (https://www.discogs.com/settings/developers → Generate new token).

## C. Gestiones por correo (borradores sin enviar en `docs/permisos/`)

- [ ] ICCMU, Diccionario de la Zarzuela (su aviso legal exige permiso escrito para cualquier uso).
- [ ] Fundación Juan March, Teatro Musical Español (uso docente/investigador con cita; pedir permiso para metadatos/MusicXML).
- [ ] CDAEM / musicadanza.es (reproducción no permitida).
- [ ] (Opcional, sin borrador) BNE: pedir excepción de Cloudflare para descargas automatizadas del dataset abierto (evita los pasos A).
- [ ] (Opcional) UCSB / DAHR: su CloudFront bloquea nuestro User-Agent identificable; pedir acceso automatizado identificado.

## D. Bloqueadas sin acción tuya viable (informativo)

- IAI (libretos): su `robots.txt` prohíbe OAI y la búsqueda; sin ellos no se puede enumerar la colección. Solo se podría con una lista de PID dada por ti (METS e IIIF sí están permitidos).
- BVMC (SPARQL): reto de Cloudflare. VIAF: Cloudflare (se cubre vía Wikidata P214).
- Catalònica y ARCA (prensa catalana): `robots.txt` lo prohíbe. La recepción en prensa catalana queda sin cubrir.
- Hemeroteca por rastreo: `robots.txt` lo prohíbe; solo por descarga manual del dataset (A6–A7).
