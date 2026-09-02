# MANUAL MAESTRO: SUBWAY BUILDER MÉXICO (v6.3)
**Arquitectura de Compilación y Modelado de Demanda Geoespacial (INEGI & Depot)**

---

## 1. INTRODUCCIÓN Y FILOSOFÍA v6.3

La versión 6.3 amplía la arquitectura declarativa y automatizada con la suite visual interactiva Wizard Studio (estilo Metro CDMX / Wyman), streaming SSE de compilación, módulos de toponimia enriquecida, validación de demanda especial y resolución multi-fuente:

1. **Un solo comando o Wizard Web:** La compilación cartográfica, el cruce censal, el modelo gravitatorio, la toponimia y el empaquetado se ejecutan de inicio a fin desde CLI o interfaz web interactiva:
   ```bash
   python build.py cities/cancun.yaml
   # O mediante el Wizard Visual:
   python tools/wizard.py
   ```
2. **Modelo Gravitatorio Puro y Absorción Argmin:** Implementación de asignación multinomial directa con conservación matemática estricta de la PEA a priori y absorción de establecimientos DENUE por mínima distancia espacial esferoidal.
3. **Toponimia y Taxonomía Modular:** Generación de archivos toponímicos OSM y `special_demand_points.json` validados contra el estándar oficial de Subway Builder Modded.
4. **Wizard Studio & POI Studio:** Servidores web locales con calibración de BBOX, edición de radios de absorción, subida de archivos y visualización en tiempo real.

---

## 2. ESTRUCTURA DEL PROYECTO

```text
MANUAL SB/
├── cities/                          # Archivos de configuración de cada ciudad (.yaml)
│   ├── _template.yaml               # Plantilla maestra documentada
│   ├── cancun.yaml                  # Configuración de Cancún / Q. Roo
│   └── ...                          # Tus próximas ciudades
├── sb_mexico/                       # Motor de procesamiento en Python
│   ├── inegi.py                     # Ingesta y conciliación de CPV, DENUE, CE 2024, ENOE y CONAPO
│   ├── gravity.py                   # Modelo de fricción β=0.12 y asignación multinomial con argmin
│   ├── special_demand.py            # Generación y validación de demanda especial (Taxonomía v5)
│   ├── toponymy.py                  # Extractor y conversor de toponimia urbana a OSM XML/PBF
│   ├── cartography.py               # Generador cartográfico con depot.maps.MapGen
│   └── pipeline.py                  # Orquestador del flujo completo y autovalidaciones
├── tools/                           # Herramientas de visualización y diseño
│   ├── wizard.py                    # Servidor web del asistente integral interactivo
│   ├── poi_studio.py                # Visualizador y editor interactivo de POIs (Leaflet)
│   └── preview_toponymy.py          # Visor geoespacial de capas toponímicas
├── build.py                         # CLI ejecutable
├── wizard.bat                       # Lanzador directo para Windows
└── *.csv / *.osm.pbf                # Datos fuente del INEGI y OpenStreetMap
```

---

## 3. FUENTES DE DATOS DEL INEGI (LA CUATRIFECTA)

El motor detecta y procesa automáticamente los archivos oficiales que coloques en el directorio de trabajo:

1. **CPV 2020 (`*RESAGEBURB*.csv` o `*censo*.csv/.xlsx`):**  
   Población y personas de 15 años y más (`P_15YMAS`) a nivel de manzana urbana (`MZA > 0`). Maneja el secreto estadístico (`*`) de forma automática.
2. **DENUE (`*denue*.csv`):**  
   Directorio georreferenciado de unidades económicas (coordenadas GPS puntuales y estratos de personal ocupado).
3. **Censos Económicos 2024 (`*SAIC*.csv` o `*cenu24*.csv`):**  
   Control total de personal ocupado municipal (`H001A`) para calibrar asimétricamente los micronegocios.
4. **ENOE (`*2026_trim*.csv` o `*enoe*.csv`):**  
   Tasa de participación laboral (Tasa PEA) y tasa de informalidad laboral (`TIL_1`).
5. **OpenStreetMap (`*.osm.pbf`):**  
   Geometrías de vialidades, edificios 3D, costas y toponimia (descargado de Geofabrik).

---

## 4. FLUJO DE TRABAJO: CÓMO CREAR UNA CIUDAD NUEVA

