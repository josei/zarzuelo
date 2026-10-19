# Zarzuelo

Base de conocimiento trazable sobre zarzuela y música escénica española (plan completo en `plan.md`, convenciones en `AGENTS.md`).

## Uso

```bash
make setup          # venv + dependencias del pipeline
make test           # pruebas (backend LLM simulado, datos ficticios)
.venv/bin/kb init   # crea data/kb.sqlite y carga el vocabulario semilla
.venv/bin/kb phase0 # comprueba las fuentes validadas
.venv/bin/kb discover --limit 50 && .venv/bin/kb vocab build && .venv/bin/kb capture
.venv/bin/kb estimate        # llamadas LLM pendientes
.venv/bin/kb extract         # extracción con el modelo local (llama-server, config/llm.yaml)
.venv/bin/kb resolve && .venv/bin/kb validate
.venv/bin/kb generate        # cuestionario (sin LLM)
.venv/bin/kb summarize       # resúmenes narrativos por ficha con Qwen local, validados
.venv/bin/kb judge-summaries # revisión de los resúmenes con Haiku (respeta el tope diario)
.venv/bin/kb export && .venv/bin/kb check
make web            # npm install + build en web/dist
.venv/bin/kb refresh --dry-run   # qué ha cambiado en las fuentes
```

El `User-Agent` hacia Wikimedia se configura con `KB_USER_AGENT` (debe incluir un contacto).
