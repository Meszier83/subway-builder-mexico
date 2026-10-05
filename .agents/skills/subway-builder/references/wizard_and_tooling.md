# Wizard Studio, POI Studio y Herramientas CLI

Este documento detalla la suite de herramientas interactivas, interfaces visuales y utilidades de consola que componen Subway Builder Mexico.

Los comandos suponen un Python con dependencias del proyecto y la raiz del
repositorio como directorio actual. Para WSL y errores de permisos, seguir
[SKILL.md](../SKILL.md). Confirmar flags en `--help` antes de automatizar.
Las descripciones siguientes orientan la navegacion; comprobar su comportamiento
en el checkout actual antes de afirmar aislamiento, persistencia o integridad.

---

## 1. Asistente Visual Integral (Wizard Studio)

El **Wizard Studio** es una aplicacion web local completa construida con la identidad visual oficial del Metro de la Ciudad de Mexico (disenada por **Lance Wyman**):

```bash
# Lanzamiento directo en consola:
python tools/wizard.py
# O en Windows mediante doble clic:
wizard.bat
```

El servidor web inicia en `http://localhost:5050` e implementa una experiencia guiada en 6 pasos:

```text
[1. Proyectos & BBOX] ---> [2. Fuentes INEGI] ---> [3. Calibracion CONAPO]
         |                          |                          |
         v                          v                          v
[4. POI Studio]      ---> [5. Compilacion SSE] ---> [6. Visor & ZIP]
```

### Paso 1: Proyectos y Delimitacion Espacial
- **Gestion Aislada:** Permite crear proyectos nuevos o alternar entre ciudades existentes sin fuga de datos (`resetProjectSession()`).
- **Tiradores Interactivos de BBOX:** 4 marcadores arrastrables en las esquinas (`NW, NE, SE, SW`) sincronizados en tiempo real con los campos de coordenadas.
- **Encuadre Cinematografico 16:9:** Marco de captura rapida para calibrar la posicion y zoom inicial de la camara (`initial_center`, `initial_zoom`) para el primer dia de partida.
- **Nucleo Urbano LOD (Concave Hull):** Trazado poligonal manual o calculo automatico con el boton **Auto Censo** a partir de los centroides de manzana del INEGI (`/api/auto-urban-polygon`).
- **Zonas Aisladas (`isolated_zones`):** Delimitacion de islas habitadas sin puente.

### Paso 2: Fuentes de Datos INEGI / OSM
- Muestra explicitamente la ruta de la carpeta activa de la ciudad (`data/<city_slug>/`).
- Valida la presencia de `RESAGEBURB` (Censo CPV 2020), `denue_inegi`, `SAIC` (Censos Economicos 2024), `ENOE` y el archivo nacional `mexico-latest.osm.pbf`.
- Ofrece enlaces directos de descarga gratuita de microdatos en los repositorios oficiales.

### Paso 3: Calibracion Macroeconomica y Auditoria CONAPO
- **Tabla de Auditoria Municipal:** Presenta en la interfaz la clave municipal (`cve_mun`), nombre oficial del municipio, ano proyectado, poblacion 2020 censal, poblacion proyectada CONAPO y el factor de crecimiento resultante.
- Calibracion de tasas ENOE (PEA e informalidad $TIL_1$), coeficientes gravitatorios y configuracion opcional del Laboratorio Modal.

### Paso 4: POI Studio Geoespacial Integrado
Editor cartografico con mapa satelital y 5 pestanas especializadas:
1. **POIs:** Listado de generadores con buscador y filtros por prefijo (`AIR_`, `UNI_`, `SPO_`, `MED_`, `TOU_`, `TRA_`).
2. **Editor:** Formulario con calculo en tiempo real de absorcion de empleos DENUE segun el radio configurado (`radius_m`, modo `MAX`, `BOOST`, `REPLACE`).
3. **Colonias:** Extraccion toponimica inteligente (`places`) con sugerencias automaticas de nombres de barrios y asentamientos prioritarios.
4. **Afluencia:** Trazado de Zonas de Alta Afluencia (`affluence_zones`) con arquetipos urbanos y multiplicadores.
5. **Exclusiones:** Trazado de Zonas de Exclusion (`exclusion_zones`) para lagunas, manglares o reservas ecológicas.

### Paso 5: Compilacion y Terminal de Telemetria en Vivo
- Conmutador para compilacion completa o solo regeneracion de demanda (`--skip-map`).
- Opcion de supresion de selvas y macro-reservas no pobladas (**Solo parques urbanos** / `urban_parks_only`).
- Streaming en vivo via **Server-Sent Events (SSE)** con barra de progreso, conexion OSRM en WSL 2 y empaquetado final.

### Paso 6: Visor de Demanda WebGL y Exportacion
- Inspeccion en mapa interactivo de densidades residenciales (burbujas azules) y de empleo (burbujas rojas).
- Metricas globales de masa activa y cohortes `pops`.
- Boton de descarga directa del paquete ZIP empaquetado (`dist/<ciudad>/<CODIGO>.zip`).

Antes de entregar una descarga, comprobar que pertenece al build actual y a la
configuracion seleccionada. El pipeline puede devolver solo demanda si falta
cartografia; no reutilizar un ZIP previo como si acabara de generarse. Verificar
miembros y referencias segun [game_engine_specs.md](game_engine_specs.md), y
observar importacion, carga, guardado y recarga antes de afirmar jugabilidad.

---

## 2. Editor Visual Standalone (POI Studio)

Para trabajar de forma independiente en el diseno de polos de atraccion sobre mapa satelital sin abrir todo el Wizard:
```bash
python tools/poi_studio.py --city cities/cancun_riviera_maya.yaml
```
Permite arrastrar pines, visualizar radios de cobertura en metros, calcular en vivo el empleo absorbido del DENUE local y persistir directamente en el archivo YAML de la ciudad.

---

## 3. Compilador de Linea de Comandos (`build.py`)

La herramienta principal para ejecuciones automatizadas, scripts CI/CD o generacion en terminal:

```bash
# Compilacion completa (Cartografia 3D + Demanda Furness + Empaquetado ZIP):
python build.py cities/cancun_riviera_maya.yaml

# Solo demanda (requiere PMTiles y roads.geojson compatibles para generar ZIP):
python build.py cities/cancun_riviera_maya.yaml --skip-map

# Incluir geometrias drivingPath para auditoria SIG externa:
python build.py cities/cancun_riviera_maya.yaml --include-driving-path

# Especificar carpetas de datos o salida personalizadas:
python build.py cities/cancun_riviera_maya.yaml --data-dir data/mi_ciudad --output-dir dist/mi_ciudad
```

---

## 4. Herramientas Auxiliares de Visualizacion

- **`visualize.py`:** Genera un visor HTML interactivo con Leaflet para auditar densidades de poblacion y matrices de deseo de viaje.
- **`tools/preview_toponymy.py`:** Inspecciona las capas toponimicas de colonias y asentamientos extraidas de OSM.
- **`tools/demo_preview.py`:** Generador rapido de muestras visuales para reportes.

---

## 5. Suite de Pruebas Unitarias

El proyecto cuenta con una bateria formal de pruebas automatizadas:
```bash
# Ejecutar la suite completa:
python -m unittest discover -s tests

# Ejecutar una prueba especifica:
python -m unittest tests/test_encoding.py
python -m unittest tests/test_gravity.py
python -m unittest tests/test_road_routing.py
```
