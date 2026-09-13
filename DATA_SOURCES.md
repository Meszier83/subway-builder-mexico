# Guia de Fuentes de Datos Oficiales (Mexico)

Para modelar cualquier ciudad o zona metropolitana de Mexico con Subway Builder Mexico (v7.1), el motor procesa fuentes de datos abiertas y gratuitas del INEGI, CONAPO y OpenStreetMap (red cartográfica OSM, microdatos censales CPV 2020, DENUE, Censos Económicos CE 2024, ENOE, proyecciones CONAPO y opcionalmente el Marco Geoestadístico Nacional MGM).

---

## 1. Jerarquia de Almacenamiento y Aislamiento por Proyecto (Regla 9)

Para garantizar la reproducibilidad y prevenir colisiones entre proyectos urbanos independientes (*Project Bubble Isolation*), los datos deben distribuirse de acuerdo con su alcance territorial:

```text
data/                                # Datasets de escala nacional
|-- mexico-latest.osm.pbf            # Extracto OSM completo de la Republica Mexicana
|-- proyecciones_conapo_2020_2053.csv# Tabulado nacional de proyecciones demograficas
|-- cancun_riviera_maya/             # Microdatos para Cancun / Riviera Maya (CUR)
|   |-- RESAGEBURB_23CSV20.csv       # Censo CPV 2020 de Quintana Roo (o subcarpeta conjunto_de_datos)
|   |-- denue_inegi_23_.csv          # DENUE estatal de Quintana Roo
|   |-- SAIC_Exporta_23.csv          # Censo Economico 2024 (H001A)
|   \-- ENOE_2026_Entidad_23.csv     # Indicadores estrategicos ENOE
|-- ciudad_de_mexico/                # Microdatos para ZMVM (CDMX, EdoMex, Hidalgo)
|   |-- RESAGEBURB_09CSV20.csv       # Censo CPV 2020 CDMX
|   |-- RESAGEBURB_15CSV20.csv       # Censo CPV 2020 Estado de Mexico
|   |-- denue_inegi_09_.csv          # DENUE CDMX
|   \-- denue_inegi_15_.csv          # DENUE Estado de Mexico
|-- merida/                          # Microdatos aislados para Merida (MID)
|   |-- RESAGEBURB_31CSV20.csv       # Censo CPV 2020 de Yucatan
|   |-- denue_inegi_31_.csv          # DENUE estatal de Yucatan
|   |-- SAIC_Exporta_31.csv          # Censo Economico 2024 (H001A)
|   \-- ENOE_2026_Entidad_31.csv     # Indicadores estrategicos ENOE
\-- saltillo/                        # Microdatos aislados para Saltillo (SAL)
    |-- RESAGEBURB_05CSV20.csv       # Censo CPV 2020 de Coahuila
    \-- denue_inegi_05_.csv          # DENUE estatal de Coahuila
```

Las salidas compiladas finales y paquetes ZIP listos para importar se generan estrictamente en `dist/<ciudad>/` (ej. `dist/cancun_riviera_maya/CUR.zip` o `dist/ciudad_de_mexico/CDMX.zip`). El motor incluye soporte y resolucion retrocompatible transparente si se utiliza la carpeta `data/cancun/`.

---

## 2. Inventario de Fuentes Oficiales

### 2.1. Cartografia: OpenStreetMap (PBF de Mexico)
* **Fuente:** Geofabrik OpenStreetMap Data Extracts.
* **Enlace:** http://download.geofabrik.de/central-america/mexico.html
* **Archivo:** `mexico-latest.osm.pbf` (colocar directamente en la raiz de `data/`).
* **Nota de Compilacion:** Si el archivo es grande (> 50 MB), el pipeline ejecuta un recorte automatico al BBOX metropolitano con `osmium extract` antes de compilar con Planetiler o OSRM. Ademas, permite zonificacion concentrica (`urban_core_polygon`) para preservar calles secundarias en la mancha urbana, suprimir etiquetas de lugares en la periferia exterior (`patch_urban_core_labels` / `apply_urban_lod_filtering`) y suprimir macro-reservas rurales (`urban_parks_only: true`).

### 2.2. Poblacion y Vivienda: Censo CPV 2020 a Nivel Manzana
* **Fuente:** INEGI - Censo de Poblacion y Vivienda 2020 (Resultados por AGEB y Manzana Urbana).
* **Enlace:** https://www.inegi.org.mx/programas/ccpv/2020/#microdatos
* **Archivo:** `RESAGEBURB_*.csv` o `.xlsx` (ej. `RESAGEBURB_23CSV20.csv` para Quintana Roo).
* **Estructura Interna:** El motor soporta tanto el archivo CSV suelto como la estructura descomprimida del ZIP del INEGI que contiene la subcarpeta `conjunto_de_datos/RESAGEBURB_*.csv`.

