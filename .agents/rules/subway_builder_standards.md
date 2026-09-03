---
trigger: always_on
description: Reglas y estándares de modelación para Subway Builder México y POI Studio
---

# Estándares de Modelación - Subway Builder México

Al trabajar en este repositorio, siempre debes seguir estos principios técnicos y de diseño:

### 1. Nomenclatura de POIs y Compatibilidad con Subway Builder
- **Prefijo de Aeropuertos (`AIR_`):** El motor del juego elimina automáticamente `AIR_` y anexa `" Terminal"`. 
  - *Correcto:* `AIR_Cancún` (el juego mostrará `✈️ Cancún Terminal`).
  - *Incorrecto:* `AIR_Aeropuerto_CUN` (el juego mostraría `Aeropuerto_CUN Terminal`).
- **Prefijo de Universidades (`UNI_`):** Activa el algoritmo horario estudiantil (*dampening* `0.3`).
- **Formato de IDs:** No usar guiones bajos `_` en los nombres de universidades, estadios o plazas salvo en el prefijo taxonómico (`UNI_`, `SPO_`, `TOU_`, `MED_`, `TRA_`). Usar nombres legibles con espacios (ej. `UNI_Universidad del Caribe`).

### 2. Delimitación Espacial Censal y Proyecciones Demográficas (CONAPO)
- **Cero Imputación a Centro de BBOX:** Los microdatos del Censo CPV (`RESAGEBURB`) contienen manzanas de toda la entidad federativa. Nunca imputar coordenadas del centro del BBOX a registros sin geometría; las manzanas fuera de los AGEBs urbanos del BBOX deben descartarse estrictamente para prevenir megapuntos de población artificiales.
- **Sincronización Temporal Intercensal:** Dado que el Censo CPV universal por manzana es del 2020 y el DENUE/CE/ENOE son contemporáneos (2024–2026), se debe aplicar una proyección demográfica municipal (`growth_factors`) derivada de los datos abiertos oficiales de CONAPO:
  $$\text{growth\_factor}_m = \frac{\text{Población Proyectada CONAPO (Año Actual)}_m}{\text{Población Censo CPV 2020}_m}$$
- **Auditoría y Desglose en Wizard (Paso 3):** Al sincronizar proyecciones, el sistema debe presentar en la interfaz de usuario no solo la clave municipal (`cve_mun`), sino el nombre oficial del municipio, el año de proyección detectado (columna `ANO`), la población base 2020 y la población proyectada de CONAPO (`POB_MIT_MUN`), permitiendo la calibración transparente antes de compilar.

### 3. Modelado de POIs vs Clusters Comerciales y Corredores
- **Nodos Puntuales Masivos:** Aeropuertos, estadios y campus centrales deben usar POIs dedicados (`radius_m: 1500–2500m`, `mode: MAX`) para absorber el DENUE local y asegurar su cuota real.
- **Corredores y Clusters Lineales (ej. Zonas Hoteleras, Bulevares Financieros):** Nunca concentrar un corredor continuo en un solo mega-POI artificial (destruye la red lineal y colapsa una sola estación). Dejar que el DENUE distribuya el empleo orgánicamente a lo largo de las avenidas, o utilizar POIs de anclaje con radios acotados (`radius_m: 500–800m`).

### 4. Rigor Matemático y Modelo Gravitatorio
- **Mínima Distancia (`argmin`):** En la absorción de POIs por radio (`radius_m`), asignar los establecimientos DENUE al POI más cercano cuando existan radios solapados con métrica esferoidal $\cos(\text{lat})$.
- **Conservación Estricta de Masa:** Mantener la invariante $\sum_{i,j} T_{ij} \equiv \sum_i \text{PEA}_i$ mediante asignación multinomial acotada (*Bounded Multinomial Allocation*). Ningún ajuste exógeno o POI especial debe duplicar la demanda regular.
- **Snapping Vial:** Proyectar nodos únicamente sobre la red vial accesible peatonalmente (descartar autopistas o accesos restringidos).

