# MANUAL MAESTRO: SUBWAY BUILDER MEXICO (v7.1)
**Arquitectura de Compilacion, Modelado Geoespacial y Calibracion Urbana (INEGI & Subway Builder)**

---

## 1. INTRODUCCION Y FILOSOFIA v7.1

La version 7.1 consolida una arquitectura declarativa, rigurosa y automatizada de nivel investigativo, combinando microsimulacion censal, fisica vial estandarizada y la suite visual interactiva Wizard Studio (estetica Metro CDMX / Lance Wyman):

1. **Un solo comando o Wizard Web Integral:** La compilacion cartografica 3D, el cruce censal por manzana, el modelo gravitatorio en dos capas, la toponimia y el empaquetado se ejecutan de inicio a fin desde CLI o interfaz web interactiva:
   ```bash
   python build.py cities/cancun_riviera_maya.yaml
   # O mediante el Wizard Visual (con POI Studio integrado en el Paso 4):
   python tools/wizard.py
   ```
2. **Modelo Gravitatorio en Dos Capas (Two-Tier Doubly-Constrained):**
   * **Capa Especial:** Generadores metropolitanos (Aeropuertos `AIR_`, Universidades `UNI_`, Estadios `SPO_`, Hospitales `MED_`) reciben el **100% exacto de su cuota** con absorcion local DENUE y deduccion estricta del presupuesto residencial ($\text{PEA}_i^{\text{rem}}$).
   * **Capa Regular (Furness / IPFP):** Balanceo iterativo proporcional bidireccional que satisface simultaneamente la masa activa de los hogares y la capacidad de atraccion de los puestos de trabajo.
3. **Ruteo Vial Canonico con OSRM, Huella SHA-256 y Desacoplamiento V8 en WSL 2:** Estimacion de tiempos reales de manejo y distancias de pavimento mediante OSRM (`car.lua`) en WSL 2 a flujo libre (~40 km/h), con desacoplamiento de `drivingPath` (off por defecto para proteger contra el limite de 512 MB de V8 en Electron), huella criptografica de red, cortafuegos de snapping (1500m) y fallback canonico de Colin.
4. **Zonificacion Concentrica y Nucleo Urbano LOD (`urban_core_polygon`):** Delimitacion manual o censal automatica (Concave Hull) para acotar edificios 3D y calles secundarias al nucleo urbano denso, garantizando horizontes continuos y rendimiento fluido en WebGL.
5. **Filtrado de Macro-Parques Urbanos (`urban_parks_only`):** Supresion de selvas, reservas rurales y sierras no pobladas, preservando parques cívicos urbanos.
6. **Zonas de Exclusion (`exclusion_zones`):** Supresion estricta de demanda y viajes en cuerpos de agua, lagunas o reservas, preservando intacta la infraestructura vial y cartografia 3D.
7. **Zonas de Alta Afluencia (`affluence_zones`):** Delimitacion poligonal de distritos clave (CBD, turismo, industrial, comercial) con modulacion de masa laboral, bono de alcance metropolitano (`reach_bonus`) y regla canonica *MAX Priority*.
8. **Zonas Topologicas Aisladas (`isolated_zones`):** Modelado estanco de islas y barreras hidricas que impide la circulacion irreal de automoviles sobre el mar.
9. **Aislamiento Hermetico por Proyecto (Regla 9):** Almacenamiento hermetico de microdatos en `data/<ciudad>/` y generacion de paquetes ZIP en `dist/<ciudad>/<CODIGO>.zip`.
10. **Canon Oficial de Simulacion de Pasajeros (Colin Miller / Subway Builder):**
    * **Ruteo de Pasajeros rRAPTOR (Delling et al., 2012):** Busqueda por rondas sobre horarios de trenes en ventanas de 30 minutos; permite a los viajeros esperar por servicios exprés y realizar transbordos peatonales (*walking transfers*) entre estaciones cercanas.
    * **Ponderaciones de Tiempo Percibido (Wardman et al., 2026):** Evaluacion con factores de tiempo generalizado ($1.0\times$ tren, $1.39\times$ caminata, $1.37\times$ andén, $0.40\times$ demora en casa, $1.33\times$ trafico automotriz, $1.60\times$ estacionamiento).
    * **Eleccion Modal por Ingreso (Tao, Wu et al., 2020):** Competencia Metro vs. Auto vs. Caminata con Valor del Tiempo ($VOT$) heterogeneo segun el nivel de ingreso del vecindario, produciendo una curva suave y elastica de captacion de usuarios.

