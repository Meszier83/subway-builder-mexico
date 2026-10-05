# Formatos de entrega y evidencia de compatibilidad

Esta guia documenta las salidas del pipeline local. No es una especificacion
verificada de todas las versiones de Subway Builder o sus mods. Para afirmar
comportamiento del motor, registrar version objetivo, codigo o fuente oficial
inspeccionados, revision y resultado observado. Las ponderaciones historicas,
la ventana rRAPTOR de 30 minutos y la eleccion modal Logit sensible al ingreso no
estan establecidos por los esquemas de este repositorio; no usarlos como contratos
sin evidencia para la version objetivo.

## Fuentes locales

Las rutas siguientes son relativas a la raiz del repositorio:

| Tema | Fuente a inspeccionar |
| --- | --- |
| Exportacion y ZIP | `sb_mexico/pipeline.py:execute_pipeline` |
| Campos de puntos | `sb_mexico/gravity.py:sanitize_demand_points` |
| Campos y referencias de cohortes | `sb_mexico/gravity.py:sync_demand_points_and_pops` |
| Metadatos POI | `sb_mexico/special_demand.py`, `sb_mexico/schemas/special_demand_points.schema.json` |
| Tipos POI | `sb_mexico/schemas/special_demand_types.json` |

La version `7.1.0` escrita en `config.json` identifica el pipeline; no prueba
compatibilidad con una version del juego. Un comentario "canonico" en el codigo
local no sustituye una fuente del motor objetivo.

## Contenido del paquete

El pipeline exige `config.json`, `demand_data.json`, `<CODIGO>.pmtiles` y
`roads.geojson` antes de crear el ZIP. Agrega los siguientes archivos cuando
existen: `buildings_index.bin.gz`, `runways_taxiways.geojson`,
`ocean_depth_index.json.gz` y `special_demand_points.json`.
Su necesidad en el juego depende de la version y de las funciones del mapa.
Si falta cartografia, el pipeline puede terminar con solo demanda:
un codigo de salida exitoso no garantiza un ZIP nuevo.

`config.json` contiene, entre otros, `name`, `code`, `description`, `population`
e `initialViewState`. Comparar sus valores con la configuracion efectiva,
considerando el truncado de descripcion y el calculo de camara.

`demand_data.json` contiene `points` y `pops`:

- Puntos: `id`, `location: [lon, lat]`, `jobs`, `residents`, `popIds`.
- Cohortes: `id`, `size`, `residenceId`, `jobId`, `drivingSeconds`,
  `drivingDistance`; `drivingPath` es opcional segun las opciones efectivas.
- `size` es un entero positivo. La convencion local es `max_pop_size: 200`;
  `min_pop_size: 25` es un parametro de consolidacion, no evidencia de que el
  juego prohiba restos de 1 a 24 personas.
- La sincronizacion local recalcula totales de display desde las cohortes,
  con un caso especial para POIs sin flujo. Esos campos sincronizados no prueban
  que las marginales originales se conservaron.

## Verificacion de entrega

1. Antes del build, identificar el archivo existente y sus huellas, si existe.
   Despues, confirmar procedencia mediante la salida de esta ejecucion, huellas
   de contenido y configuracion efectiva; la fecha sola no basta. Salidas
   identicas pueden conservar legitimamente el mismo hash.
2. Inspeccionar ZIP con `zipfile.ZipFile`: integridad CRC, nombres sin duplicados,
   archivos requeridos en la raiz, contenido no vacio y JSON parseable. Comparar
   bytes o SHA-256 de miembros con los artefactos de esta ejecucion. PMTiles
   reutilizados deben corresponder al territorio y las opciones actuales.
3. Verificar IDs unicos, coordenadas finitas WGS84, referencias de cada cohorte,
   `popIds` coherentes, tamanos enteros positivos, suma de tamanos y restricciones
   espaciales. Si se requieren marginales OD duras, compararlas con vectores
   autorizados antes de sincronizacion y comprobar pares prohibidos.
4. Para POIs, ejecutar validacion del esquema local y sus relaciones.
   Distinguir advertencias del pipeline de validaciones bloqueantes.
5. Importar y cargar el paquete en el juego/mod objetivo, observar mapa y demanda,
   guardar y recargar. Sin acceso al juego, anunciar "verificacion estatica;
   carga en juego no verificada" e indicar los controles ya ejecutados.

## Tamano y rendimiento

Mantener `include_driving_path: false` por defecto. Las rutas detalladas aumentan
datos y memoria de parsing. Ni 15 000 cohortes ni 512 MB son un umbral universal
establecido aqui: longitud de rutas, codificacion, V8/Electron, arquitectura y
cargador influyen en el resultado.

Para investigar, medir bytes del JSON descomprimido, cantidad de coordenadas de
ruta, tiempo de carga y memoria en el runtime objetivo. Un `ERR_STRING_TOO_LONG`
requiere el error observado y la version del runtime. Medir tambien tiempos de
simulacion y FPS sobre una configuracion reproducible; ninguna formula de tamano
de cohorte garantiza 60 FPS.
