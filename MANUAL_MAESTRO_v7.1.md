# MANUAL MAESTRO: SUBWAY BUILDER MEXICO (v7.1)
**Arquitectura de Compilacion, Modelado Geoespacial y Calibracion Urbana (INEGI & Subway Builder)**

---

## 1. INTRODUCCION Y FILOSOFIA v7.1

La version 7.1 consolida una arquitectura declarativa, rigurosa y automatizada de nivel investigativo, combinando microsimulacion censal, fisica vial estandarizada y la suite visual interactiva Wizard Studio (estetica Metro CDMX / Lance Wyman):

1. **Un solo comando o Wizard Web Integral:** La compilacion cartografica 3D, el cruce censal por manzana, el modelo gravitatorio en dos capas, la toponimia y el empaquetado se ejecutan de inicio a fin desde CLI o interfaz web interactiva:
   ```bash
   python build.py cities/cancun.yaml
   # O mediante el Wizard Visual (con POI Studio integrado en el Paso 4):
   python tools/wizard.py
   ```
2. **Modelo Gravitatorio en Dos Capas (Two-Tier Doubly-Constrained):**
   * **Capa Especial:** Generadores metropolitanos (Aeropuertos `AIR_`, Universidades `UNI_`, Estadios `SPO_`, Hospitales `MED_`) reciben el **100% exacto de su cuota** con absorcion local DENUE y deduccion estricta del presupuesto residencial ($\text{PEA}_i^{\text{rem}}$).
   * **Capa Regular (Furness / IPFP):** Balanceo iterativo proporcional bidireccional que satisface simultaneamente la masa activa de los hogares y la capacidad de atraccion de los puestos de trabajo.
3. **Ruteo Vial Canonico con OSRM y Puente WSL 2:** Estimacion de tiempos reales de manejo y distancias de pavimento mediante OSRM (`car.lua`) en WSL 2, inyectando la geometria vectorial de recorrido (`drivingPath`) en las cohortes `pops`, con supervisor persistente contra suspensiones de WSL 2 y fallback canonico de Colin.
4. **Zonas de Alta Afluencia (`affluence_zones`):** Delimitacion poligonal de distritos clave (CBD, turismo, industrial, comercial) con modulacion de masa laboral, bono de alcance metropolitano (`reach_bonus`) y regla canonica *MAX Priority*.
5. **Zonas Topologicas Aisladas (`isolated_zones`):** Modelado estanco de islas y barreras hidricas que impide la circulacion irreal de automoviles sobre el mar.
6. **Aislamiento Hermetico por Proyecto (Regla 9):** Almacenamiento hermetico de microdatos en `data/<ciudad>/` y generacion de paquetes ZIP en `dist/<ciudad>/<CODIGO>.zip`.

---

## 2. ESTRUCTURA DEL PROYECTO