---

## 2. ESTRUCTURA DEL PROYECTO

```text
subway-builder-mexico/
|-- cities/                          # Archivos de configuracion de cada metropoli (.yaml)
|   |-- _template.yaml               # Plantilla maestra documentada v7.1
|   |-- cancun_riviera_maya.yaml     # Configuracion de Cancun / Riviera Maya (CUR)
|   |-- ciudad_de_mexico.yaml        # Configuracion de CDMX / Valle de Mexico (CDMX)
|   |-- merida.yaml                  # Configuracion de Merida / Yucatan (MID)
|   \-- saltillo.yaml                # Configuracion de Saltillo / Coahuila (SAL)
|-- data/                            # Datasets y fuentes de datos
|   |-- mexico-latest.osm.pbf        # Extracto OSM nacional PBF (Geofabrik)
|   |-- proyecciones_conapo_2020_2053.csv # Proyecciones demograficas municipales CONAPO
|   |-- cancun_riviera_maya/         # Microdatos especificos de Cancun / Riviera Maya
|   |-- ciudad_de_mexico/            # Microdatos especificos de CDMX y municipios metropolitanos
|   |-- merida/                      # Microdatos especificos de Merida
|   \-- saltillo/                    # Microdatos especificos de Saltillo
|-- dist/                            # Salidas compiladas y paquetes ZIP importables
|   |-- cancun_riviera_maya/         # CUR.zip, demand_data.json, CUR.pmtiles, roads.geojson
|   |-- ciudad_de_mexico/            # CDMX.zip, demand_data.json, CDMX.pmtiles, roads.geojson
|   \-- merida/                      # MID.zip, demand_data.json, MID.pmtiles, roads.geojson
|-- releases/                        # Manifiestos de actualizacion para Subway Builder
|   \-- cur-update.json              # Version oficial v1.0.0 de Cancun / Riviera Maya
|-- sb_mexico/                       # Nucleo del motor de procesamiento
|   |-- inegi.py                     # Ingesta, geocodificacion jerarquica, multi-archivo y CONAPO
|   |-- gravity.py                   # Furness IPFP, dos capas, affluence/exclusion zones, snapping y modal lab
|   |-- osrm.py                      # Microservicio OSRM (car.lua), huella SHA-256, cortafuegos y fallback Colin
|   |-- special_demand.py            # Generacion y validacion de demanda especial (Taxonomia canonica)
|   |-- toponymy.py                  # Extractor toponimico para OSM XML/PBF
|   |-- cartography.py               # Generador cartografico con depot.maps.MapGen y LOD urbano
|   |-- cartography_runner.py        # Compilacion cartografica nativa en Linux / WSL 2
|   |-- pipeline.py                  # Orquestador del flujo completo, auditoria espacial y validaciones
|   \-- schemas/                     # Esquemas JSON oficiales de validacion de demanda especial
|-- tests/                           # Suite formal de pruebas unitarias (162 tests)
|-- tools/                           # Herramientas de visualizacion y diseno
|   |-- wizard.py                    # Servidor web del asistente integral (6 Pasos, POI Studio en Paso 4)
|   |-- poi_studio.py                # Editor visual standalone de POIs en mapa satelital
|   |-- preview_toponymy.py          # Visor geoespacial de capas toponimicas
|   |-- patch_depot_wsl.py           # Suite de 9 parches cartograficos (Campus Wins, RAM, LOD, etiquetas, parques, toponimia nwr)
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
2. **Paso 1 (Proyectos y Delimitacion Espacial):**
   * Crea un nuevo proyecto o selecciona uno existente (ej. Cancun / Riviera Maya, CDMX, Merida).
   * Ajusta interactivamente el BBOX metropolitano con los 4 tiradores de esquina (`NW, NE, SE, SW`).
   * Configura el encuadre inicial de camara 16:9 (`initial_center`, `initial_zoom`) con el boton de captura rapida del visor.
   * Traza el perimetro del Nucleo Urbano LOD (`urban_core_polygon`) manualmente o usa el boton **Auto Censo** para calcular la envolvente concava (*Concave Hull*) a partir de las manzanas urbanas del INEGI.
   * Si existen islas o cuencas maritimas sin puente vehicular, traza Zonas Topologicas Aisladas (`isolated_zones`).
3. **Paso 2 (Fuentes de Datos INEGI / OSM):**
   * Inspecciona y valida los archivos censales en la carpeta activa `data/<ciudad>/`.
   * Verifica la deteccion automatica del Censo CPV 2020 a nivel manzana, DENUE estatal, Censos Economicos 2024 (SAIC) y ENOE trimestral, asi como el archivo `mexico-latest.osm.pbf`.
4. **Paso 3 (Calibracion Macroeconomica, CONAPO y Laboratorio Modal):**
   * Audita la tabla de desglose municipal CONAPO (`cve_mun`, nombre del municipio, ano de proyeccion, poblacion 2020, poblacion proyectada y factor resultante).
   * Calibra las tasas de informalidad laboral ($TIL_1$) y participacion (PEA) de la ENOE, y parametros de Furness IPFP.
   * Opcionalmente activa el Laboratorio de Competitividad Modal (`modal_experiment`) para simular congestion vial o alta dependencia de transporte colectivo lento.
5. **Paso 4 (POI Studio Geoespacial Integrado):**
   Editor cartografico satelital con 5 pestanas de especializacion:
   * **Pestana POIs:** Catalogo de generadores clave con buscador y filtros taxonomicos (`AIR_`, `UNI_`, `SPO_`, `MED_`, `TOU_`, `TRA_`).
   * **Pestana Editor:** Formulario interactivo con vista previa del nombre en el juego y calculo en vivo de absorcion de empleos DENUE en el radio configurado (`radius_m`, modo `MAX`, `BOOST` o `REPLACE`).
   * **Pestana Colonias:** Extraccion toponimica inteligente (`places`) con sugerencias automaticas de colonias y barrios prioritarios desde DENUE y Censo.
   * **Pestana Afluencia:** Trazado vectorial de Zonas de Alta Afluencia (`affluence_zones`) con arquetipos (`cbd`, `tourism`, `industrial`, `commercial`, `custom`), multiplicador de empleo y bono de alcance metropolitano (`reach_bonus`).
   * **Pestana Exclusiones:** Trazado poligonal de Zonas de Exclusion (`exclusion_zones`) para suprimir al 100% residentes y empleos en lagunas, manglares o reservas ecológicas preservando la red vial intacta.
6. **Paso 5 (Compilacion y Terminal de Telemetria en Vivo):**
   * Elige entre compilacion completa (Cartografia 3D + Demanda + ZIP) o solo regeneracion de demanda (`--skip-map`).
   * Conmuta la opcion de supresion de selvas y reservas no pobladas (**Solo parques urbanos** / `urban_parks_only`).
   * Supervisa en tiempo real el streaming de logs (SSE) con barra de progreso, conexion OSRM en WSL 2 y empaquetado final.
7. **Paso 6 (Visor de Demanda y Exportacion):**
   * Inspecciona en mapa WebGL las densidades de residentes (azul) y puestos de trabajo (rojo).
   * Revisa el volumen total de viajeros (`pops`) y descarga directamente el archivo ZIP empaquetado para el juego.

### Metodo B: Mediante CLI Directo
Guarda la configuracion en `cities/<ciudad>.yaml` y ejecuta:
```bash
# Compilacion completa (Cartografia 3D + Demanda Furness + Empaquetado ZIP):
python build.py cities/cancun_riviera_maya.yaml

