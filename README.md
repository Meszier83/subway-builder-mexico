<div align="center">

# Subway Builder Mexico (v7.1)
### Motor de Generacion de Demanda, Ruteo Vial y Cartografia 3D para Mexico

[![Release](https://img.shields.io/badge/Release-v7.1.0-blue.svg?style=flat-square)](https://github.com/Meszier83/subway-builder-mexico)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Engine](https://img.shields.io/badge/Engine-Furness%20IPFP%20%7C%20OSRM-purple.svg?style=flat-square)](METHODOLOGY.md)
[![UI Theme](https://img.shields.io/badge/UI-Metro%20CDMX%20%2F%20Lance%20Wyman-E53935.svg?style=flat-square)](tools/wizard.py)
[![Standards](https://img.shields.io/badge/Standards-S--Tier%20Strict-059669.svg?style=flat-square)](.agents/rules/subway_builder_standards.md)
[![Tests](https://img.shields.io/badge/Tests-114%20Passed-10B981.svg?style=flat-square)](tests/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)

**Pipeline integral, declarativo y matematicamente riguroso para transformar microdatos abiertos del INEGI (CPV 2020, DENUE, CE 2024, ENOE) y CONAPO en mapas metropolitanos de alta fidelidad, perfectamente compatibles con Subway Builder y Subway Builder Modded.**

[Inicio Rapido](#inicio-rapido-quickstart) | [Innovaciones v7.1](#innovaciones-arquitectonicas-v71) | [Wizard Studio](#asistente-visual-integral-wizard-studio) | [Documentacion](#documentacion-oficial-y-fundamentos)

</div>

---

## Vision del Proyecto

En Mexico no existe un repositorio publico universal de matrices origen-destino a escala micrometrica (como *LODES/LEHD* en EE. UU.). Tradicionalmente, crear ciudades mexicanas para simuladores de transporte exigia recurrir a encuestas domiciliarias desactualizadas o a la distribucion uniforme artificial, generando megapuntos irreales o redes colapsadas.

**Subway Builder Mexico** cierra esta brecha implementando un marco cientifico de **microsimulacion de eleccion discreta, modelo gravitatorio en dos capas con algoritmo de Furness (IPFP) y ruteo vial canonico con OSRM**. El motor garantiza la conservacion estricta de la masa activa censal ($\Delta = 0$ personas), asigna cuotas exactas a generadores metropolitanos clave y modela la circulacion vehicular sobre las calles reales de OpenStreetMap en WSL 2.

---

## Comparativa: Modelado Convencional vs. Subway Builder Mexico v7.1

| Dimension | Modelado Tradicional / Heuristico | Subway Builder Mexico v7.1 |
| :--- | :--- | :--- |
| **Conservacion de Masa** | Inflacion artificial de pasajeros; duplicacion arbitraria en hubs. | **Conservacion estricta invariante:** $\sum \text{Pops} \equiv \sum \text{PEA}$ ($\Delta = 0$ personas). |
| **Distribucion de Empleo** | Asignacion uniconstrenida (satura comercios pequenos, vacia parques industriales). | **Furness / IPFP Doblemente Acotado:** Satisface la PEA residencial y la capacidad laboral real del destino. |
| **Generadores Especiales** | Burbujas infladas de poblacion ficticia. | **Modelo en Dos Capas con Deduccion:** Cuota exacta (AFAC/SEP/ANUIES) y deduccion estricta ($\text{PEA}_i^{\text{rem}}$). |
| **Ruteo y Tiempos Viales** | Distancia euclidiana recta (autos cruzando oceanos y lagunas). | **OSRM Canonico (`car.lua`) en WSL 2:** Kilometros de pavimento real, tiempos a ~40 km/h y geometria `drivingPath`. |
| **Corredores Economicos** | Un solo mega-POI que arruina el corredor y colapsa una sola estacion. | **Zonas de Alta Afluencia (`affluence_zones`):** Modulacion de atraccion territorial y bono de alcance metropolitano. |
| **Georreferenciacion Censal** | Imputacion del centro de BBOX (crea megapuntos en lagos, sierras o manglares). | **Cascada Cuadruple:** MGM $\to$ DENUE Manzana $\to$ MGM AGEB $\to$ DENUE AGEB y `dropna()` estricto. |
| **Sincronizacion Temporal** | Desfase temporal (Censo 2020 vs. DENUE/CE contemporaneos). | **Proyecciones CONAPO 2020–2053:** Proyeccion intercensal transparente por municipio (`growth_factors`). |
| **Experiencia de Usuario** | Comandos dispersos en consola. | **Wizard Studio Unificado:** Estetica Metro CDMX (Lance Wyman), POI Studio integrado y streaming SSE en vivo. |

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
                                  | Zonas Aisladas Estancas (isolated_zones)            |
                                  +-----------------------------------------------------+
                                                            |
                                                            v
                                  +-----------------------------------------------------+
                                  | FISICA VIAL: Ruteo Canonico OSRM (car.lua) en WSL 2 |
                                  | drivingSeconds, drivingDistance y drivingPath       |
                                  | Fallback canonico de Colin (circuidad tau=1.3)     |
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

### 2. Ruteo Vial Canonico con OSRM y WSL 2
* **OSRM Oficial con `car.lua`:** En cumplimiento con las directrices del motor original de Colin Miller, las cohortes de desplazamiento commuter (`pops`) se enriquecen con tiempos de viaje a flujo libre (~40 km/h), distancias reales de pavimento y la traza vectorial GeoJSON (`drivingPath`).
* **Resiliencia de Microservicios:** Implementa recorte ultrarrapido con `osmium extract` (de 15 min a 1.2s), supervisor persistente contra la suspension idle de WSL 2 (`SIGTERM 15`), renovacion de sockets HTTP Keep-Alive (`max=512`) y fail-fast con fallback canonico de Colin.

### 3. Zonas de Alta Afluencia (`affluence_zones`)
Delimita corredores y distritos de empleo intensivo (Reforma, Santa Fe, Zona Hotelera de Cancun, San Pedro Garza Garcia) mediante poligonos vectoriales:
* **Arquetipos Urbanos:** `cbd` (2.5x, bono alcance 0.40), `tourism` (2.2x, bono 0.35), `industrial` (1.8x, bono 0.25), `commercial` (1.4x, bono 0.15) o `custom`.
* **Modulacion de Friccion:** Bono de alcance ($\text{reach\_bonus} \in [0.0, 0.60]$) que reduce $\beta_j = \beta (1 - \text{reach\_bonus})$ con salvaguarda de piso de retencion local ($d \le 3\text{ km}$).
* **Regla Canonica MAX Priority:** Resuelve solapamientos espaciales de forma determinista y preserva intactas las cuotas de los POIs especiales contenidos.

### 4. Zonas Topologicas Aisladas (`isolated_zones`)
Tratamiento matematicamente estanco para conurbaciones costeras con islas habitadas sin conexion vial terrestre (ej. Isla Mujeres o Cozumel), eliminando la generacion de trayectos imposibles en automovil sobre el oceano.

### 5. Laboratorio Experimental de Competitividad Modal (`modal_experiment`)
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
[1. Proyectos]    Gestion aislada de carpetas data/<ciudad>/ y dist/<ciudad>/.
[2. Datos]        Validacion automatica y subida de la Cuatrifecta del INEGI.
[3. BBOX/CONAPO]  Calibracion interactiva con 4 tiradores (NW, NE, SE, SW) y auditoria CONAPO.
[4. POI Studio]   Editor geoespacial unificado de POIs y Zonas de Alta Afluencia en mapa satelital.
[5. Toponimia]    Extraccion inteligente de colonias, barrios y vialidades principales.
[6. Compilacion]  Ejecucion integral del pipeline con streaming de telemetria en vivo (SSE).
[7. Visor]        Inspeccion interactiva de densidades residenciales y de empleo en WebGL.
```

Si prefieres editar POIs de forma standalone en mapa satelital:
```bash
python tools/poi_studio.py --city cities/cancun.yaml
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
\-- cancun/                          # Microdatos especificos de la metropoli
    |-- RESAGEBURB_23CSV20.csv       # Censo CPV 2020 a nivel manzana (o subcarpeta conjunto_de_datos)
    |-- denue_inegi_23_.csv          # DENUE estatal
    |-- SAIC_Exporta_23.csv          # Censos Economicos 2024 (H001A municipal)
    \-- ENOE_2026_Entidad_23.csv     # Indicadores estrategicos ENOE
```

> Consulta la [Guia de Fuentes de Datos (DATA_SOURCES.md)](DATA_SOURCES.md) para enlaces directos de descarga gratuita de cualquier estado en 3 minutos.

### 3. Disenar la Ciudad en YAML

Crea o edita el archivo declarativo en `cities/<ciudad>.yaml` basandote en [`cities/_template.yaml`](cities/_template.yaml):

```yaml
city:
  code: "CUN"
  name: "Cancun"
  description: "Zona Metropolitana de Cancun y Riviera Maya"
  bbox: [-87.2369, 20.8254, -86.6615, 21.4095]
  grid_size: 0.0018
  include_ocean: true

macroeconomics:
  tasa_pea: 0.665
  til_1_state: 0.450
  gravity_beta: 0.12
  target_pop_size: 180

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
python build.py cities/cancun.yaml

# Solo regeneracion de demanda (si ya tienes los mapas compilados):
python build.py cities/cancun.yaml --skip-map
```

### 5. Importar en el Juego

El compilador genera de forma estanca el paquete listo para importar en:
`dist/<ciudad>/<CODIGO>.zip` (ejemplo: `dist/cancun/CUN.zip`).

1. Abre **Subway Builder** o **Subway Builder Modded**.
2. Entra a **Kronifer's Map Manager / Railyard**.
3. Haz clic en **ADD A MAP** y selecciona el archivo `.zip`.
4. Disena tu red metropolitana de trenes sobre datos reales y balanceados.

---

## Estructura del Repositorio

```text
cities/                  # Archivos de configuracion por metropoli (.yaml)
|-- _template.yaml       # Plantilla maestra documentada v7.1
|-- cancun.yaml          # Configuracion de Cancun / Quintana Roo
\-- merida.yaml          # Configuracion de Merida / Yucatan
sb_mexico/               # Nucleo algoritmico del motor
|-- inegi.py             # Ingesta, jerarquia censal MGM, multi-archivo y CONAPO
|-- gravity.py           # Furness IPFP, dos capas, affluence zones, snapping y modal lab
|-- osrm.py              # Microservicio OSRM (car.lua), supervisor WSL 2 y fallback Colin
|-- special_demand.py    # Validacion y exportacion de demanda especial (Taxonomia v5)
|-- toponymy.py          # Extractor toponimico para OSM XML/PBF
|-- cartography.py       # Wrapper de depot.maps.MapGen y puente WSL 2
|-- cartography_runner.py# Runner de compilacion cartografica nativa en Linux / WSL 2
|-- pipeline.py          # Orquestador del flujo completo, validaciones y ZIP
\-- schemas/             # Esquemas JSON oficiales de validacion de demanda especial
tests/                   # Suite formal de pruebas unitarias (114 tests)
tools/                   # Herramientas de visualizacion y diseno interactivo
|-- wizard.py            # Servidor web del asistente integral (con POI Studio en Paso 4)
|-- poi_studio.py        # Editor visual standalone de POIs y calibrador satelital
|-- preview_toponymy.py  # Visor geoespacial de capas toponimicas
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
* [**Estandares de Calidad S-Tier (.agents/rules/subway_builder_standards.md)**](.agents/rules/subway_builder_standards.md): Las 12 reglas maestras de rigor algoritmico, inmunidad de codificacion UTF-8 y arquitectura de microservicios.

---

## Garantia de Calidad y Pruebas Unitarias

El proyecto cuenta con una suite rigurosa de **114 pruebas unitarias automatizadas** que validan:
* Conservacion estricta de masa ($\Delta = 0$).
* Balanceo bidireccional de Furness / IPFP y pisos de retencion local.
* Modulacion y regla *MAX Priority* en Zonas de Alta Afluencia.
* Ruteo OSRM, renovacion de sockets Keep-Alive y fallback canonico de Colin.
* Inmunidad absoluta contra falsos positivos de codificacion UTF-8 sin BOM (`tests/test_encoding.py`).

Para ejecutar la suite de pruebas:
```bash
python -m unittest discover tests
```

---

## Licencia y Creditos

* **Licencia:** Distribuido bajo la [Licencia MIT](LICENSE).
* **Motor de Simulacion Base:** *Subway Builder* por **Colin Miller**.
* **Variante Avanzada:** *Subway-Builder-Modded* por **Kronifer**.
* **Fuentes Estadisticas:** [INEGI](https://www.inegi.org.mx/) (CPV 2020, DENUE, Censos Economicos, ENOE) y [CONAPO](https://datos.gob.mx/) (Proyecciones de Poblacion).
* **Datos Cartograficos:** Colaboradores de [OpenStreetMap](https://www.openstreetmap.org/) y extractos de [Geofabrik](https://download.geofabrik.de/).
* **Inspiracion Grafica:** Sistema de Senaletica del Metro de la Ciudad de Mexico por **Lance Wyman** (1969).