### 2.3. Directorio Economico y Empleo: DENUE
* **Fuente:** INEGI - DENUE Descarga Masiva por Entidad Federativa.
* **Enlace:** https://www.inegi.org.mx/app/descarga/
* **Archivo:** `denue_inegi_*.csv` (ej. `denue_inegi_23_.csv`). Contiene coordenadas GPS puntuales y estratos de personal ocupado (`per_ocu`).

### 2.4. Control Municipal de Empleo: Censos Economicos 2024 (SAIC)
* **Fuente:** INEGI - Sistema Automatizado de Informacion Censal (SAIC) o microdatos CE 2024.
* **Enlace:** https://www.inegi.org.mx/app/saic/
* **Archivo:** Exportar consulta CSV de la variable `H001A` (Personal ocupado total) a nivel municipal (ej. `SAIC_Exporta_*.csv` o `*tr_ce*.csv`).

### 2.5. Tasa de Actividad e Informalidad: ENOE Trimestral
* **Fuente:** INEGI - Encuesta Nacional de Ocupacion y Empleo (Indicadores Estrategicos).
* **Enlace:** https://www.inegi.org.mx/programas/enoe/15ymas/
* **Archivo:** `*_Entidad_*.csv` o `.xlsx`. Provee la Tasa de Participacion Laboral (Tasa PEA) y la Tasa de Informalidad Laboral ($TIL_1$).

### 2.6. Proyeccion Demografica Intercensal: CONAPO (2020 -> 2026+)
* **Fuente:** CONAPO (Consejo Nacional de Poblacion) - Proyecciones de la Poblacion de los Municipios de Mexico 2020-2050 (o 2020-2053).
* **Enlace Oficial:** https://datos.gob.mx/busca/dataset/proyecciones-de-la-poblacion-de-mexico-y-de-las-entidades-federativas-2020-2050
* **Archivo:** `*conapo*.csv` o `data-*.csv` (colocar en `data/` o en `data/<ciudad>/`).
* **Importancia:** Sincroniza la poblacion del Censo 2020 con los censos economicos contemporaneos:
  $$\text{growth\_factor}_m = \frac{\text{Poblacion Proyectada CONAPO (Ano Actual)}_m}{\text{Poblacion Censo CPV 2020}_m}$$
  * *Ejemplo Benito Juarez (Cancun, 23005):* $\frac{968{,}000 \text{ hab. (2026)}}{904{,}684 \text{ hab. (2020)}} = 1.07$ (+7.0%).
  * *Auditoria en Wizard (Paso 3):* La interfaz grafica desglosa automaticamente el nombre oficial del municipio, el ano detectado (`ANO`), la poblacion base 2020, la poblacion proyectada CONAPO (`POB_MIT_MUN`) y el factor resultante para validacion visual antes de compilar.

### 2.7. Georreferenciacion Vectorial Oficial: Marco Geoestadistico Nacional (MGM - Opcional)
* **Fuente:** INEGI - Marco Geoestadistico Nacional (Capa Vectorial de Manzanas y AGEBs Urbanos).
* **Enlace:** https://www.inegi.org.mx/temas/mg/
* **Archivos Soportados:** Shapefile (`.shp`), GeoJSON (`.geojson`) o GeoPackage (`.gpkg`) con atributos `CVE_ENT`, `CVE_MUN`, `CVE_AGEB`, `CVE_MZA`. Colocar en `data/<ciudad>/`.
* **Rol en el Motor:** Constituye el **Nivel 0 (Oficial)** de la cascada jerarquica de georreferenciacion censal (`load_marco_geoestadistico_coords`). Provee los centroides poligonales exactos de cada manzana censal antes del fallback baricentrico comercial del DENUE.

---

## 3. Zonas Metropolitanas Interestatales y Fuentes Multi-Archivo (Regla 7)

Para metrópolis que abarcan dos o más entidades federativas (ej. Valle de Mexico [CDMX, EdoMex, Hidalgo], La Laguna [Coahuila, Durango], Puebla-Tlaxcala o Puerto Vallarta-Bahia de Banderas [Jalisco, Nayarit]):

1. **Colocacion de Archivos:** Deposita los archivos censales y de DENUE de todos los estados involucrados dentro de la carpeta `data/<ciudad>/`.
   * Ej. para La Laguna: `RESAGEBURB_05*.csv` (Coahuila) y `RESAGEBURB_10*.csv` (Durango); `denue_inegi_05*.csv` y `denue_inegi_10*.csv`.
2. **Concatenacion y Deduplicacion en Memoria:** El motor detecta multiples archivos por patron, los concatena en un solo DataFrame y ejecuta un filtrado espacial estricto contra el BBOX metropolitano.
3. **Cero Duplicacion de Masa:** Se garantiza que cada manzana censal y cada establecimiento economico se contabilice exactamente una sola vez.