# Solo regeneracion de demanda (si la cartografia ya esta compilada):
python build.py cities/cancun_riviera_maya.yaml --skip-map

# Habilitar geometrias drivingPath para auditoria SIG externa:
python build.py cities/cancun_riviera_maya.yaml --include-driving-path

# Especificar directorios personalizados de datos o salida:
python build.py cities/cancun_riviera_maya.yaml --data-dir data/mi_ciudad --output-dir dist/mi_ciudad
```

El archivo final queda listo en `dist/<ciudad>/<CODIGO>.zip`.

### Importar en el Juego
1. Abre **Subway Builder** o **Subway Builder Modded**.
2. Selecciona **ADD A MAP** dentro de **Kronifer's Map Manager / Railyard**.
3. Carga el archivo `.zip` generado en `dist/<ciudad>/`.
4. ¡Comienza a trazar tu red metropolitana de transporte!

---

## 5. DICCIONARIO COMPLETO DE PARAMETROS YAML

A continuacion se detallan todas las propiedades reconocidas por el motor en `cities/<ciudad>.yaml`:

### Seccion `city` (Identidad y Geometria Urbana)
| Parametro | Tipo | Descripcion / Valor Recomendado |
| :--- | :--- | :--- |
| `code` | string | Clave IATA o sigla unica de 3 letras en mayusculas (ej. `CUR`, `CDMX`, `MID`, `SAL`). |
| `name` | string | Nombre oficial completo de la zona metropolitana. |
| `description`| string | Descripcion breve para el menu de seleccion del juego (< 80 caracteres). |
| `bbox` | list | `[min_lon, min_lat, max_lon, max_lat]` del rectangulo de estudio metropolitano. |
| `creator` | string | Nombre o alias del creador del mapa. |
| `grid_size` | float | Resolucion de celda de demanda: `0.0018` (~180m, fino), `0.0025` (~250m, estandar), `0.0035` (metropolis masiva). |
| `min_residents`| int | Umbral minimo de habitantes para consolidar un nodo de origen (default: `10`). |
| `min_jobs` | int | Umbral minimo de empleos para consolidar un nodo de destino laboral (default: `3`). |
| `initial_zoom` | float | Nivel de zoom de inicio al abrir la partida (ej. `11.0` a `13.0`). |
| `initial_center`| list | `[lon, lat]` del encuadre inicial de camara para el Dia 1 de partida (calibrable en Paso 1 del Wizard). |
| `building_filter_size` | float | Filtro de area para edificios 3D en MapGen (default: `15.0` m²; hasta `35.0` m² en megaciudades). |
| `building_simplification` | float | Tolerancia de simplificacion geometrica de edificios (default: `0.2`). |
| `include_ocean` | bool | `true` para ciudades costeras que requieren batimetria marina; `false` para valles interiores. |
| `urban_parks_only` | bool | `true` para suprimir selvas, bosques y macro-reservas rurales fuera de parques urbanos civicos (default: `false`). |
| `urban_core_polygon` | list | Lista de pares `[[lon, lat], ...]` que delimita la mancha urbana densa (AOI LOD concéntrico) para apagar edificios 3D perifericos. |
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
| `max_distance_km` | float | Distancia maxima para considerar viajes urbanos cotidianos (default: `50.0` a `55.0` km). |
| `min_pop_size` | int | Tamano minimo por cohorte de viaje (default: `25`; previene micro-pops ineficientes de 1-10 pax). |
| `target_pop_size` | int | Tamano objetivo de empaquetado de cohortes para estabilizar la simulacion a 60 FPS (default: `150` a `180`). |
| `max_pop_size` | int | Tamano maximo permitido para una cohorte individual `pop` (default nativo Subway Builder: `200`). |
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

### Seccion `routing` (Ruteo OSRM y Control de Geometrias)
| Parametro | Tipo | Descripcion / Opciones |
| :--- | :--- | :--- |
| `include_driving_path` | bool | `false` (por defecto y recomendado): Genera archivos JSON ligeros (~1-3 MB) e inmunes al limite de 512 MB de V8 en Electron. `true`: Inyecta la traza vectorial GeoJSON de cada ruta para analisis SIG o visores externos. |

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

### Seccion `exclusion_zones` (Zonas de Exclusion - Cero Demanda)
| Parametro | Tipo | Descripcion / Opciones |
| :--- | :--- | :--- |
| `id` | string | Identificador unico de la zona de exclusion (ej. `excl_manglar`). |
| `name` | string | Nombre descriptivo del area excluida (ej. `Laguna / Reserva Natural`). |
| `type` | string | Tipo de geometria: `polygon` o `bbox`. |
| `reason` | string | Razon de exclusion: `water_body`, `ecological_reserve`, `unpopulated_island`, `military_restricted`. |
| `color` | string | Color hexadecimal para el visor y Wizard (default: `"#EF4444"`). |
| `coordinates` | list | Lista de pares `[lon, lat]` que delimitan el poligono cerrado. |
| `enabled` | bool | Activa o desactiva la exclusion (default: `true`). |

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

### Seccion `places` (Toponimia Urbana y Asentamientos Adicionales)
| Parametro | Tipo | Descripcion / Reglas |
| :--- | :--- | :--- |
| `name` | string | Nombre visible del asentamiento, colonia o barrio (ej. `Supermanzana 94`, `Centro Historico`, `Polanco`). |
| `loc` | list | `[longitud, latitud]` exacta del centroide del asentamiento. |
| `type` | string | Categoria toponimica: `suburb` (colonia / supermanzana), `neighbourhood` (barrio / fraccionamiento), `quarter` (cuadrante / seccion). |

---

## 6. FORMULACION MATEMATICA DE REFERENCIA

### 1. Modelo en Dos Capas y Deduccion de Presupuesto
* **Capa 1 (Hubs Especiales):** Asignacion de alcance metropolitano ($\beta_{\text{esp}} = 0.04$):
  $$W_{ik} = \text{PEA}_i \cdot e^{-\beta_{\text{esp}} \cdot d_{ik}}$$
  $$\vec{T}_{\cdot \to k} \sim \operatorname{Multinomial}\left(K_k, \ \vec{P}_k\right)$$
  Deduccion estricta para evitar doble viaje: $\text{PEA}_i^{\text{rem}} = \text{PEA}_i - \sum_k T_{i \to k}$.
* **Capa 2 (Furness / IPFP Doblemente Acotado):**
  Balanceo iterativo que resuelve la matriz $T_{ij}$ tal que $\sum_j T_{ij} = \text{PEA}_i^{\text{rem}}$ y $\sum_i T_{ij} \propto E_j$, con friccion modulada por zonas de afluencia $\beta_j = \beta (1 - \text{reach\_bonus}_j)$ y supresion estricta en zonas de exclusion ($E_j = 0$).

### 2. Ruteo Vial OSRM, Huella Criptografica y Cortafuegos de Snapping
* **OSRM en WSL 2:** Distancia real de calle, duracion en segundos a flujo libre (~40 km/h) con desacoplamiento seguro de `drivingPath` (V8 Safe).
* **Huella Criptografica (SHA-256):** Hash unificado sobre BBOX, OSM PBF y `car.lua` para garantizar reproducibilidad e invalidacion automatica de cache en WSL 2.
* **Cortafuegos de Snapping Extremo (1500m):** Si la distancia de proyeccion a la red vial excede 1,500m, se aplica el fallback canonico de Colin para prevenir atajos a traves de cuerpos de agua o vacios topologicos.
* **Fallback Canonico de Colin (sin Docker o en vias insulares):**
  $$\text{drivingDistance} = \max(150\text{ m}, \ \operatorname{round}(d_{\text{euclid}} \times 1.3))$$
  $$\text{drivingSeconds} = \max\left(45\text{ s}, \ \operatorname{round}\left(\frac{\text{drivingDistance}}{40.0 / 3.6}\right)\right)$$

### 3. Teorema de Conservacion Estricta de Masa
$$\sum_{p \in \text{pops}} \text{size}(p) \equiv \sum_{i \in \text{celdas}} \text{PEA}_i \qquad (\Delta = 0\text{ personas})$$
Garantiza fidelidad cientifica absoluta y compatibilidad nativa con Subway Builder.

### 4. Zonificacion Concentrica (Urban Core AOI LOD)
Aplica filtrado en dos niveles con `osmium extract` y `tags-filter`:
* **BBOX Completo:** Red vial troncal (`motorway`, `trunk`, `primary`, `secondary`, `tertiary`), vias ferreas y cuerpos de agua para mantener horizontes visuales infinitos, suprimiendo etiquetas de lugares en la periferia exterior (`apply_urban_lod_filtering`).
* **Nucleo Urbano (`urban_core_polygon`):** Red vial menor (`residential`, `service`, `living_street`, `pedestrian`), etiquetas toponimicas urbanas y edificios 3D (`patch_urban_core_lod`, `patch_urban_core_labels`), reduciendo el peso de teselas vectoriales hasta en un 80% y eliminando cuellos de botella de memoria.

### 5. Suite de Parches Cartograficos en WSL 2 (depot.maps)
El script `tools/patch_depot_wsl.py` aplica 9 parches de estabilidad y estandares canonicos sobre el compilador de mapas:
1. **Campus Wins (`patch_campus_wins`):** Sustraccion geometrica de `college_mask` sobre comercios superpuestos y etiquetado dual `type: 'college', kind: 'college'`.
2. **Desbloqueo CPU Oceano (`patch_ocean_cpu`):** Asignacion de 100% de cores CPU para batimetria marina.
3. **Zoom Mascara de Agua (`patch_ocean_water_zoom`):** Optimizacion a $z=14$ acelerando procesamiento oceanico en >60%.
4. **Seguridad de RAM (`patch_ram_safety`):** Correccion de overflow de multiplicacion doble en MapGen (evita OOM killer).
5. **Resiliencia de Edificios 3D (`patch_resilient_buildings`):** Simplificacion nativa en GeoPandas/Shapely eliminando fallos de Mapshaper/Node.js.
6. **Filtrado de Macro-Parques (`patch_urban_parks`):** Supresion de selvas y reservas no pobladas (`SB_URBAN_PARKS_ONLY=1`).
7. **Urban Core LOD Edificios (`patch_urban_core_lod`):** Confinamiento de volumenes 3D al perimetro denso metropolitano.
8. **Urban Core LOD Etiquetas (`patch_urban_core_labels`):** Supresion estricta de toponimia fuera del nucleo (`SB_URBAN_CORE_GEOJSON`).
9. **Toponimia Universal NWR (`patch_polygon_neighborhoods`):** Extraccion de vias y relaciones poligonales en OSM (`nwr/place`) con conversion automatica a centroides `Point` y fusion con asentamientos del DENUE (`neighborhoods_additional`).

### 6. Encuadre Inicial de Camara (Viewport Centrado en Masa)
* **Modo Manual:** Coordenadas declaradas explicitamente en `initial_center` y `initial_zoom` calibradas en el Wizard Studio con encuadre 16:9.
* **Modo Automatico (Fallback):** Baricentro ponderado de la masa activa censal:
  $$\text{cam\_lat} = \frac{\sum_i \text{PEA}_i \cdot \text{lat}_i}{\sum_i \text{PEA}_i}, \qquad \text{cam\_lon} = \frac{\sum_i \text{PEA}_i \cdot \text{lon}_i}{\sum_i \text{PEA}_i}$$
