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

### 3. Modelado de POIs vs Clusters Comerciales y Corredores
- **Nodos Puntuales Masivos:** Aeropuertos, estadios y campus centrales deben usar POIs dedicados (`radius_m: 1500–2500m`, `mode: MAX`) para absorber el DENUE local y asegurar su cuota real.
- **Corredores y Clusters Lineales (ej. Zonas Hoteleras, Bulevares Financieros):** Nunca concentrar un corredor continuo en un solo mega-POI artificial (destruye la red lineal y colapsa una sola estación). Dejar que el DENUE distribuya el empleo orgánicamente a lo largo de las avenidas, o utilizar POIs de anclaje con radios acotados (`radius_m: 500–800m`).

### 4. Rigor Matemático y Modelo Gravitatorio
- **Mínima Distancia (`argmin`):** En la absorción de POIs por radio (`radius_m`), asignar los establecimientos DENUE al POI más cercano cuando existan radios solapados con métrica esferoidal $\cos(\text{lat})$.
- **Conservación Estricta de Masa:** Mantener la invariante $\sum_{i,j} T_{ij} \equiv \sum_i \text{PEA}_i$ mediante asignación multinomial acotada (*Bounded Multinomial Allocation*). Ningún ajuste exógeno o POI especial debe duplicar la demanda regular.
- **Snapping Vial:** Proyectar nodos únicamente sobre la red vial accesible peatonalmente (descartar autopistas o accesos restringidos).

### 5. Capas Cartográficas y Visualización en POI Studio
- **Proveedores de Mapas:** Usar capas sin marcas de agua ni requerimiento de API key (CartoDB Dark Matter, Esri Dark Canvas, Esri World Imagery, OpenStreetMap). Configurar siempre `maxNativeZoom` y `maxZoom: 20`.
- **Diferenciación de Capas:** Mantener separados los nodos residenciales (azul) y de empleo (rojo).

### 6. Resiliencia de Scripts, Codificación y Seguridad
- **Codificación Universal UTF-8:** Todos los archivos de documentación, scripts y datos deben ser UTF-8 estricto sin BOM con terminaciones LF y `.gitattributes` en la raíz para prevenir corrupción de caracteres (mojibake).
- **Resolución de Rutas:** Los scripts en `tools/` y `sb_mexico/` deben resolver rutas a `cities/` y `data/` de forma flexible, verificando candidatos relativos a `ROOT_DIR`, `CWD`, `data_dir` y `output_dir`.
- **Sanitización de Archivos:** Las APIs locales deben restringir el acceso exclusivamente a archivos dentro del directorio del proyecto para prevenir Path Traversal.
