# Ruteo OSRM y operacion WSL

Esta referencia describe la implementacion local en `sb_mexico/osrm.py`.
Las constantes del proyecto no prueban paridad con todas las versiones del juego.

## Tiempo y geometria

`enrich_pops_with_osrm` obtiene segundos y metros del perfil vehicular OSRM.
`car.lua` usa velocidades segun la red y el perfil; 40 km/h es la constante
del fallback local, no una velocidad uniforme impuesta a cada ruta OSRM.
Mantener el modo base sin congestion artificial. Cambiar la competitividad modal
solo dentro del experimento solicitado y registrar las opciones efectivas.
Comprobar en el motor objetivo cualquier multiplicador de congestion, hora pico,
estacionamiento o costo antes de afirmar que se aplica dos veces.

Con `include_driving_path: false`, las consultas usan `overview=false`.
Cuando se pide geometria, usan `overview=full&geometries=geojson`.
Medir bytes y memoria; no prometer un tamano fijo ni un porcentaje de aceleracion.

## Servicio en WSL

En Windows, inspeccionar `prepare_osrm_network_wsl`,
`start_osrm_daemon_wsl` y `stop_osrm_daemon_wsl`.
El grafo se prepara en `~/osrm_<codigo>/`, con PBF recortado y archivos MLD.
El servicio se supervisa con un proceso persistente. No atribuir todo SIGTERM
a un tiempo universal de suspension: comprobar logs y estado del servicio.

`enrich_pops_with_osrm` configura `Retry(total=2, backoff_factor=0.05)` y abandona
las peticiones tras cinco errores consecutivos de conexion. Son decisiones
locales de resiliencia. Confirmar puerto, respuesta OSRM y conectividad antes de
diagnosticar keep-alive; no asumir que toda instalacion cierra a las 512 peticiones.
Usar el mecanismo de escalacion disponible ante un E_ACCESSDENIED de sandbox,
como explica [SKILL.md](../SKILL.md); no apagar WSL de forma rutinaria.

## Cache y cobertura

`compute_osrm_fingerprint` calcula SHA-256 sobre codigo normalizado, BBOX
redondeado a cinco decimales, tamano y mtime entero del PBF, y la etiqueta literal
`car.lua:v1`. La marca esta en `.osrm_fingerprint`.
**No es un hash del contenido del PBF ni del perfil real.** No afirmar que detecta
toda modificacion de estos archivos. Si una tarea exige identidad por contenido,
informar esta limitacion y proponer la correccion dentro de su alcance.

El umbral local de snapping es `MAX_WAYPOINT_SNAPPING_METERS = 1500`.
Una ruta rechazada puede usar fallback; ello no demuestra conectividad vial real.
Separar fallback por servicio caido, ruta ausente y snapping excesivo en el informe.

## Fallback y verificacion final

Inspeccionar la funcion invocada: hay dos variantes locales para distancias pequenas.

- `osrm.calculate_canonical_driving_fallback`:
  `road_m = round(max(150, euclid_m) * 1.3)`.
- `gravity.calculate_commute_impedance`:
  `road_m = max(150, round(haversine_km * 1000 * 1.3))`.
- Ambas usan `seconds = max(45, round(road_m / (40 / 3.6)))`.

El nombre "canonical" es una etiqueta local; verificar la fuente/version del juego
antes de presentarlo como formula oficial. El fallback aproxima impedancia, no
construye una ruta navegable.

Antes de exportar, `pipeline.validate_cohort_spatial_integrity` comprueba los
pares sobre coordenadas finales. Leer sus limites vigentes y sus tests; medir
rutas OSRM/fallback, distancias, velocidades y pares entre zonas aisladas.
Tras mover o consolidar puntos, revisar que sus impedancias sigan correspondiendo
a las coordenadas finales. No corregir fallos espaciales cambiando pares OD duros.
