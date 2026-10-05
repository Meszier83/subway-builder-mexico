# Diccionario de Parametros YAML y Guia de Diseno de Ciudades

Esta guia muestra propiedades frecuentes de `cities/<ciudad>.yaml`, no un esquema
exhaustivo ni una prueba de compatibilidad del juego. Confirmar consumo y defaults
en `sb_mexico/pipeline.py`, `sb_mexico/gravity.py` y las herramientas afectadas.
Los valores de ejemplo no son una calibracion oficial para otra ciudad.

`city.code`, `city.name`, `city.description` y `city.bbox` son necesarios para las
operaciones revisadas del pipeline. Los campos numericos deben ser finitos y las
coordenadas usar `[lon, lat]` WGS84. `data_dir` es una opcion de nivel raiz;
respetar tambien `--data-dir` y `--output-dir` cuando el usuario los proporciona.
El validador auxiliar verifica un subconjunto estatico; no comprueba fuentes,
topologia completa de poligonos ni resultados del build.

---

## 1. Seccion `city` (Identidad y Geometria Espacial)

```yaml
city:
  code: "CUR"                         # Clave IATA o sigla de 3 letras en mayusculas (ej. CUR, CDMX, MID, SAL)
  name: "Cancun / Riviera Maya"       # Nombre oficial completo de la metropoli
  description: "Zona Metropolitana..."# Descripcion breve para el menu del juego (< 80 caracteres)
  bbox: [-87.6338, 20.1311, -85.6659, 21.8564] # [min_lon, min_lat, max_lon, max_lat] en WGS84
  bbox_locked: false                  # true para proteger la BBOX contra arrastres accidentales en el Wizard
  creator: "TuNombre"                 # Nombre o alias del creador del mapa
  grid_size: 0.0018                   # Resolucion de celda: 0.0018 (~180m, fino), 0.0025 (~250m, estandar)
  min_residents: 10                   # Umbral minimo de habitantes para consolidar un nodo residencial
  min_jobs: 3                         # Umbral minimo de empleos para consolidar un nodo laboral
  initial_zoom: 11.5                  # Nivel de zoom de inicio al abrir la partida (11.0 a 13.0)
  initial_center: [-86.8512, 21.1645] # [lon, lat] del encuadre inicial Dia 1 (calibrado en Wizard 16:9)
  building_filter_size: 15.0          # Filtro de area para edificios 3D en m2 (10-20; 25-35 en megaciudades)
  building_simplification: 0.2        # Tolerancia de simplificacion geometrica de huellas
  include_ocean: true                 # true para urbes costeras que requieren batimetria marina; false en valles
  urban_parks_only: true              # true para suprimir selvas y reservas rurales fuera de parques civicos
  seed: 42                            # Semilla pseudoaleatoria determinista para reproducibilidad matematica
  urban_core_polygon:                 # (Opcional) Poligono del nucleo urbano denso (AOI LOD concentrico)
    - [-86.88, 21.18]
    - [-86.80, 21.18]
    - [-86.80, 21.10]
    - [-86.88, 21.10]
    - [-86.88, 21.18]
  restrict_demand_to_urban_core: true # true: excluye poblacion/empleos rurales fuera del contorno urbano denso
```

---

## 2. Seccion `macroeconomics` (Calibracion Censal y Gravitatoria)

```yaml
macroeconomics:
  tasa_pea: 0.665                     # Tasa de participacion laboral sobre pob 15+ (ENOE estatal)
  til_1_state: 0.450                  # Tasa de informalidad laboral estatal (ENOE)
  sample_threshold: 500               # Muestra formal minima para calibrar asimetricamente un municipio
  default_growth_factor: 1.05         # Factor de proyeccion base si no hay dato CONAPO (1.00 = Censo 2020 puro)
  gravity_beta: 0.12                  # Coeficiente de friccion espacial por distancia (0.10 a 0.14)
  max_distance_km: 55.0               # Radio maximo en km para viajes metropolitanos regulares
  min_pop_size: 25                    # Preferencia de consolidacion; pueden quedar restos menores
  target_pop_size: 180                # Objetivo local; no garantiza FPS
  max_pop_size: 200                   # Techo configurado del proyecto; verificar version del juego
  furness_iterations: 15              # Ciclos maximos de balanceo bidireccional IPFP
  furness_tol: 0.02                   # Parametro local; medir residuos finales antes de afirmar balance

  # Ejemplos de factores por clave municipal (EEMMM); documentar fuente y ano:
  growth_factors:
    "23005": 1.07                     # Benito Juarez (Cancun): +7%
    "23008": 1.15                     # Solidaridad (Playa del Carmen): +15%
    "23003": 1.05                     # Isla Mujeres: +5%

  # Modulo experimental opcional (desactivado por defecto en el proyecto):
  modal_experiment:
    enabled: false
    preset: "canonical"               # canonical (40 km/h, auto 1.0), moderate_traffic, cdmx_peak
```

