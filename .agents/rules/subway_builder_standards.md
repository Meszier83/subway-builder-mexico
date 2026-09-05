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

### 6. Resiliencia de Scripts, Codificación Universal y Prevención de Mojibake
- **Codificación Universal UTF-8:** Todos los archivos de documentación, scripts y datos deben ser UTF-8 estricto sin BOM con terminaciones LF y `.gitattributes` en la raíz para prevenir corrupción de caracteres (mojibake).
- **Inmunidad contra Falsos Positivos de Chino (GB2312/GBK) en Editores de Windows:** 
  - Nunca colocar emojis SMP de 4 bytes (> U+FFFF como `🚇`, `🇲🇽`, `🌟`, `🚀`) en las primeras líneas o encabezados de archivos Markdown sin BOM, ya que engañan a los algoritmos heurísticos de detección (`uchardet` en Notepad++, `jschardet` en VS Code) provocando que interpreten el archivo como GB2312 y corrompan los acentos (ej. `ó` interpretado como ideograma chino).
  - Usar exclusivamente caracteres ASCII estándar en árboles de carpetas (`|--`, `\--`) en lugar de caracteres de dibujo de cajas (`├──`, `└──`).
  - Mantener obligatorios `.vscode/settings.json` con `"files.autoGuessEncoding": false` y `.editorconfig` con `charset = utf-8`.
  - La suite de pruebas debe incluir siempre el test de integridad `tests/test_encoding.py` para bloquear cualquier carácter de mojibake o BOM antes de un commit.
- **Resolución de Rutas y Sanitización:** Los scripts en `tools/` y `sb_mexico/` deben resolver rutas a `cities/` y `data/` de forma flexible, verificando candidatos relativos a `ROOT_DIR`, `CWD`, `data_dir` y `output_dir`. Las APIs locales deben restringir el acceso exclusivamente a archivos dentro del directorio del proyecto para prevenir Path Traversal.

### 7. Zonas Metropolitanas Interestatales y Fuentes de Datos Multi-Archivo
- **Concatenación y Deduplicación Espacial:** Para conurbaciones multi-estado (ej. ZMVM, La Laguna, Puebla-Tlaxcala, Puerto Vallarta), el motor debe aceptar múltiples archivos `RESAGEBURB` y `denue_inegi`, concatenándolos en memoria y aplicando un recorte estricto por BBOX para eliminar manzanas y establecimientos fuera del área funcional sin duplicar masa ni demanda.
- **Trazabilidad y Detección en UI:** La interfaz debe reflejar el conteo de archivos detectados por entidad y proporcionar accesos directos a los repositorios de datos abiertos oficiales (INEGI, CONAPO, Geofabrik).

### 8. Ergonomía Cartográfica y Experiencia de Usuario en Leaflet (Wizard / POI Studio)
- **Despeje de Controles de Capas:** El área de `L.control.layers` (esquina superior derecha) debe mantenerse siempre despejada de badges, leyendas o tooltips flotantes (ubicarlos en la esquina inferior izquierda `bottom-4 left-4`).
- **Cinemática del Zoom (Scrollwheel):** Configurar el zoom de rueda con `wheelPxPerZoomLevel: 50–60`, `wheelDebounceTime: 10ms` y `zoomSnap: 0.5` para garantizar una respuesta ágil, rápida y precisa.
- **Calibración Interactiva de BBOX:** Las herramientas de delimitación deben contar con 4 tiradores visibles en las esquinas (`NW, NE, SE, SW`) con eventos de arrastre sincronizados en tiempo real con los campos de entrada de coordenadas.

### 9. Jerarquía de Ingesta y Aislamiento Hermético por Proyecto (Project Bubble Isolation)
- **Estructura Canónica de Directorios (`data/` y `dist/`):** Los microdatos de cada ciudad deben almacenarse de forma aislada en `data/<city_code>/` o `data/<city_name>/` (ej. `data/cancun/`, `data/gdl/`) para evitar colisiones entre entidades en conurbaciones distintas. Las salidas y archivos compilados pertenecen exclusivamente a `dist/<city_slug>/`. La raíz de `data/` se reserva únicamente para datasets nacionales (ej. extracto OSM nacional PBF y proyecciones CONAPO).
- **Cero Fugas o Fallbacks Cruzados:** Ningún endpoint del Wizard ni herramienta auxiliar (`/api/density`, `/api/demand-preview`, `/api/download`) debe caer en fallbacks a otras ciudades (como Cancún) ni a la raíz del repositorio. Si un proyecto carece de datos propios, debe retornar vacío o advertencia explícita.
- **Purga de Memoria en Frontend:** Al crear, abrir o cambiar de proyecto en el Wizard web, se deben reiniciar y purgar a cero todas las capas Leaflet, muestras de densidad y datos en caché (`resetProjectSession()`).
- **Ruta Activa y Controles en UI (Paso 2):** El Wizard debe mostrar explícitamente la ruta de la carpeta activa de la ciudad seleccionada y dirigir las cargas manuales a dicho subdirectorio.

