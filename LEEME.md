# Despliegue de Zarzuelo (sitio estático)

`make site` genera el sitio en `site/` (o en la carpeta de la variable `ZARZUELO_SITE`). `site/` está ignorada por la rama principal: es un *worktree* de la rama huérfana `gh-pages`, la que sirve GitHub Pages desde el mismo repositorio (monorepo). Se sincroniza con `rsync` y se conservan su `.git` y su `CNAME`. Contiene: HTML, JS, CSS, tipografías y la API en JSON. **No hay servidor, base de datos ni modelos de lenguaje en tiempo de ejecución**; todo el texto derivado se generó y validó antes.

## Subirlo
Cualquier alojamiento de ficheros estáticos sirve: Cloudflare Pages, GitHub Pages, Netlify, S3/CloudFront, nginx o Apache. Basta con copiar el contenido de `site/` (unos 15 MB, 2.200 ficheros, 2 MB comprimido).
- Las rutas usan `#` (`/#/obra/Q123`), así que **no hace falta ninguna regla de reescritura**.
- Funciona en la raíz del dominio o en un subdirectorio (`https://ejemplo.org/zarzuelo/`; con la barra final).
- Probarlo en local: `cd site && python3 -m http.server 8000` y abrir `http://localhost:8000/`. No funciona abriendo `index.html` con doble clic (`file://`), porque el navegador bloquea la carga de los JSON.

## Alojamientos concretos
- **GitHub Pages:** sube el contenido de `site/` (por ejemplo a la rama `gh-pages`, o con la acción oficial de Pages). Va incluido `.nojekyll`, que evita que Jekyll descarte ficheros. Con un sitio de proyecto, la URL es `https://usuario.github.io/repo/` (GitHub añade la barra final).
- **S3:** `aws s3 sync site/ s3://mi-bucket/ --delete` con el alojamiento web estático activado (`index.html` como documento de inicio). Para HTTPS y compresión, ponle CloudFront delante. Los JSON conservan siempre el mismo nombre: si cambias datos, invalida la caché (`/api/*`) o usa una vida de caché corta para `api/`.
- Todos los nombres de fichero son ASCII y sin dos puntos, para que no den problemas en ningún alojamiento ni al descomprimir en Windows.

## Primera publicación en GitHub Pages (monorepo)
Requiere el primer commit en `main` y un repositorio público llamado `zarzuelo`; la web quedará en `https://<usuario>.github.io/zarzuelo/`.
1. `rm -rf site && git worktree add --detach site && cd site && git checkout --orphan gh-pages && git rm -rf . && cd ..` (rama huérfana, sin el código; con Git 2.42 o posterior basta `git worktree add --orphan -b gh-pages site`).
2. `make site`, y en `site/`: `git add -A && git commit -m "Sitio" && git push origin gh-pages`.
3. En GitHub: Settings → Pages → Source: *Deploy from a branch* → `gh-pages` / raíz.

## De qué depende en ejecución
- **Nada propio.** Las tipografías van incluidas (licencia OFL).
- **Solo la página «El plano»** pide teselas a OpenStreetMap y, si activas un plano antiguo, al servicio del IGN (CC BY 4.0). Es externo por naturaleza (son mapas): si ese servicio cayera, el resto de la web sigue funcionando.
- Los PDF y demás medios **no se alojan**: se enlazan a su institución.

## Licencias y atribución
Los textos derivados de Wikipedia son CC BY-SA 4.0 (con atribución e historial en cada ficha); IMSLP, Europeana y demás se muestran con la licencia de cada objeto. `kb check` (incluido en `make site`) impide publicar texto de fuentes de solo consulta.

## Actualizar
`make refresh` (incremental) → `make site` → volver a subir (en `site/`: `git add -A && git commit && git push origin gh-pages`).
