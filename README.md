# Subway Builder México (v6.0) 🚇🇲🇽
**Pipeline Integral, Declarativo y Riguroso para Generación de Mapas de México**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Engine: Subway Builder](https://img.shields.io/badge/engine-Subway%20Builder-orange.svg)](https://subwaybuilder.com/)
[![Data: INEGI & OSM](https://img.shields.io/badge/data-INEGI%202024%20%7C%20OSM-green.svg)](https://www.inegi.org.mx/)
[![Standard: S--Tier Gold Standard](https://img.shields.io/badge/standard-Gold%20Standard-gold.svg)]()

sb_mexico es un compilador automatizado de alta fidelidad que transforma datos geoestadísticos abiertos del **INEGI** (Censo CPV 2020, DENUE, Censos Económicos 2024, ENOE) y de **OpenStreetMap** en paquetes oficiales listos para jugar en **Subway Builder** (.zip).

---

## 🌟 Características Principales

* **Declarativo y Sencillo:** Define tu metrópoli completa en un archivo YAML de 30 líneas en cities/<ciudad>.yaml.
* **Conservación Estricta de Masa:** $\sum \text{Pasajeros Activos} \equiv \sum \text{PEA Censal}$. Cero inflación artificial y cero pasajeros fantasma.
* **Modelo de Demanda en Dos Capas:**
  * **Capa Especial:** Aeropuertos (AIR_), Universidades (UNI_) y Estadios reciben el **100% exacto de su cuota de diseño**.
  * **Capa Regular:** Muestreo Multinomial Gravitatorio puro para comercios, oficinas e industrias.
* **Calibración Asimétrica de Empleo:** Reconcilia el empleo formal de grandes empresas y ajusta el sector informal de micro-negocios usando los **Censos Económicos 2024 ($)** y la **Tasa de Informalidad Laboral (ENOE)**.
* **Física de Tráfico y Congestión:** Curva no lineal de velocidad (\text{ km/h}$ en el centro urbano hasta \text{ km/h}$ en autopistas) para garantizar una competencia modal realista entre el auto y el metro.
* **Toponimia y Cartografía Mexicana:** Calles, avenidas, colonias, fraccionamientos, edificios 3D y batimetría marina procesados automáticamente.
* **Cámara Inteligente:** Centrado automático del viewport en el **Baricentro Urbano de Población y Empleo**.

---

## 🚀 Inicio Rápido (Quickstart)

### 1. Clonar e Instalar Dependencias
`ash
git clone https://github.com/tu-usuario/subway-builder-mexico.git
cd subway-builder-mexico
pip install -r requirements.txt
`

### 2. Colocar los Datos del INEGI y OSM
Descarga los 4 archivos estadísticos del estado y el PBF de México (consulta la [Guía de Descarga de Datos](DATA_SOURCES.md)):
1. mexico-latest.osm.pbf (Geofabrik)
2. RESAGEBURB_*.csv (Censo de Población y Vivienda 2020)
3. denue_inegi_*.csv (Directorio Estadístico Nacional de Unidades Económicas)
4. SAIC_Exporta_*.csv (Censos Económicos 2024 - SAIC)
5. *_Entidad_*.csv (ENOE - Indicadores Estratégicos)

### 3. Compilar la Ciudad
`ash
# Compilación completa (Cartografía + Demanda)
python3 build.py cities/cancun.yaml

# Si ya compilaste el mapa y solo quieres ajustar la demanda:
python3 build.py cities/cancun.yaml --skip-map
`

¡Listo! Encontrarás tu archivo listo para jugar en ./CUN.zip.

---

## 📁 Estructura del Proyecto

`	ext
├── cities/                  # Archivos de configuración por ciudad (YAML)
│   ├── _template.yaml       # Plantilla documentada
│   └── cancun.yaml          # Configuración de Cancún / Riviera Norte
├── sb_mexico/               # Núcleo del motor
│   ├── inegi.py             # Ingesta, geocodificación y calibración INEGI
│   ├── gravity.py           # Malla espacial, POIs, dos capas y congestión
│   ├── cartography.py       # Wrapper optimizado de depot.maps.MapGen
│   └── pipeline.py          # Orquestador integral y empaquetado
├── build.py                 # CLI de ejecución por línea de comandos
├── DATA_SOURCES.md          # Dónde y cómo descargar los datos oficiales
├── METHODOLOGY.md           # Justificación matemática y científica
└── requirements.txt         # Dependencias de Python
`

---

## 📖 Documentación Adicional

* [**Guía de Fuentes de Datos (DATA_SOURCES.md)**](DATA_SOURCES.md): Enlaces oficiales y pasos para descargar los datos de cualquier estado de México en 3 minutos.
* [**Libro Blanco y Metodología (METHODOLOGY.md)**](METHODOLOGY.md): Formulación matemática de las ecuaciones de gravedad, calibración laboral y comparación con el estándar Vanilla de EE.UU.

---

## 📄 Licencia
Distribuido bajo la Licencia MIT. Desarrollado con ❤️ para la comunidad de Subway Builder y entusiastas de la planeación de transporte en México.