```text
subway-builder-mexico/
|-- cities/                          # Archivos de configuracion de cada metropoli (.yaml)
|   |-- _template.yaml               # Plantilla maestra documentada v7.1
|   |-- cancun.yaml                  # Configuracion de Cancun / Quintana Roo
|   \-- merida.yaml                  # Configuracion de Merida / Yucatan
|-- data/                            # Datasets y fuentes de datos
|   |-- mexico-latest.osm.pbf        # Extracto OSM nacional PBF (Geofabrik)
|   |-- proyecciones_conapo.csv      # Proyecciones demograficas municipales CONAPO
|   |-- cancun/                      # Microdatos especificos de Cancun (CPV, DENUE, CE, ENOE)
|   \-- merida/                      # Microdatos especificos de Merida
|-- dist/                            # Salidas compiladas y paquetes ZIP importables
|   |-- cancun/                      # CUN.zip, demand_data.json, CUN.pmtiles, roads.geojson
|   \-- merida/                      # MID.zip, demand_data.json, MID.pmtiles, roads.geojson
|-- sb_mexico/                       # Nucleo del motor de procesamiento
|   |-- inegi.py                     # Ingesta, geocodificacion jerarquica, multi-archivo y CONAPO
|   |-- gravity.py                   # Furness IPFP, dos capas, affluence zones, snapping y modal lab
|   |-- osrm.py                      # Microservicio OSRM (car.lua), supervisor WSL 2 y fallback Colin
|   |-- special_demand.py            # Generacion y validacion de demanda especial (Taxonomia v5)
|   |-- toponymy.py                  # Extractor toponimico para OSM XML/PBF
|   |-- cartography.py               # Generador cartografico con depot.maps.MapGen
|   |-- cartography_runner.py        # Compilacion cartografica nativa en Linux / WSL 2
|   |-- pipeline.py                  # Orquestador del flujo completo y autovalidaciones
|   \-- schemas/                     # Esquemas JSON oficiales de validacion de demanda especial
|-- tests/                           # Suite formal de pruebas unitarias (114 tests)
|-- tools/                           # Herramientas de visualizacion y diseno
|   |-- wizard.py                    # Servidor web del asistente integral (con POI Studio en Paso 4)
|   |-- poi_studio.py                # Editor visual standalone de POIs en mapa satelital
|   |-- preview_toponymy.py          # Visor geoespacial de capas toponimicas
|   \-- demo_preview.py              # Generador rapido de vistas previas
|-- build.py                         # CLI ejecutable principal
|-- wizard.bat                       # Lanzador directo para Windows
|-- DATA_SOURCES.md                  # Enlaces y pasos para descarga de datos oficiales
|-- METHODOLOGY.md                   # Libro blanco y justificacion matematica v7.1
|-- MANUAL_MAESTRO_v7.1.md           # Este manual de usuario
\-- requirements.txt                 # Dependencias del entorno Python
```

---

## 3. FUENTES DE DATOS DEL INEGI Y CONAPO

El motor procesa automaticamente los archivos oficiales colocados en `data/<ciudad>/` (para datos locales) y `data/` (para datos nacionales):

1. **CPV 2020 (`*RESAGEBURB*.csv`, `.xlsx` o subcarpeta `conjunto_de_datos/`):**  
   Poblacion total (`POBTOT`) y poblacion de 15 anos y mas (`P_15YMAS`) a nivel de manzana urbana (`MZA > 0`). Resuelve el secreto estadistico (`*`) y aplica la jerarquia cuadruple de georreferenciacion censal.
2. **DENUE (`*denue*.csv`):**  
   Directorio georreferenciado de unidades economicas (coordenadas GPS puntuales y estratos de personal ocupado).
3. **Censos Economicos 2024 (`*SAIC*.csv` o `*tr_ce*.csv`):**  
   Cifra de control municipal de personal ocupado total (`H001A`) para calibrar asimetricamente los micronegocios informales.
4. **ENOE (`*_Entidad_*.csv` o `.xlsx`):**  
   Tasa de participacion laboral (Tasa PEA) y tasa de informalidad laboral (`TIL_1`).
5. **Proyecciones CONAPO (`*conapo*.csv` o `data-*.csv`):**  
   Proyecciones de poblacion municipal a mitad de ano (`POB_MIT_MUN`), desglosadas y auditadas transparentemente en el Paso 3 del Wizard.
6. **OpenStreetMap (`mexico-latest.osm.pbf`):**  
   Red vial peatonal y vehicular, edificios 3D, costas y toponimia urbana descargado de Geofabrik.
7. **Conurbaciones Interestatales Multi-Archivo (Regla 7):**  
   Para metropolis multi-estado (ej. ZMVM, La Laguna, Puebla-Tlaxcala), deposita los archivos de todas las entidades en `data/<ciudad>/`; el motor los concatena y recorta estrictamente por BBOX.

---

## 4. FLUJO DE TRABAJO: COMO GENERAR UNA METROPOLI