### 5. Capas Cartográficas y Visualización en POI Studio y Wizard
- **Proveedores de Mapas y Cero Marcas de Agua:** Usar capas sin marcas de agua ni requerimiento de API key. La capa base predeterminada en tema oscuro debe ser **Esri Dark Canvas** (`World_Dark_Gray_Base`) o CartoDB Dark sin parámetros inválidos de clave. Proveer capas complementarias (Esri World Imagery, OpenStreetMap). Configurar siempre `maxNativeZoom` y `maxZoom: 20`.
- **Diferenciación de Capas:** Mantener separados los nodos residenciales (azul) y de empleo (rojo).

### 6. Resiliencia de Scripts, Codificación y Seguridad
- **Codificación Universal UTF-8:** Todos los archivos de documentación, scripts y datos deben ser UTF-8 estricto sin BOM con terminaciones LF y `.gitattributes` en la raíz para prevenir corrupción de caracteres (mojibake).
- **Resolución de Rutas:** Los scripts en `tools/` y `sb_mexico/` deben resolver rutas a `cities/` y `data/` de forma flexible, verificando candidatos relativos a `ROOT_DIR`, `CWD`, `data_dir` y `output_dir`.
- **Sanitización de Archivos:** Las APIs locales deben restringir el acceso exclusivamente a archivos dentro del directorio del proyecto para prevenir Path Traversal.

### 7. Zonas Metropolitanas Interestatales y Fuentes de Datos Multi-Archivo
- **Concatenación y Deduplicación Espacial:** Para conurbaciones multi-estado (ej. ZMVM, La Laguna, Puebla-Tlaxcala, Puerto Vallarta), el motor debe aceptar múltiples archivos `RESAGEBURB` y `denue_inegi`, concatenándolos en memoria y aplicando un recorte estricto por BBOX para eliminar manzanas y establecimientos fuera del área funcional sin duplicar masa ni demanda.
- **Trazabilidad y Detección en UI:** La interfaz debe reflejar el conteo de archivos detectados por entidad y proporcionar accesos directos a los repositorios de datos abiertos oficiales (INEGI, CONAPO, Geofabrik).

### 8. Ergonomía Cartográfica y Experiencia de Usuario en Leaflet (Wizard / POI Studio)
- **Despeje de Controles de Capas:** El área de `L.control.layers` (esquina superior derecha) debe mantenerse siempre despejada de badges, leyendas o tooltips flotantes (ubicarlos en la esquina inferior izquierda `bottom-4 left-4`).
- **Cinemática del Zoom (Scrollwheel):** Configurar el zoom de rueda con `wheelPxPerZoomLevel: 50–60`, `wheelDebounceTime: 10ms` y `zoomSnap: 0.5` para garantizar una respuesta ágil, rápida y precisa.
- **Calibración Interactiva de BBOX:** Las herramientas de delimitación deben contar con 4 tiradores visibles en las esquinas (`NW, NE, SE, SW`) con eventos de arrastre sincronizados en tiempo real con los campos de entrada de coordenadas.

### 9. Jerarquía de Ingesta y Gestión de Archivos en Wizard
- **Estructura Canónica de Directorios (`data/`):** Los microdatos de cada ciudad deben almacenarse de forma aislada en `data/<city_code>/` o `data/<city_name>/` (ej. `data/cancun/`, `data/gdl/`) para evitar colisiones entre entidades en conurbaciones distintas. La raíz de `data/` se reserva exclusivamente para datasets nacionales (ej. extracto OSM nacional PBF).
- **Ruta Activa y Controles en UI (Paso 2):** El Wizard debe mostrar explícitamente la ruta de la carpeta activa de la ciudad seleccionada y dirigir las cargas manuales a dicho subdirectorio.
- **Detección Automática y Ciclo de Vida:** Debe existir un botón de refresco/detección automática para re-escanear `data/` tras modificaciones externas en el explorador de archivos. Cada archivo listado debe permitir su desvinculación temporal de la sesión de compilación o su eliminación física previa confirmación.