### 10. Requisito Formal de WSL 2 y Puente Cartográfico Transparente (WSL Bridge)
- **WSL 2 como Requisito Oficial:** En entornos Windows, la compilación cartográfica 3D (Planetiler, Tippecanoe, MapGen) se ejecuta obligatoriamente dentro de WSL 2 (distribución Ubuntu).
- **Compilación Rápida con Recorte BBOX:** Si el archivo OSM PBF es nacional o grande (> 50 MB), el sistema debe recortarlo automáticamente al BBOX de la ciudad con `osmium extract` antes de pasarlo a `planetiler.jar` para recortar el tiempo de compilación de ~15 minutos a solo ~1 a 2 minutos.
- **Compilación en Disco Nativo ext4:** La compilación de teselas debe ocurrir en la partición nativa de Linux (`~/build_<city>`) y los archivos finales transferirse a `dist/<city>/`, evitando cuellos de botella de I/O en NTFS.
- **Auto-recuperación y Streaming:** Las llamadas a `wsl.exe` deben manejar auto-recuperación ante errores transitorios (`E_UNEXPECTED` con `wsl --shutdown`) y transmitir la telemetría en vivo línea por línea al Wizard.

### 11. Canon Oficial de Ruteo Vial y Tiempos de Manejo (OSRM vs Runtime)
- **Primacía del Canon Oficial:** El canon y la arquitectura del motor original de Subway Builder son definitivos y superiores a cualquier heurística propia. Nunca inventar motores de ruteo internos ni curvas sintéticas si existe el estándar documentado por el autor.
- **Línea Base a Flujo Libre (~40 km/h):** Los valores inyectados en `demand_data.json` (`drivingSeconds`, `drivingDistance`) deben reflejar condiciones de flujo libre promedio (~40 km/h). Está estrictamente prohibido pre-congestionar los tiempos en la generación de datos (ej. reducir a 20–25 km/h), ya que el motor del juego ya aplica en tiempo de ejecución multiplicadores de congestión dinámica (`CONGESTED_DRIVING_MULTIPLIER = 1.33x`), hora pico (`DRIVING_TIMES.HIGH_DEMAND = 1.5x`), búsqueda de estacionamiento (+180s origen / +180s destino) y costos de operación ($0.65/km).
- **Ruteo Canónico OSRM con `car.lua`:** El enriquecimiento de cohortes (`pops`) debe ejecutarse mediante OSRM (`osrm/osrm-backend:latest`) con perfil estándar de automóvil (`car.lua`) en WSL 2, inyectando `drivingDistance` (metros), `drivingSeconds` (segundos redondeados) y `drivingPath` (geometría GeoJSON completa).
- **Fórmula Canónica de Respaldo (Colin):** Si un par carece de conectividad vial física (ej. conurbaciones insulares como Isla Mujeres) o el entorno carece de Docker, se debe aplicar exclusivamente la fórmula canónica de respaldo de Colin:
  $$\text{road\_m} = \max(150,\, \text{round}(\text{distancia\_euclidiana} \times 1.3))$$
  $$\text{driving\_seconds} = \max\left(45,\, \text{round}\left(\frac{\text{road\_m}}{40 / 3.6}\right)\right)$$

### 12. Arquitectura de Microservicios y Resiliencia en WSL 2 (Keep-Alive, Idle Standby y Fail-Fast)
- **Supervisor Persistente contra WSL 2 Idle Standby:** Al invocar contenedores o daemons de soporte en WSL 2 desde scripts en Windows, nunca desacoplarlos con `docker run -d` y cerrar el proceso `wsl.exe`. Windows pone en suspensión la máquina virtual WSL 2 si no detecta handles de proceso activos, enviando `SIGTERM (signal 15)` tras ~15–20 segundos. El script debe mantener el handle abierto mediante `subprocess.Popen(["wsl.exe", ...])` durante toda la fase de consultas y terminarlo limpiamente en un bloque `finally`.
- **Renovación de Sockets Keep-Alive (`max=512`):** El microservicio OSRM limita cada conexión a 512 peticiones (`Keep-Alive: max=512`). Las consultas masivas deben dirigirse a `http://127.0.0.1:5000` (evitando demoras de DNS/IPv6) y utilizar `urllib3.util.Retry(total=2, backoff_factor=0.05)` para renovar sockets de forma transparente.
- **Fail-Fast ante Caídas de Servicio:** Los bucles de enriquecimiento deben monitorear fallos consecutivos de red. Si se alcanzan 5 errores consecutivos de conexión, el sistema debe abortar inmediatamente las consultas HTTP y aplicar el fallback canónico en memoria al resto de la lista, evitando bloqueos acumulativos de timeout en Windows ($N \times 4.12\text{s}$).