### Metodo A: Mediante Wizard Studio (Recomendado)
1. Ejecuta `python tools/wizard.py` o haz doble clic en `wizard.bat`.
2. **Paso 1 (Proyectos):** Crea un nuevo proyecto o selecciona uno existente (ej. Cancun, Merida).
3. **Paso 2 (Datos):** Sube o verifica los archivos censales en la carpeta activa `data/<ciudad>/`.
4. **Paso 3 (Delimitacion & CONAPO):** Ajusta el BBOX metropolitano con los 4 tiradores de esquina interactivos y audita los factores de proyeccion municipal calculados.
5. **Paso 4 (POI Studio & Zonas de Afluencia):** Ubica en mapa satelital tus nodos clave (`AIR_`, `UNI_`, `SPO_`), calibra sus radios de absorcion con vista en tiempo real y traza Zonas de Alta Afluencia poligonales.
6. **Paso 5 (Toponimia):** Revisa y selecciona colonias y vialidades prioritarias.
7. **Paso 6 (Compilacion):** Inicia la compilacion con telemetria en vivo por streaming SSE.
8. **Paso 7 (Visor):** Inspecciona el mapa interactivo final antes de jugar.

### Metodo B: Mediante CLI Directo
Guarda la configuracion en `cities/<ciudad>.yaml` y ejecuta:
```bash
# Compilacion completa (Cartografia 3D + Demanda + Empaquetado ZIP)
python build.py cities/<ciudad>.yaml

# O si solo modificaste POIs o parametros macroeconomicos (demanda rapida):
python build.py cities/<ciudad>.yaml --skip-map
```

El archivo final queda listo en `dist/<ciudad>/<CODIGO>.zip`.

### Importar en el Juego
1. Abre **Kronifer's Map Manager / Railyard** en Subway Builder.
2. Selecciona **ADD A MAP** y carga el archivo ZIP generado en `dist/<ciudad>/`.
3. ¡Comienza a trazar tu red metropolitana de transporte!

---

## 5. DICCIONARIO COMPLETO DE PARAMETROS YAML

A continuacion se detallan todas las propiedades reconocidas por el motor en `cities/<ciudad>.yaml`:

### Seccion `city` (Identidad y Geometria Urbana)
| Parametro | Tipo | Descripcion / Valor Recomendado |
| :--- | :--- | :--- |
| `code` | string | Clave IATA o sigla unica de 3 letras en mayusculas (ej. `CUN`, `MID`, `GDL`). |
| `name` | string | Nombre oficial completo de la zona metropolitana. |
| `description`| string | Descripcion breve para el menu de seleccion del juego (< 80 caracteres). |
| `bbox` | list | `[min_lon, min_lat, max_lon, max_lat]` del rectangulo de estudio metropolitano. |
| `creator` | string | Nombre o alias del creador del mapa. |
| `grid_size` | float | Resolucion de celda de demanda: `0.0018` (~180m, fino), `0.0025` (~250m, estandar), `0.0035` (metropolis masiva). |
| `min_residents`| int | Umbral minimo de habitantes para consolidar un nodo de origen (default: `10`). |
| `min_jobs` | int | Umbral minimo de empleos para consolidar un nodo de destino laboral (default: `3`). |
| `initial_zoom` | float | Nivel de zoom de inicio al abrir la partida (ej. `11.5` a `12.5`). |
| `building_filter_size` | float | Filtro de area para edificios 3D en MapGen (default: `15.0` m² para 60 FPS estables). |
| `building_simplification` | float | Tolerancia de simplificacion geometrica de edificios (default: `0.2`). |
| `include_ocean` | bool | `true` para ciudades costeras que requieren batimetria marina; `false` para valles interiores. |
| `seed` | int | Semilla pseudoaleatoria determinista para reproducibilidad matematica exacta (ej. `42`). |

