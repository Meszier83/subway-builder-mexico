<div align="center">

# Subway Builder Mexico (v7.1)
### Motor de Generacion de Demanda, Ruteo Vial y Cartografia 3D para Mexico

[![Release](https://img.shields.io/badge/Release-v7.1.0-blue.svg?style=flat-square)](https://github.com/Meszier83/subway-builder-mexico)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Engine](https://img.shields.io/badge/Engine-Furness%20IPFP%20%7C%20OSRM-purple.svg?style=flat-square)](METHODOLOGY.md)
[![UI Theme](https://img.shields.io/badge/UI-Metro%20CDMX%20%2F%20Lance%20Wyman-E53935.svg?style=flat-square)](tools/wizard.py)
[![Standards](https://img.shields.io/badge/Standards-S--Tier%20Strict-059669.svg?style=flat-square)](.agents/rules/subway_builder_standards.md)
[![Tests](https://img.shields.io/badge/Tests-162%20Passed-10B981.svg?style=flat-square)](tests/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)

**Pipeline integral, declarativo y matematicamente riguroso para transformar microdatos abiertos del INEGI (CPV 2020, DENUE, CE 2024, ENOE) y CONAPO en mapas metropolitanos de alta fidelidad, perfectamente compatibles con Subway Builder y Subway Builder Modded.**

[Inicio Rapido](#inicio-rapido-quickstart) | [Innovaciones v7.1](#innovaciones-arquitectonicas-v71) | [Wizard Studio](#asistente-visual-integral-wizard-studio) | [Documentacion](#documentacion-oficial-y-fundamentos)

</div>

---

## Vision del Proyecto

En Mexico no existe un repositorio publico universal de matrices origen-destino a escala micrometrica (como *LODES/LEHD* en EE. UU.). Tradicionalmente, crear ciudades mexicanas para simuladores de transporte exigia recurrir a encuestas domiciliarias desactualizadas o a la distribucion uniforme artificial, generando megapuntos irreales o redes colapsadas.

**Subway Builder Mexico** cierra esta brecha implementando un marco cientifico de **microsimulacion de eleccion discreta, modelo gravitatorio en dos capas con algoritmo de Furness (IPFP) y ruteo vial canonico con OSRM**. El motor garantiza la conservacion estricta de la masa activa censal ($\Delta = 0$ personas), asigna cuotas exactas a generadores metropolitanos clave y modela la circulacion vehicular sobre las calles reales de OpenStreetMap en WSL 2 con zonificacion concentrica de nivel de detalle (Urban Core AOI LOD) y proteccion de memoria contra limites de V8 en Electron.

---

## Comparativa: Modelado Convencional vs. Subway Builder Mexico v7.1

| Dimension | Modelado Tradicional / Heuristico | Subway Builder Mexico v7.1 |
| :--- | :--- | :--- |
| **Conservacion de Masa** | Inflacion artificial de pasajeros; duplicacion arbitraria en hubs. | **Conservacion estricta invariante:** $\sum \text{Pops} \equiv \sum \text{PEA}$ ($\Delta = 0$ personas). |
| **Distribucion de Empleo** | Asignacion uniconstrenida (satura comercios pequenos, vacia parques industriales). | **Furness / IPFP Doblemente Acotado:** Satisface la PEA residencial y la capacidad laboral real del destino. |
| **Generadores Especiales** | Burbujas infladas de poblacion ficticia. | **Modelo en Dos Capas con Deduccion:** Cuota exacta (AFAC/SEP/ANUIES) y deduccion estricta ($\text{PEA}_i^{\text{rem}}$). |
| **Ruteo y Tiempos Viales** | Distancia euclidiana recta (autos cruzando oceanos y lagunas). | **OSRM Canonico (`car.lua`) en WSL 2:** Kilometros de pavimento real, tiempos a ~40 km/h y desacoplamiento seguro de `drivingPath` (V8 Safe). |
| **Corredores Economicos** | Un solo mega-POI que arruina el corredor y colapsa una sola estacion. | **Zonas de Alta Afluencia (`affluence_zones`):** Modulacion de atraccion territorial y bono de alcance metropolitano. |
| **Zonas de Exclusion** | Demanda y viajes asignados en lagunas, manglares y reservas naturales. | **Zonas de Exclusion (`exclusion_zones`):** Cero demanda ni viajes en zonas no habitables preservando 100% la red vial y mapas 3D. |
| **Zonificacion y LOD 3D** | Edificios 3D y calles menores en todo el BBOX (cuelgues de GPU y OOM). | **Nucleo Urbano LOD (`urban_core_polygon`):** Edificios 3D y vias locales acotados a la mancha urbana; horizontes continuos en todo el BBOX. |
| **Filtrado de Parques** | Selvas, sierras y macro-reservas rurales como areas verdes saturadas. | **Filtrado Urbano (`urban_parks_only`):** Suprime macro-reservas rurales manteniendo plazas, parques y camellones urbanos. |
| **Georreferenciacion Censal** | Imputacion del centro de BBOX (crea megapuntos en lagos, sierras o manglares). | **Cascada Cuadruple:** MGM $\to$ DENUE Manzana $\to$ MGM AGEB $\to$ DENUE AGEB y `dropna()` estricto. |
| **Sincronizacion Temporal** | Desfase temporal (Censo 2020 vs. DENUE/CE contemporaneos). | **Proyecciones CONAPO 2020–2053:** Proyeccion intercensal transparente por municipio (`growth_factors`). |
| **Experiencia de Usuario** | Comandos dispersos en consola. | **Wizard Studio 6 Pasos:** Estetica Metro CDMX (Lance Wyman), POI Studio integrado, encuadre 16:9 y streaming SSE en vivo. |

---

## Innovaciones Arquitectonicas v7.1

```text
                                  [FLUJO DE DATOS INTEGRAL v7.1]
                                  
  INEGI CPV 2020 (Manzanas) -----> [Georreferenciacion Cascada MGM/DENUE] --+
  INEGI DENUE (Establecimientos) -> [Calibracion Asimetrica CE 2024 (H001A)]-+-> [Malla Espacial de Demanda]
  CONAPO Proyecciones 2026 ------> [Sincronizacion Demografica Intercensal]-+           |
                                                                                        v
                                  +-----------------------------------------------------+
                                  | CAPA 1: Hubs Especiales (AIR_, UNI_, SPO_, MED_)   |
                                  | Cuotas exactas AFAC/SEP + Deduccion estricta PEA    |
                                  +-----------------------------------------------------+
                                                            |
                                                            v
                                  +-----------------------------------------------------+
                                  | CAPA 2: Empleo Regular (Furness / IPFP Bidireccional)|
                                  | Zonas de Alta Afluencia (affluence_zones)           |
                                  | Zonas de Exclusion (exclusion_zones - cero demanda) |
                                  | Zonas Aisladas Estancas (isolated_zones)            |
                                  +-----------------------------------------------------+
                                                            |
                                                            v
                                  +-----------------------------------------------------+
                                  | FISICA VIAL: Ruteo Canonico OSRM (car.lua) en WSL 2 |
                                  | drivingSeconds y drivingDistance a flujo libre      |
                                  | Huella SHA-256, cortafuegos de snapping (1500m)     |
                                  | drivingPath opcional (V8 Safe, off por defecto)     |
                                  | Fallback canonico de Colin (circuidad tau=1.3)     |
                                  +-----------------------------------------------------+
                                                            |
                                                            v
                                  +-----------------------------------------------------+
                                  | CARTOGRAFIA 3D: LOD Concentrico y Regla Campus Wins |
                                  | Nucleo Urbano LOD (urban_core_polygon) + Parques    |
                                  | Simplificacion GeoPandas resiliente en WSL 2 Ubuntu |
                                  +-----------------------------------------------------+
                                                            |
                                                            v
                                  +-----------------------------------------------------+
                                  | EMPAQUETADO FINAL (dist/<ciudad>/<CODIGO>.zip)      |
                                  | Listo para Kronifer's Map Manager / Railyard        |
                                  +-----------------------------------------------------+
```

### 1. Modelo en Dos Capas (Two-Tier Doubly-Constrained)
* **Capa 1 (Generadores Metropolitanos):** Aeropuertos (`AIR_`), Universidades (`UNI_`), Estadios (`SPO_`) y Hospitales (`MED_`) reciben su cuota exacta mediante atraccion gravitatoria de largo alcance ($\beta_{\text{esp}} = 0.04$) y sorteo multinomial acotado. La masa asignada se deduce formalmente de la PEA del origen ($\text{PEA}_i^{\text{rem}} = \text{PEA}_i - \sum_k T_{i \to k}$), impidiendo la duplicacion de viajes.
* **Capa 2 (Furness / IPFP Doblemente Acotado):** El remanente de trabajadores y puestos de trabajo comerciales, corporativos e industriales se equilibra iterativamente hasta converger simultaneamente a los totales marginales de origen y destino.

### 2. Ruteo Vial Canonico con OSRM, Huella Criptografica y Desacoplamiento V8
* **OSRM Oficial con `car.lua`:** En estricto cumplimiento con el canon del autor Colin Miller, las cohortes `pops` incorporan tiempos de viaje a flujo libre (~40 km/h) y distancias de pavimento real.
* **Desacoplamiento de `drivingPath` (V8 Safe):** Las geometrias de ruta estan desactivadas por defecto (`include_driving_path: false`), consultando OSRM con `overview=false`. Esto protege a Electron contra el limite rigido de 512 MB por cadena de V8 (`ERR_STRING_TOO_LONG`), manteniendo los archivos livianos (~1-3 MB) sin riesgo de colapso en metropolis de >20,000 cohortes.
* **Huella Criptografica de Red (SHA-256):** Deteccion automatica de cambios en BBOX, extracto OSM PBF o perfil vial para invalidacion estricta de cache OSRM en WSL 2.
* **Cortafuegos de Snapping Extremo (1500m):** Si un nodo esta a mas de 1,500m de la red vial accesible, el ruteo se desvia al fallback canonico de Colin para prevenir atajos artificiales a traves de lagunas o barreras geograficas.
* **Resiliencia de Microservicios:** Supervisor persistente contra la suspension idle de WSL 2 (`SIGTERM 15`), renovacion de sockets HTTP Keep-Alive (`max=512`) y fail-fast con fallback canonico de Colin ante anomalias de red.

### 3. Zonas de Alta Afluencia (`affluence_zones`)
Delimita corredores y distritos de empleo intensivo (Reforma, Santa Fe, Zona Hotelera de Cancun, San Pedro Garza Garcia) mediante poligonos vectoriales:
* **Arquetipos Urbanos:** `cbd` (2.5x, bono alcance 0.40), `tourism` (2.2x, bono 0.35), `industrial` (1.8x, bono 0.25), `commercial` (1.4x, bono 0.15) o `custom`.
* **Modulacion de Friccion:** Bono de alcance ($\text{reach\_bonus} \in [0.0, 0.60]$) que reduce $\beta_j = \beta (1 - \text{reach\_bonus})$ con salvaguarda de piso de retencion local ($d \le 3\text{ km}$).
* **Regla Canonica MAX Priority:** Resuelve solapamientos espaciales de forma determinista y preserva intactas las cuotas de los POIs especiales contenidos.

### 4. Zonas de Exclusion (`exclusion_zones`)
Delimita poligonos o bounding boxes donde se omite al 100% la simulacion de poblacion, empleo y cohortes `pops` (cuerpos de agua, lagunas, manglares, reservas naturales protegidas o zonas militares). Toda la infraestructura vial, vias y edificios 3D continúan renderizandose con fidelidad total en el juego.

### 5. Zonificacion Concentrica y Nucleo Urbano LOD (`urban_core_polygon`)
Permite recortar la generacion de edificios 3D y calles secundarias exclusivamente al contorno de la mancha urbana central mediante `osmium extract` y `tags-filter`, manteniendo carreteras troncales, costas y horizontes limpios en todo el BBOX metropolitano, suprimiendo ademas etiquetas toponimicas de lugares en la periferia (`patch_urban_core_labels`). Incluye trazado interactivo en el Wizard y calculo automatico de envolvente concava (*Concave Hull*) a partir de microdatos censales (`/api/auto-urban-polygon`).

### 6. Filtrado de Macro-Parques Urbanos (`urban_parks_only`)
Opcion de compilacion que suprime selvas masivas, sierras y macro-reservas de biosfera rurales fuera de los centros de poblacion (`patch_urban_parks`), preservando intactos los parques urbanos, jardines y camellones cívicos.

### 7. Zonas Topologicas Aisladas (`isolated_zones`)
Tratamiento matematicamente estanco para conurbaciones costeras con islas habitadas sin conexion vial terrestre (ej. Isla Mujeres o Cozumel), eliminando la generacion de trayectos imposibles en automovil sobre el oceano.

### 8. Encuadre Inicial de Camara 16:9 y Calibracion de Cohortes
* **Encuadre 16:9 (`initial_center` & `initial_zoom`):** Herramienta interactiva en el Wizard con marco de captura rapida para fijar la camara del Dia 1 en el juego, con fallback automatico al baricentro ponderado de masa activa.
* **Calibracion de Cohortes (`min_pop_size`, `target_pop_size`, `max_pop_size`):** Previene micro-cohortes ineficientes (1-10 pax) y estabiliza la simulacion WebGL a 60 FPS continuos.

### 9. Motor Cartografico Resiliente en WSL 2 y Regla "Campus Wins"
* **Campus Wins:** Disyuntividad geometrica estricta que sustrae la mascara de campus universitarios (`college_mask`) sobre poligonos comerciales superpuestos y aplica etiquetado dual `type: 'college', kind: 'college'`.
* **Resiliencia de Edificios 3D:** Simplificacion nativa en GeoPandas sin dependencia externa de Mapshaper Node.js, salvaguarda de memoria RAM (`patch_ram_safety`), aceleracion de calculo oceanico con 100% de nucleos CPU y supresion de etiquetas de lugares fuera del nucleo (`patch_urban_core_labels`).

### 10. Laboratorio Experimental de Competitividad Modal (`modal_experiment`)
Modulo opcional para simular escenarios de congestion vial severa o alta dependencia de transporte colectivo lento de superficie mediante la formulacion de Impedancia Alternativa Ponderada. Desactivado por defecto (`enabled: false`) para mantener la pureza canonica del juego.

---

## Asistente Visual Integral (Wizard Studio)

El **Wizard Studio** es una aplicacion web local completa construida con la identidad visual y los colores oficiales de la senaletica del Metro de la Ciudad de Mexico (disenada por **Lance Wyman**):

```bash
# Lanzamiento del Wizard Studio
python tools/wizard.py
# o en Windows mediante doble clic en:
wizard.bat
```

```text
[1. Proyectos & Delimitacion] Gestion aislada, BBOX interactivo con tiradores, encuadre 16:9 de camara, Nucleo Urbano LOD y Zonas Aisladas.
[2. Fuentes de Datos]         Carga y validacion automatica de microdatos censales INEGI (CPV, DENUE, CE, ENOE) y OSM PBF.
[3. Calibracion & CONAPO]     Auditoria municipal CONAPO transparente, tasas ENOE, Furness IPFP y Laboratorio Modal experimental.
[4. POI Studio Geoespacial]   Editor unificado con 5 pestanas: POIs, Editor con absorcion DENUE, Colonias/Toponimia, Zonas de Afluencia y Exclusiones.
[5. Compilacion & Logs]       Compilacion cartografica 3D en WSL 2, ruteo OSRM, opcion 'Solo parques urbanos' y streaming SSE en vivo.
[6. Visor & Exportacion]      Inspeccion interactiva WebGL de densidades/cohortes y descarga directa del paquete ZIP empaquetado.
```

Si prefieres editar POIs de forma standalone en mapa satelital:
```bash
python tools/poi_studio.py --city cities/cancun_riviera_maya.yaml
```

---

## Inicio Rapido (Quickstart)

### 1. Clonar el Repositorio e Instalar Dependencias

```bash
git clone https://github.com/Meszier83/subway-builder-mexico.git
cd subway-builder-mexico
pip install -r requirements.txt
```

### 2. Estructura y Colocacion de Datos (Regla 9)

Organiza las fuentes abiertas del INEGI y OSM conforme al principio de aislamiento hermetico por proyecto (*Project Bubble Isolation*):

```text
data/                                # Datasets de alcance nacional
|-- mexico-latest.osm.pbf            # Extracto OSM nacional PBF (Geofabrik)
|-- proyecciones_conapo_2020_2053.csv# Proyecciones demograficas municipales CONAPO
\-- cancun_riviera_maya/             # Microdatos especificos de la metropoli
    |-- RESAGEBURB_23CSV20.csv       # Censo CPV 2020 a nivel manzana (o subcarpeta conjunto_de_datos)
    |-- denue_inegi_23_.csv          # DENUE estatal
    |-- SAIC_Exporta_23.csv          # Censos Economicos 2024 (H001A municipal)
    \-- ENOE_2026_Entidad_23.csv     # Indicadores estrategicos ENOE
```

> Consulta la [Guia de Fuentes de Datos (DATA_SOURCES.md)](DATA_SOURCES.md) para enlaces directos de descarga gratuita de cualquier estado en 3 minutos.

### 3. Disenar la Ciudad en YAML

Crea o edita el archivo declarativo en `cities/<ciudad>.yaml` basandote en [`cities/_template.yaml`](cities/_template.yaml) (ejemplo [`cities/cancun_riviera_maya.yaml`](cities/cancun_riviera_maya.yaml)):

```yaml
city:
  code: "CUR"
  name: "Cancun / Riviera Maya"
  description: "Zona Metropolitana de Cancun y Riviera Maya"
  bbox: [-87.6338, 20.1311, -85.6659, 21.8564]
  grid_size: 0.0018
  include_ocean: true
  urban_parks_only: true
  initial_zoom: 11.0

macroeconomics:
  tasa_pea: 0.665
  til_1_state: 0.450
  gravity_beta: 0.12
  min_pop_size: 25
  target_pop_size: 150
  max_pop_size: 200

routing:
  include_driving_path: false         # V8 Safe (desactivado por defecto)

pois:
  - id: "AIR_Cancun"
    name: "Aeropuerto Internacional de Cancun"
    type: "airport"
    loc: [-86.874, 21.036]
    jobs: 32000
    radius_m: 2500
    mode: "MAX"
```

### 4. Compilar la Metropoli por CLI

```bash
# Compilacion completa (Cartografia 3D + Modelo Furness + Empaquetado ZIP):
python build.py cities/cancun_riviera_maya.yaml

# Solo regeneracion de demanda (si ya tienes los mapas compilados):
python build.py cities/cancun_riviera_maya.yaml --skip-map

# Habilitar geometrias drivingPath para auditoria SIG externa:
python build.py cities/cancun_riviera_maya.yaml --include-driving-path
```

### 5. Importar en el Juego

El compilador genera de forma estanca el paquete listo para importar en:
`dist/<ciudad>/<CODIGO>.zip` (ejemplo: `dist/cancun_riviera_maya/CUR.zip`). El motor mantiene resolucion retrocompatible si se utiliza la nomenclatura previa `cancun.yaml` (`CUN.zip`).

1. Abre **Subway Builder** o **Subway Builder Modded**.
2. Entra a **Kronifer's Map Manager / Railyard**.
3. Haz clic en **ADD A MAP** y selecciona el archivo `.zip`.
4. Disena tu red metropolitana de trenes sobre datos reales y balanceados.

---

## Estructura del Repositorio

```text
cities/                  # Archivos de configuracion por metropoli (.yaml)
|-- _template.yaml       # Plantilla maestra documentada v7.1
|-- cancun_riviera_maya.yaml # Configuracion de Cancun / Riviera Maya (CUR)
|-- ciudad_de_mexico.yaml# Configuracion de la CDMX y Valle de Mexico (CDMX)
|-- merida.yaml          # Configuracion de Merida / Yucatan (MID)
\-- saltillo.yaml        # Configuracion de Saltillo / Coahuila (SAL)
releases/                # Manifiestos oficiales de publicacion y actualizaciones
\-- cur-update.json      # Manifiesto v1.0.0 de Cancun / Riviera Maya
sb_mexico/               # Nucleo algoritmico del motor
|-- inegi.py             # Ingesta, jerarquia censal MGM, multi-archivo y CONAPO
|-- gravity.py           # Furness IPFP, dos capas, affluence zones, snapping y modal lab
|-- osrm.py              # Microservicio OSRM (car.lua), supervisor WSL 2 y fallback Colin
|-- special_demand.py    # Validacion y exportacion de demanda especial (Taxonomia canonica)
|-- toponymy.py          # Extractor toponimico para OSM XML/PBF
|-- cartography.py       # Wrapper de depot.maps.MapGen y puente WSL 2
|-- cartography_runner.py# Runner de compilacion cartografica nativa en Linux / WSL 2
|-- pipeline.py          # Orquestador del flujo completo, validaciones y ZIP
\-- schemas/             # Esquemas JSON oficiales de validacion de demanda especial
tests/                   # Suite formal de pruebas unitarias (162 tests)
tools/                   # Herramientas de visualizacion y diseno interactivo
|-- wizard.py            # Servidor web del asistente integral (con POI Studio en Paso 4)
|-- poi_studio.py        # Editor visual standalone de POIs y calibrador satelital
|-- preview_toponymy.py  # Visor geoespacial de capas toponimicas
|-- patch_depot_wsl.py   # Suite de 8 parches cartograficos (Campus Wins, RAM, LOD, etiquetas, parques)
\-- demo_preview.py      # Generador rapido de vistas previas
build.py                 # CLI de ejecucion principal
wizard.bat               # Lanzador directo de Wizard en Windows
visualize.py             # Visor HTML interactivo de demanda
DATA_SOURCES.md          # Guia oficial de descarga y organizacion de datos
METHODOLOGY.md           # Libro blanco, algoritmo de Furness y formulacion matematica v7.1
MANUAL_MAESTRO_v7.1.md   # Manual maestro de referencia tecnica y diccionario YAML
requirements.txt         # Dependencias de Python
```

---

## Documentacion Oficial y Fundamentos

* [**Guia de Fuentes de Datos (DATA_SOURCES.md)**](DATA_SOURCES.md): Pasos para descargar y estructurar datasets de cualquier entidad federativa en minutos.
* [**Libro Blanco y Fundamentos Matematicos (METHODOLOGY.md)**](METHODOLOGY.md): Formulacion matematica completa de Furness IPFP, Zonas de Alta Afluencia, ruteo OSRM, dimensionamiento de cohortes y fisica de trafico.
* [**Manual Maestro v7.1 (MANUAL_MAESTRO_v7.1.md)**](MANUAL_MAESTRO_v7.1.md): Guia de referencia paso a paso y diccionario exhaustivo de parametros de configuracion YAML.
* [**Estandares de Calidad S-Tier (.agents/rules/subway_builder_standards.md)**](.agents/rules/subway_builder_standards.md): Las reglas maestras de rigor algoritmico, inmunidad de codificacion UTF-8 y arquitectura de microservicios.

---

## Garantia de Calidad y Pruebas Unitarias

El proyecto cuenta con una suite rigurosa de **162 pruebas unitarias automatizadas** que validan:
* Conservacion estricta de masa ($\Delta = 0$) y limites de cohortes (`min_pop_size`, `target_pop_size`, `max_pop_size`).
* Balanceo bidireccional de Furness / IPFP y pisos de retencion local.
* Modulacion y regla *MAX Priority* en Zonas de Alta Afluencia (`affluence_zones`).
* Supresion total en Zonas de Exclusion (`exclusion_zones`) preservando 100% la cartografia 3D.
* Zonificacion concentrica y Nucleo Urbano LOD (`urban_core_polygon`) con filtrado de parques (`urban_parks_only`).
* Ruteo OSRM con perfil `car.lua`, huella criptografica SHA-256, cortafuegos de snapping (1500m), renovacion Keep-Alive y fallback canonico de Colin.
* Desacoplamiento de `drivingPath` para total inmunidad contra el limite de 512 MB de V8 en Electron.
* Regla cartografica canonica *Campus Wins*, etiquetado dual y parches de RAM en WSL 2.
* Encuadre de camara inicial 16:9 y persistencia exhaustiva del Wizard Studio.
* Inmunidad absoluta contra falsos positivos de codificacion UTF-8 sin BOM (`tests/test_encoding.py`).

Para ejecutar la suite de pruebas:
```bash
python -m unittest discover -s tests
```

---

## Licencia y Creditos

* **Licencia:** Distribuido bajo la [Licencia MIT](LICENSE).
* **Motor de Simulacion Base:** *Subway Builder* por **Colin Miller**.
* **Variante Avanzada:** *Subway-Builder-Modded* por **Kronifer**.
* **Fuentes Estadisticas:** [INEGI](https://www.inegi.org.mx/) (CPV 2020, DENUE, Censos Economicos, ENOE) y [CONAPO](https://datos.gob.mx/) (Proyecciones de Poblacion).
* **Datos Cartograficos:** Colaboradores de [OpenStreetMap](https://www.openstreetmap.org/) y extractos de [Geofabrik](https://download.geofabrik.de/).
* **Inspiracion Grafica:** Sistema de Senaletica del Metro de la Ciudad de Mexico por **Lance Wyman** (1969).
