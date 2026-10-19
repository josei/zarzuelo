PY ?= .venv/bin/python
KB ?= .venv/bin/kb
# Carpeta de destino del sitio estático. Se toma de la variable de entorno ZARZUELO_SITE, si no del `.env` local y, por defecto, `site/`
# (ignorada por git: es un worktree de la rama gh-pages, ver docs/despliegue.md).
ZARZUELO_SITE ?= $(or $(shell sed -n 's/^ZARZUELO_SITE=//p' .env 2>/dev/null | tail -n 1),site)
SITE = $(ZARZUELO_SITE)

.PHONY: setup test init check build refresh web export site
setup:
	python3 -m venv .venv
	.venv/bin/pip install -e 'pipeline[dev]'
test:
	$(PY) -m pytest pipeline/tests -q
init:
	$(KB) init
check:
	$(KB) check
build:
	$(KB) discover && $(KB) capture && $(KB) segment && $(KB) extract && $(KB) resolve && $(KB) validate && $(KB) generate && $(KB) export
refresh:
	$(KB) refresh
web: export
	cd web && npm install --no-audit --no-fund
	cd web && npm run build
export:
	$(KB) export

# Sitio estático autocontenido: exporta la API, valida, compila la web y lo copia a $(SITE) listo para subir a cualquier alojamiento estático.
# Se sincroniza (no se borra la carpeta) para conservar el .git del worktree de destino.
site:
	@test -n "$(SITE)" && test "$(SITE)" != "/" && test "$(SITE)" != "." || { echo "ZARZUELO_SITE no es válida: '$(SITE)'"; exit 1; }
	$(KB) export
	$(KB) check
	cd web && npm install --no-audit --no-fund && npm run build
	mkdir -p "$(SITE)"
	rsync -a --delete --exclude=/.git --exclude=/CNAME web/dist/ "$(SITE)/"
	cp docs/despliegue.md "$(SITE)/LEEME.md" && touch "$(SITE)/.nojekyll"
	du -sh "$(SITE)"