### Seccion `data_dir` y `output_dir` (Opcionales)
| Parametro | Tipo | Descripcion |
| :--- | :--- | :--- |
| `data_dir` | string | Ruta explicita a los microdatos locales (default: `data/<city_slug>/`). |
| `output_dir` | string | Ruta de salida de artefactos compilados (default: `dist/<city_slug>/`). |

### Seccion `macroeconomics` (Calibracion Censal y Gravitatoria)
| Parametro | Tipo | Descripcion / Valor Recomendado |
| :--- | :--- | :--- |
| `tasa_pea` | float | Tasa de participacion laboral sobre poblacion 15+ de la ENOE (ej. `0.665`). |
| `til_1_state` | float | Tasa de informalidad laboral estatal de la ENOE (ej. `0.450`). |
| `sample_threshold` | int | Muestra minima de personal ocupado formal para calibrar un municipio (default: `500`). |
| `default_growth_factor` | float | Factor de proyeccion demografica base si no hay CONAPO (default: `1.05`; `1.00` = Censo 2020 puro). |
| `gravity_beta` | float | Coeficiente de decaimiento por distancia regular (default: `0.12`). |
| `max_distance_km` | float | Distancia maxima para considerar viajes urbanos cotidianos (default: `55.0` km). |
| `max_pop_size` | int | Tamano maximo permitido para una cohorte individual `pop` (default nativo: `200`). |
| `target_pop_size` | int | Tamano objetivo de cohortes para estabilizar la simulacion a 60 FPS (default: `180`). |
| `furness_iterations` | int | Numero maximo de ciclos de balanceo bidireccional IPFP (default: `15`). |
| `furness_tol` | float | Criterio de convergencia en distribucion de marginales (default: `0.02` = 2%). |
| `growth_factors` | dict | Diccionario de factores oficiales de proyeccion CONAPO por clave municipal `EEMMM` (ej. `"23005": 1.07`). |

### Sub-seccion `modal_experiment` (Laboratorio Opcional de Congestion)
| Parametro | Tipo | Descripcion / Opciones |
| :--- | :--- | :--- |
| `enabled` | bool | `true` para activar el laboratorio; `false` (default) para respetar el flujo libre canónico (40 km/h). |
| `preset` | string | `canonical`, `moderate_traffic` (28 km/h), `cdmx_peak` (18 km/h), `captive_transit` (24 km/h), `custom`. |
| `traffic_speed_kmh` | float | Velocidad promedio de auto en km/h (`10.0` a `60.0`). |
| `motorization_rate` | float | Tasa de hogares con automovil (`0.05` a `1.00`). |

### Seccion `affluence_zones` (Zonas de Alta Afluencia)
| Parametro | Tipo | Descripcion / Opciones |
| :--- | :--- | :--- |
| `id` | string | Identificador unico de la zona (ej. `zone_distrito_financiero`). |
| `name` | string | Nombre descriptivo del distrito o corredor economico. |
| `type` | string | Tipo de geometria: `polygon` o `bbox`. |
| `archetype` | string | Arquetipo: `cbd` (2.5x, reach 0.40), `tourism` (2.2x, reach 0.35), `industrial` (1.8x, reach 0.25), `commercial` (1.4x, reach 0.15), `custom`. |
| `multiplier` | float | Multiplicador de atraccion de empleo regular DENUE ($\ge 1.0$). |
| `reach_bonus` | float | Bono de alcance metropolitano ($[0.0, 0.60]$) que modula $\beta_j$. |
| `target_mode` | string | `MULTIPLIER` (multiplicacion directa) o `TARGET_CAPACITY` (reescalado a cupo fijo). |
| `target_jobs` | int | Cupo total de empleos cuando `target_mode: TARGET_CAPACITY`. |
| `coordinates` | list | Lista de pares `[lon, lat]` que delimitan el poligono cerrado. |
| `enabled` | bool | Permite activar o desactivar la zona rapidamente (default: `true`). |

