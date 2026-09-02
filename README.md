# Subway Builder México (v6.0) 🚇🇲🇽
**Pipeline Integral, Declarativo y Riguroso para Generación de Mapas de México**

---

## 🌟 Características Principales

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

## 🚀 Inicio Rápido (Quickstart)

### 1. Clonar e Instalar Dependencias
`ash
git clone https://github.com/Meszier83/subway-builder-mexico.git
cd subway-builder-mexico
pip install -r requirements.txt
`

### 2. Colocar los Datos del INEGI y OSM
Descarga los 4 archivos estadísticos del estado y el PBF de México (ver [DATA_SOURCES.md](DATA_SOURCES.md)):
1. mexico-latest.osm.pbf (Geofabrik)
2. RESAGEBURB_*.csv (Censo CPV 2020)
3. denue_inegi_*.csv (DENUE)
4. SAIC_Exporta_*.csv (Censos Económicos 2024 - SAIC)
5. *_Entidad_*.csv (ENOE)

### 3. Compilar la Ciudad
`ash
# Compilación completa (Cartografía + Demanda)
python3 build.py cities/cancun.yaml

# Solo demanda (si ya tienes el mapa):
python3 build.py cities/cancun.yaml --skip-map
`

¡Listo! El paquete final se genera en ./CUN.zip.

---

## 📁 Estructura del Proyecto

`
├── cities/                  # Archivos de configuración por ciudad (YAML)
│   ├── _template.yaml       # Plantilla documentada
│   └── cancun.yaml          # Configuración de Cancún
├── sb_mexico/               # Núcleo del motor
│   ├── inegi.py             # Ingesta, geocodificación y calibración INEGI
│   ├── gravity.py           # Malla espacial, POIs, dos capas y congestión
│   ├── cartography.py       # Wrapper de depot.maps.MapGen
│   └── pipeline.py          # Orquestador integral y empaquetado
├── build.py                 # CLI de ejecución
├── DATA_SOURCES.md          # Dónde y cómo descargar datos oficiales
├── METHODOLOGY.md           # Justificación matemática y científica
└── requirements.txt         # Dependencias
`

---

## 📖 Documentación

* [**Guía de Fuentes de Datos (DATA_SOURCES.md)**](DATA_SOURCES.md): Enlaces y pasos para descargar datos de cualquier estado en 3 minutos.
* [**Libro Blanco y Metodología (METHODOLOGY.md)**](METHODOLOGY.md): Formulación matemática y justificación técnica.

---

## 📄 Licencia
Distribuido bajo la Licencia MIT.
