# Guía de Fuentes de Datos Oficiales (México) 🇲🇽

Para generar cualquier ciudad o zona metropolitana de México, el motor requiere 5 fuentes de datos abiertas y gratuitas del INEGI y OpenStreetMap.

---

## 1. Cartografía: OpenStreetMap (PBF de México)
* **Fuente:** Geofabrik OpenStreetMap Extracts
* **Enlace:** http://download.geofabrik.de/central-america/mexico.html
* **Archivo:** mexico-latest.osm.pbf (sirve para todo el país).

## 2. Población y Vivienda: Censo CPV 2020 a Nivel Manzana
* **Fuente:** INEGI - Censo de Población y Vivienda 2020 (Resultados por AGEB y Manzana Urbana).
* **Enlace:** https://www.inegi.org.mx/programas/ccpv/2020/#microdatos
* **Archivo:** RESAGEBURB_*.csv (ej. RESAGEBURB_23CSV20.csv para Quintana Roo).

## 3. Directorio Económico y Empleo: DENUE
* **Fuente:** INEGI - DENUE Descarga Masiva por Entidad Federativa.
* **Enlace:** https://www.inegi.org.mx/app/descarga/
* **Archivo:** denue_inegi_*.csv (ej. denue_inegi_23_.csv).

## 4. Benchmarks Municipales de Empleo: Censos Económicos 2024 (SAIC)
* **Fuente:** INEGI - Sistema Automatizado de Información Censal (SAIC).
* **Enlace:** https://www.inegi.org.mx/app/saic/
* **Archivo:** Exportar consulta CSV de H001A Personal ocupado total a nivel municipal (ej. SAIC_Exporta_*.csv).

## 5. Tasa de Actividad e Informalidad: ENOE Trimestral
* **Fuente:** INEGI - Encuesta Nacional de Ocupación y Empleo (Indicadores Estratégicos).
* **Enlace:** https://www.inegi.org.mx/programas/enoe/15ymas/
* **Archivo:** *_Entidad_*.csv.