### Paso 1: Pedirle a la IA el archivo `.yaml` de la ciudad
Cuando quieras mapear una nueva ciudad (ej. Querétaro, Monterrey, Guadalajara), simplemente pídele a la IA:
> *"Quiero hacer el mapa de Guadalajara. Genérame su archivo `guadalajara.yaml` con su BBOX metropolitano, códigos municipales del INEGI, tasas de la ENOE/CE2024 de Jalisco y sus 10 POIs más importantes (Aeropuerto, Universidades, Parques Industriales, Plazas).*

### Paso 2: Guardar y Ejecutar
Guarda el archivo en `cities/guadalajara.yaml` y ejecuta en tu terminal de WSL / Linux:
```bash
# Compilación completa (Mapa + Demanda + ZIP)
python build.py cities/guadalajara.yaml

# O si solo modificaste los POIs / factores y ya tienes el mapa compilado:
python build.py cities/guadalajara.yaml --skip-map
```

### Paso 3: Importar en el Juego
El script genera automáticamente el archivo `<CODIGO_CIUDAD>.zip` en tu carpeta.
1. Abre **Kronifer's Map Manager / Railyard**.
2. Selecciona **ADD A MAP** y carga el archivo ZIP.
3. ¡Listo para diseñar tu red de metro!

---

## 5. PARÁMETROS DEL ARCHIVO YAML

| Sección | Parámetro | Descripción / Valor Recomendado |
| :--- | :--- | :--- |
| `city` | `code` | Código IATA o sigla única de 3 letras (ej. `GDL`, `MTY`, `CUN`). |
| `city` | `bbox` | `[min_lon, min_lat, max_lon, max_lat]` del área metropolitana. |
| `city` | `grid_size` | Tamaño de celda de demanda: `0.0018` (fino), `0.0025` (estándar), `0.0035` (metrópolis gigante). |
| `city` | `building_filter_size` | Filtro de edificios 3D para `MapGen` (default `15.0` para optimizar FPS). |
| `macroeconomics` | `tasa_pea` | Tasa de participación laboral sobre población 15+ (ej. `0.6644`). |
| `macroeconomics` | `til_1_state` | Tasa de informalidad laboral estatal (ej. `0.4497`). |
| `macroeconomics` | `gravity_beta` | Coeficiente de decaimiento por distancia (default `0.12`). |
| `macroeconomics` | `max_pop_size` | Tamaño máximo de cohorte de viaje (default `150` pax para fluidez en el juego). |
| `pois` | `id` | Nombre del nodo. **Prefijos obligatorios:** `AIR_*` (Aeropuertos, 24/7 dampening=0.5), `UNI_*` (Universidades, horario estudiantil dampening=0.3), `SPO_*`, `TOU_*`, `MED_*`. |
| `pois` | `loc` | `[lon, lat]` exacto del punto de interés sobre la malla urbana. |
| `pois` | `jobs` | Cantidad de empleos o capacidad de atracción del nodo. |
| `pois` | `radius_m` | Radio de absorción en metros (`2000-3000m` para aeropuertos, `800-1200m` para universidades, `500-800m` para anclas en clusters). |
| `pois` | `mode` | `MAX` (recomendado: garantiza cuota absorbiendo DENUE local), `BOOST` (suma exógena), `REPLACE` (sobrescritura). |

> [!TIP]
> **Regla de Clusters Comerciales (ej. Zona Hotelera o Paseo de la Reforma):**
> Nunca crees un solo mega-POI para todo un corredor lineal, ya que concentrará miles de empleos en una sola estación dejando el resto del corredor desierto. Deja que el DENUE distribuya los empleos orgánicamente a lo largo de las avenidas, o crea POIs de anclaje puntuales con `radius_m: 500-800m`.

---

## 6. FORMULACIÓN MATEMÁTICA INTERNA (REFERENCIA)

1. **Fricción Espacial:**
   $$f(d_{ij}) = e^{-\beta \cdot d_{ij}} \quad \text{donde } \beta = 0.12, \ d_{ij} \le 55\text{ km}$$
2. **Atracción y Probabilidad O-D:**
   $$P_{ij} = \frac{(E_j)^{0.85} \cdot f(d_{ij})}{\sum_{k} (E_k)^{0.85} \cdot f(d_{ik})}$$
3. **Distribución Multinomial de Viajes (Conservación Estricta de Masa):**
   $$\mathbf{Viajes}_i \sim \text{Multinomial}\left( \text{PEA}_i, \ [P_{i1}, P_{i2}, \dots, P_{in}] \right)$$
   Garantiza que $\sum \text{Viajeros} \equiv \sum \text{PEA}$ sin artefactos de inflado ni rellenos.
