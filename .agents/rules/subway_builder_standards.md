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
- **Formato de IDs:** No usar guiones bajos `_` en los nombres de universidades, estadios o plazas salvo en el prefijo taxonómico (`UNI_`, `SPO_`, `TOU_`, `MED_`, `TRA_`). Usar nombres legibles con espacios (ej. `UNI_Universidad del Caribe`).

### 2. Capas Cartográficas y Visualización en POI Studio
- **Proveedores de Mapas:** Usar siempre capas sin marcas de agua ni requerimiento de API key (Esri Dark Canvas, Esri World Imagery, OpenStreetMap).
- **Consumo de Demanda:** Dar prioridad estricta a los datos reales compilados (`dist/<ciudad>/demand_data.json`) o al DENUE real (`data/<ciudad>/*denue*.csv`). Nunca proyectar datos sintéticos si existen microdatos reales.
- **Diferenciación de Capas:** Mantener separados los nodos residenciales (origen/azul) y los nodos de empleo (destino/rojo).

### 3. Rigor Matemático y Modelo Gravitatorio
- **Mínima Distancia (`argmin`):** En la absorción de POIs por radio (`radius_m`), asignar los establecimientos DENUE al POI más cercano cuando existan radios solapados, nunca al primero en la lista YAML.
- **Conservación Estricta de Masa:** Mantener la invariante $\sum_{i,j} T_{ij} \equiv \sum_i \text{PEA}_i$. Cualquier ajuste exógeno o POI especial no debe duplicar la demanda regular.
- **Snapping Vial:** Proyectar nodos únicamente sobre la red vial accesible peatonalmente (descartar autopistas o accesos restringidos).

### 4. Resiliencia de Scripts y Seguridad
- **Resolución de Rutas:** Los scripts en `tools/` deben resolver rutas a `cities/` y `data/` de forma flexible, verificando candidatos relativos a `ROOT_DIR`, `CWD` y `cities/`.
- **Sanitización de Archivos:** Las APIs locales deben restringir el acceso exclusivamente a archivos dentro del directorio del proyecto para prevenir Path Traversal.
