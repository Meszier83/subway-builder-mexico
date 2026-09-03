# Subway Builder México (v6.3) 
**Pipeline Integral, Declarativo y Riguroso para Generación de Mapas de México**

---

## Características Principales

* **Declarativo y Sencillo:** Define tu metrópoli en un archivo YAML de 30 líneas en cities/<ciudad>.yaml.
* **Conservación Estricta de Masa:** Suma de viajeros activos = PEA del Censo (cero inflación artificial).
* **Modelo de Demanda en Dos Capas:**
  * **Capa Especial:** Aeropuertos (AIR_), Universidades (UNI_) y Estadios reciben el **100% exacto de su cuota**.
  * **Capa Regular:** Muestreo Multinomial Gravitatorio puro para comercios, oficinas e industrias.
* **Calibración Asimétrica de Empleo:** Ajusta el sector informal de micro-negocios usando los **Censos Económicos 2024 (H001A)** y la **Tasa de Informalidad Laboral (ENOE)**.
* **Física de Tráfico y Congestión:** Curva no lineal de velocidad (18 km/h en centro urbano hasta 65 km/h en autopistas).
* **Toponimia y Cartografía Mexicana:** Calles, colonias, fraccionamientos, edificios 3D y batimetría marina.
* **Cámara Inteligente:** Centrado automático del viewport en el **Baricentro Urbano de Población y Empleo**.

---

## Inicio Rápido (Quickstart)

### 1. Clonar e Instalar Dependencias

```bash
git clone https://github.com/Meszier83/subway-builder-mexico.git
cd subway-builder-mexico
pip install -r requirements.txt
```

### 2. Colocar los Datos del INEGI y OSM
Descarga los 4 archivos estadísticos del estado y el PBF de México (ver [DATA_SOURCES.md](DATA_SOURCES.md)):
1. `mexico-latest.osm.pbf` (Geofabrik)
2. `RESAGEBURB_*.csv` (Censo CPV 2020)
3. `denue_inegi_*.csv` (DENUE)
4. `SAIC_Exporta_*.csv` o `*tr_ce*.csv` (Censos Económicos 2024 - SAIC)
5. `*_Entidad_*.csv` (ENOE)
6. `*conapo*.csv` o `data-*.csv` (Proyecciones de Población CONAPO)

### 3. Asistente Visual Integral (Wizard Studio) o POI Studio
Puedes iniciar el asistente gráfico completo con interfaz inspirada en la señalética del Metro de la CDMX (soporte para subida de archivos, calibración de BBOX, toponimia y compilación en vivo con streaming):

```bash
# Iniciar el Wizard Integral
python tools/wizard.py
# o en Windows haciendo doble clic en wizard.bat
```

O si solo deseas inspeccionar y calibrar radios de POIs en mapa satelital:
```bash
python tools/poi_studio.py --city cities/cancun.yaml
```

### 4. Compilar la Ciudad por CLI

```bash
# Compilación completa (Cartografía + Demanda)
python build.py cities/cancun.yaml

# Solo demanda (si ya tienes el mapa):
python build.py cities/cancun.yaml --skip-map
```

¡Listo! El paquete final se genera en `CUN.zip` (o en `--output-dir dist/cancun/`).

---

## Estructura del Proyecto

```text
cities/                  # Archivos de configuración por ciudad (YAML)
|-- _template.yaml       # Plantilla documentada
\-- cancun.yaml          # Configuración de Cancún
sb_mexico/               # Núcleo del motor
|-- inegi.py             # Ingesta, geocodificación y calibración INEGI/CONAPO
|-- gravity.py           # Malla espacial, POIs, dos capas y asignación multinomial
|-- special_demand.py    # Validación y exportación de demanda especial (Taxonomía v5)
|-- toponymy.py          # Extracción y filtrado toponímico para OSM XML/PBF
|-- cartography.py       # Wrapper de depot.maps.MapGen y puente WSL 2
|-- cartography_runner.py# Runner de compilación cartográfica nativa para WSL/Linux
\-- pipeline.py          # Orquestador integral y empaquetado
tests/                   # Suite formal de pruebas unitarias
tools/                   # Utilidades y scripts auxiliares
|-- wizard.py            # Servidor web del asistente integral interactivo
|-- poi_studio.py        # Editor visual de POIs y calibrador en mapa satelital
|-- preview_toponymy.py  # Visor geoespacial de capas toponímicas
\-- demo_preview.py      # Generador rápido de vistas previas
build.py                 # CLI de ejecución principal
wizard.bat               # Lanzador directo de Wizard en Windows
visualize.py             # Visor HTML interactivo de demanda
DATA_SOURCES.md          # Dónde y cómo descargar datos oficiales
METHODOLOGY.md           # Justificación matemática y científica
requirements.txt         # Dependencias
```

---

## Documentación

* [**Guía de Fuentes de Datos (DATA_SOURCES.md)**](DATA_SOURCES.md): Enlaces y pasos para descargar datos de cualquier estado en 3 minutos.
* [**Libro Blanco y Metodología (METHODOLOGY.md)**](METHODOLOGY.md): Formulación matemática y justificación técnica.
* [**Manual Maestro (MANUAL_MAESTRO_v6.3.md)**](MANUAL_MAESTRO_v6.3.md): Guía de referencia paso a paso.

---

## Licencia
Distribuido bajo la Licencia MIT.