---

## 3. Seccion `routing` (Ruteo Vial OSRM)

```yaml
routing:
  include_driving_path: false         # false por defecto; medir bytes/memoria cuando se habilite
                                      # true: incluye traza vectorial GeoJSON para SIG o visores externos
```

---

## 4. Seccion `pois` (Generadores Especiales de Demanda - Reglas 1 y 3)

### Convencion de IDs del repositorio
- Usar prefijos como `AIR_`, `UNI_`, `SPO_`, `MED_`, `TOU_` y `TRA_` para POIs
  nuevos. `special_demand.py` interpreta prefijos y aliases de tipos.
- Usar nombres legibles con espacios en el cuerpo. Preservar IDs existentes:
  cambiarlos puede romper referencias de demanda o metadatos.
- La adicion de "Terminal" y el dampening universitario 0.3 son afirmaciones
  historicas de reglas locales; verificar version y codigo del juego antes de
  depender de ese comportamiento. Ver [game_engine_specs.md](game_engine_specs.md).

### Regla 3: Nodos Masivos vs Corredores
- Nodos puntuales masivos pueden usar POIs dedicados. `radius_m: 1500-2500` y
  `mode: MAX` son ejemplos de configuracion, no limites del formato; ajustar al
  territorio y empleo absorbido. `MAX`, `BOOST` y `REPLACE` tienen semanticas
  distintas que deben comprobarse en `build_demand_grid`.
- Corredores lineales (Zonas Hoteleras, avenidas financieras) nunca deben concentrarse en un solo mega-POI; dejar que el DENUE distribuya el empleo orgánicamente o usar zonas de afluencia.

```yaml
pois:
  - id: "AIR_Cancun"
    name:
      es: "Aeropuerto Internacional de Cancun"
      en: "Cancun International Airport"
    type: "airport"
    sub_type: "international_terminal"
    loc: [-86.874, 21.036]
    jobs: 32000
    radius_m: 2500
    mode: "MAX"                       # MAX (recomendado), BOOST (suma exogena) o REPLACE

  - id: "UNI_Universidad del Caribe"
    name: "Universidad del Caribe"
    type: "university"
    sub_type: "campus"
    loc: [-86.824, 21.201]
    jobs: 8500
    radius_m: 1200
    mode: "MAX"
```

---

## 5. Seccion `affluence_zones` (Zonas de Alta Afluencia)

```yaml
affluence_zones:
  - id: "zone_reforma_centro"
    name: "Corredor Reforma / Centro Financiero"
    type: "polygon"
    archetype: "cbd"                  # cbd (2.5x, bono 0.40), tourism (2.2x), industrial, commercial
    multiplier: 2.5
    reach_bonus: 0.40                 # Bono de alcance ([0.0, 0.60]) que aplana beta
    target_mode: "MULTIPLIER"
    color: "#8B5CF6"
    enabled: true
    coordinates:
      - [-99.172, 19.428]
      - [-99.155, 19.432]
      - [-99.148, 19.425]
      - [-99.165, 19.421]
      - [-99.172, 19.428]
```

---

## 6. Seccion `exclusion_zones` (Zonas de Exclusion - Regla 14)

```yaml
exclusion_zones:
  - id: "excl_laguna_nichupte"
    name: "Laguna Nichupte y Manglares"
    type: "polygon"
    reason: "water_body"             # water_body, ecological_reserve, unpopulated_island, military_restricted
    color: "#EF4444"
    enabled: true
    coordinates:
      - [-86.850, 21.140]
      - [-86.830, 21.140]
      - [-86.830, 21.120]
      - [-86.850, 21.120]
      - [-86.850, 21.140]
```

---

## 7. Seccion `isolated_zones` (Zonas Aisladas Estancas)

```yaml
isolated_zones:
  - id: "isla_mujeres"
    name: "Isla Mujeres"
    bbox: [-86.76, 21.20, -86.68, 21.28]
```

---

## 8. Seccion `places` (Toponimia Urbana Adicional)

```yaml
places:
  - name: "Centro Historico"
    loc: [-86.825, 21.161]
    type: "neighbourhood"             # suburb, neighbourhood, quarter
```