### Seccion `isolated_zones` (Zonas Topologicas Aisladas)
| Parametro | Tipo | Descripcion |
| :--- | :--- | :--- |
| `id` | string | Identificador unico de la isla o sub-zona aislada (ej. `isla_mujeres`). |
| `name` | string | Nombre oficial de la zona geografica aislada. |
| `bbox` | list | `[min_lon, min_lat, max_lon, max_lat]` del cuadrilatero aislado. |

### Seccion `pois` (Generadores Especiales de Demanda)
| Parametro | Tipo | Descripcion / Reglas Oficiales |
| :--- | :--- | :--- |
| `id` | string | Nombre del nodo. **Prefijos obligatorios:** `AIR_*` (Aeropuertos), `UNI_*` (Universidades), `SPO_*` (Estadios), `TOU_*` (Turismo), `MED_*` (Hospitales), `TRA_*` (Terminales). Sin guiones bajos en el resto del nombre. |
| `name` | dict/str | Nombre visible en UI. Puede ser texto o bilingue `{es: "...", en: "..."}`. |
| `type` | string | Tipo taxonomico oficial (ej. `airport`, `university`, `sports_facility`, `hospital`). |
| `sub_type` | string | Subtipo especifico (ej. `international_terminal`, `stadium`, `campus`). |
| `loc` | list | `[longitud, latitud]` exacta del nodo. |
| `jobs` | int | Cuota exacta de pasajeros/empleos objetivo del polo. |
| `radius_m` | int | Radio de absorcion en metros (`1500–2500m` aeropuertos, `800–1200m` universidades, `500–800m` anclas). |
| `mode` | string | `MAX` (recomendado: absorbe DENUE local y fija piso de cuota), `BOOST` (suma exogena pura), `REPLACE` (sobrescribe DENUE). |
| `metadata` | dict | Metadatos documentales libres (ej. `source: "AFAC 2026"`). |

---

## 6. FORMULACION MATEMATICA DE REFERENCIA

### 1. Modelo en Dos Capas y Deduccion de Presupuesto
* **Capa 1 (Hubs Especiales):** Asignacion de alcance metropolitano ($\beta_{\text{esp}} = 0.04$):
  $$W_{ik} = \text{PEA}_i \cdot e^{-\beta_{\text{esp}} \cdot d_{ik}}$$
  $$\vec{T}_{\cdot \to k} \sim \operatorname{Multinomial}\left(K_k, \ \vec{P}_k\right)$$
  Deduccion estricta para evitar doble viaje: $\text{PEA}_i^{\text{rem}} = \text{PEA}_i - \sum_k T_{i \to k}$.
* **Capa 2 (Furness / IPFP Doblemente Acotado):**
  Balanceo iterativo que resuelve la matriz $T_{ij}$ tal que $\sum_j T_{ij} = \text{PEA}_i^{\text{rem}}$ y $\sum_i T_{ij} \propto E_j$, con friccion modulada por zonas de afluencia $\beta_j = \beta (1 - \text{reach\_bonus}_j)$.

### 2. Ruteo Vial OSRM vs. Fallback Canonico de Colin
* **OSRM en WSL 2:** Distancia real de calle, duracion en segundos a flujo libre (~40 km/h) y vector GeoJSON `drivingPath`.
* **Fallback Canónico de Colin (sin Docker o en vias insulares):**
  $$\text{drivingDistance} = \max(150\text{ m}, \ \operatorname{round}(d_{\text{euclid}} \times 1.3))$$
  $$\text{drivingSeconds} = \max\left(45\text{ s}, \ \operatorname{round}\left(\frac{\text{drivingDistance}}{40.0 / 3.6}\right)\right)$$

### 3. Teorema de Conservacion Estricta de Masa
$$\sum_{p \in \text{pops}} \text{size}(p) \equiv \sum_{i \in \text{celdas}} \text{PEA}_i \qquad (\Delta = 0\text{ personas})$$
Garantiza fidelidad cientifica absoluta y compatibilidad nativa con Subway Builder.
