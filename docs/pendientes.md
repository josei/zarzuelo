# Pendientes que no puedo cerrar yo (actualizado 2026-10-15)

## Necesitan una decisión o una acción tuya
1. **Oro** (`gold/extract_claims.draft.yaml`): 40 pasajes reales de Wikipedia con los hechos que propuso Qwen. Marca `ok: true/false` y añade en `faltan` lo que no se extrajo. Con 20 revisados ya sirve. Después: `kb eval --engine local` y `kb eval --engine claude`. No puedo rellenarlo yo: debe ser verificado a mano.
2. **Correos de permiso** (`docs/permisos/`, sin enviar): ICCMU (Diccionario de la Zarzuela), Fundación Juan March, CDAEM. Enviarlos es acción tuya.
3. **BNE (descargas)**: aparcada. Solo si quieres retomarla: el paquete de autoridades/partituras/grabaciones solo da metadatos y enlaces, sin texto. Ver `docs/descargas-manuales.md`.
4. **Hemeroteca Digital**: el OCR que probaste era ilegible y su `robots.txt` veta el rastreo. Queda como «solo enlazar».
5. **Discogs**: sin `DISCOGS_TOKEN` en `.env`. Solo si MusicBrainz se queda corto.

## Verificado y descartado (no hay nada que hacer ahora)
- **Planos históricos fuera de Madrid**: para Madrid el IGN sirve 11 planos (WMS, CC BY 4.0, verificado). Para Barcelona solo he encontrado mapas topográficos del Ajuntament de 1984–2015 (WMS, CC BY 4.0, fuera de época) y escaneos de la Cartoteca de Catalunya (ICGC) con derechos por fichero y **sin servicio WMS verificado**. Valencia y otras ciudades: sin fuente localizada. No se implementa nada sin verificar el servicio y su licencia.
- **Fuentes bloqueadas por robots.txt/Cloudflare** (no se esquivan): IAI (libretos), BVMC (SPARQL), DAHR, VIAF, Catalònica/ARCA.

## En marcha sin intervención
- **Revisión de resúmenes con Haiku**: heartbeat cada 6 h, tope de 40 llamadas/día (reservada la cuota para uso interactivo). Solo se publican los resúmenes aprobados.
