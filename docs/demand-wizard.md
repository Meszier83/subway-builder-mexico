# Demanda y Wizard: comportamiento actual

El [motor candidato v2](demand-v2.md) se activa con «Usar nuevo motor de demanda»
en el paso del proyecto. Ese único toggle selecciona EIC 2025, manzanas oficiales,
ocupados censales y empleo automático CE/DENUE. No exige activar esos métodos
por separado. Conserva POIs, zonas aisladas, recortes y tamaño de cohortes.
Al apagarlo recupera los ajustes anteriores, también después de guardar y recargar.
El toggle se guarda de inmediato. Las fuentes EIC oficiales ya disponibles en el
proyecto o en la carpeta nacional se vinculan cuando cubren las entidades del Censo.
Si faltan, se indica en Fuentes: «Preparar descargas» mantiene el flujo habitual de
adquisición y vinculación. La compilación del nuevo motor se bloquea con un mensaje
concreto si falta DENUE, Censo, Marco o EIC; no vuelve automáticamente al legado.
ENOE y CONAPO quedan sin seleccionar para el nuevo motor; los controles de periodo
ENOE sólo aparecen si se solicita esa descarga opcional.
Su evaluación no activa ni migra proyectos. Comparte la preparación de POIs,
Desde etapa 4 reserva su cuota configurada después de captura DENUE y declara
insuficiencias; véase [contrato OD y POIs](demand-od-rounding.md).
El motor actual continúa siendo el predeterminado.

En v2 la compilación usa la referencia 0,120 por defecto, también en proyectos
existentes sin beta manual ni selección explícita. `demand.beta` positivo tiene
prioridad. `demand.beta_selection: automatic` activa la calibración completa EIC
en opciones avanzadas; `reference` es el valor predeterminado y ejecuta una sola
asignación. El legado no cambia.

«Comprobar sensibilidad» usa `stage=sensitivity`: prepara las fuentes y puntos,
pero no ejecuta asignación final, cohortes, ruteo ni empaquetado. Compara 0,060;
0,120 y 0,240 con 512 sorteos deterministas de origen ponderados por viajeros y
todos los destinos permitidos. Muestra distancias geométricas aproximadas,
viajes de más de 20 km, variación de destinos, cobertura y tiempos separados de
preparación/comparación. El presupuesto comparativo es 15 s comprobado entre
orígenes; la preparación no tiene ese límite. Sin cobertura de todos los municipios,
sin soporte o con tiempo agotado, declara inconcluso y descarta resultados parciales.
La caché del proyecto verifica fuentes/código/configuración/puntos y checksum.
No balancea los totales laborales ni aplica reservas finales de POIs; no cambia
beta y no certifica exactitud ni jugabilidad. Véase [política de fricción](demand-beta-policy.md).

En v2, el modo de tamaño fijo conserva restos locales cuando no puede completar
el tamaño dentro de 500 m, municipio, zona aislada y componente vial. También
conserva restos para relaciones municipales pequeñas y cuotas de POIs. La interfaz
muestra el conteo final después de asignar o compilar; la sensibilidad no calcula
cohortes finales y antes se muestra un mínimo. Véase
[contrato de agrupamiento](demand-fixed-groups.md). El adaptativo no agrega este
desplazamiento residencial adicional.

## Compilación habitual

Los proyectos nuevos y las configuraciones sin selección explícita usan:

- `city.residential_placement: official_blocks`: geometría oficial INEGI con
  cobertura y respaldo documentados.
- `macroeconomics.residential_employment: census_employed`: población ocupada
  censal y reconstrucción controlada de valores reservados.
- `macroeconomics.workplace_employment: auto`: inspección del contenido de las
  fuentes CE seleccionadas. Aplica transferencia histórica cuando hay detalle
  municipal, sectorial y por tamaño utilizable; conserva estimaciones DENUE
  acotadas cuando falta y muestra el motivo.

En v2, `auto` utiliza también los niveles SCIAN más finos compatibles y reporta
su cobertura; el legado conserva su selección sectorial. El comportamiento y
los límites están descritos en [detalle CE](demand-ce-detail.md).

La política se aplica igual a todos los proyectos. No se deduce comparabilidad
CE/DENUE del nombre de la ciudad, ni se afirma ajuste a empleo contemporáneo
solo por disponer de una referencia histórica. Los controles anteriores y CE
manual siguen disponibles en «Compatibilidad»; cargar un proyecto con métodos
explícitos los conserva y muestra un aviso. Los reportes de cobertura permanecen
visibles en el flujo principal.

El Wizard guarda la configuración antes de solicitar la vista previa o compilar.
La vista previa candidata respeta el año guardado y usa las calles del último
build de esa configuración cuando existe. Cada compilación conserva su carpeta
propia; la descarga comprueba identidad de proyecto, configuración y paquete.
Las rutas aceptadas se reutilizan solo con claves de red, coordenadas y geometría
coincidentes. El guardado conserva el contrato `routing` explícito.
El aviso previo de empleo v2 usa las mismas fuentes y selección SCIAN del motor;
la evaluación muestra cobertura dentro del BBOX y cuotas/faltantes de POIs.
La selección CONAPO conserva año y procedencia. Las zonas aisladas continúan
separando asignaciones; ninguna corrección de empleo las conecta entre sí.

## Referencia demográfica EIC 2025

La referencia EIC es optativa. En el Wizard se selecciona «Encuesta Intercensal
2025» y se indican el CSV nacional de indicadores y los CSV de personas de todos
los estados del mapa, una ruta por línea. Las rutas relativas se resuelven desde
la raíz del repositorio. La compilación y la vista previa usan el mismo lector y
los hashes de estas fuentes invalidan la caché, incluso fuera del `data_dir`.

```yaml
macroeconomics:
  residential_employment: census_employed
  projection_year: 2025
  demographic_reference:
    mode: eic2025
    indicators: data/eic2025/conjunto_datos_eic2025_105.csv
    persons:
      - data/eic2025/personas01.csv
      - data/eic2025/personas02.csv
```

Obtención: en [INEGI EIC 2025](https://www.inegi.org.mx/programas/eic/2025/),
«Datos abiertos», descargar `conjunto_de_datos_eic2025_105_csv.zip` y extraer
`conjunto_datos_eic2025_105.csv`; en «Microdatos», descargar para cada entidad
`eic2025_micro_XX_csv.zip` y extraer `personasXX.csv`. No se lee directamente el
ZIP ni se elige la entidad por el nombre del mapa.

Este modo fija la referencia en 2025. Otro año se rechaza. Sustituye los factores
manuales/CONAPO para población y ocupados, conservándolos en configuración y en
proveniencia como ignorados. Para cada municipio representado se reproducen
exactamente población y ocupados publicados sumando FACTOR de personas. Los
viajeros son los ocupados de 12–130 años, CONACT 10–20, con tiempo de traslado
1–6; no traslado (7) y no especificado (9) se conservan por separado.

Los controles completos municipales se distribuyen proporcionalmente con
POBTOT y POCUPADA completos CPV 2020 como pesos espaciales. La población EIC
corresponde a viviendas particulares; el peso POBTOT no se presenta como una
base histórica del mismo universo. Se trata de asignación espacial, no de una
tasa de crecimiento comparable. Sólo se asigna la fracción representada por las
manzanas retenidas; los recortes, zonas rurales y masa fuera del mapa no se
absorben. La EIC no indica dónde crecieron los barrios ni observa puestos de
trabajo DENUE. Las referencias de establecimiento conservan sus años reales.

La integración sirve para cualquier combinación de municipios/estados con esas
fuentes. Rechaza controles faltantes, denominadores municipales ausentes,
conflictos de identidad y microdatos parciales. Los archivos solapados se
deduplican por entidad/ID_PERSONA; dos registros diferentes con la misma identidad
fallan. No remapea automáticamente municipios nuevos o límites incompatibles.

El descargador del Wizard incluye EIC: al activarla en Macroeconomía, preparar
descargas recomienda EIC y deja CONAPO sin seleccionar. Descarga indicadores
nacionales y personas de todos los estados que intersectan el BBOX, extrae sólo
los CSV de datos y concilia población/ocupados ponderados con controles publicados
antes de publicar la fuente. Los archivos manuales o modificados se conservan.
Al terminar, el Wizard guarda las rutas en el proyecto que inició la descarga;
si cambió el proyecto o la referencia, no reemplaza esa selección. Sin activar
EIC también puede descargarse, pero eso no cambia el método demográfico.

Una referencia sin rutas puede guardarse con `pending_download: true` para preparar
la descarga; bloquea la compilación hasta descargar y guardar las rutas. El panel
muestra EIC requerida, archivos faltantes o estructura inválida y CONAPO sin uso
con EIC. Su comprobación de CSV no sustituye la conciliación municipal al compilar.
Las rutas EIC explícitas se editan en Macroeconomía, no mediante exclusiones por
nombre. Desactivar EIC conserva el flujo anterior de proyecciones.

El reporte conserva población, ocupados residentes y viajeros separados, los
intervalos al 90%, error estándar, CV y hashes. Los campos visuales del juego
siguen representando viajes realizados. La referencia no calibra celdas OD ni
establece paridad con el juego. Al desactivar EIC en el Wizard se restaura el año
anterior si se guardó `previous_projection_year`.

## Balance y diagnóstico

Los presupuestos de origen retenidos se conservan. La sincronización de campos
visuales no sustituye los presupuestos originales en la tabla territorial.

Si un grupo de destinos exige más masa de la que sus orígenes alcanzables pueden
proporcionar, el balance puede corregir sus objetivos de atracción conservando
pesos relativos y redistribuyendo la diferencia dentro del bloque presupuestario.
Se registran los objetivos solicitados y efectivos, el corte probado y los errores
contra ambos. No se agregan enlaces de viaje. Si las probabilidades anteriores
ya cumplen los objetivos corregidos, se conservan para evitar cambios innecesarios
en el muestreo. Una convergencia lenta sin déficit probado no reescribe objetivos;
los casos sin resolver siguen mostrando `iteration_limit`.

Estos errores corresponden a flujos esperados antes del muestreo y consolidación.
No certifican marginales exactos de destinos en las cohortes exportadas ni son
conteos observados de empleo.

`demand_pipeline_report.json` incluye presupuestos territoriales, balance y
procedencia vial por par, con motivos de respaldo y viajeros afectados. La
procedencia se obtiene durante la consulta, no por coincidencia numérica con la
fórmula estimada. Los campos de diagnóstico no se agregan al esquema del juego.
El respaldo vial no modela explícitamente accesos, esperas y tiempos de ferry.

## Entrega y compatibilidad

El manifiesto `wizard-build.json` identifica configuración, ejecución, estado y
paquete. Una ejecución fallida o de solo demanda sin cartografía compatible no
habilita la descarga de un ZIP anterior como si fuera nuevo. La compatibilidad
con el juego requiere comprobar importación, ejecución, guardado y recarga; las
pruebas automatizadas no sustituyen esa comprobación.

## Archivos locales y Git

`data/`, `dist/`, `reports/` y `plans/` permanecen locales. Los proyectos de
`cities/*.yaml` también, salvo `_template.yaml`. Un commit del código no respalda
los proyectos ni sus fuentes: conservar por separado configuraciones, fuentes,
ZIP probado y reportes correspondientes.

Los recursos de `tools/static/wizard/`, incluido `manifest.json` y las licencias,
son necesarios para servir el Wizard y se versionan. También se versionan los
archivos de construcción y bloqueo de dependencias de `tools/wizard-assets/`;
`node_modules/` no se versiona. Para reconstruir los recursos:

```sh
npm ci --prefix tools/wizard-assets
node tools/build_wizard_assets.cjs
```

## POIs universitarios investigados con IA

El [flujo aprobado de POIs universitarios](college-pois.md) usa matrícula
escolarizada como aproximación de demanda: mínimo 500 antes de redondear a 50,
BOOST y radio inicial de 100 m para puntos nuevos. Incluye el prompt, fuentes,
estimaciones cuando faltan cifras y revisión de coordenadas en el Wizard.
Las correcciones y descartes manuales prevalecen al repetir la investigación.
El caso de Torreón documenta la propuesta inicial y la revisión posterior.
Una cuota en blanco se guarda como `jobs: null` para revisar la ubicación.
El editor conserva las notas de procedencia y muestra **Pendiente** hasta que
se defina la cuota. La compilación se bloquea mientras quede algún POI pendiente.

## Capas de puntos en POI Studio

En el paso **POI Studio**, los botones **Empleos DENUE** (rojo) y
**Residentes CPV** (azul) permiten mostrar y ocultar las capas por separado.
La consulta guarda primero la configuración y carga la referencia del proyecto
activo. Los POIs especiales se excluyen de estas capas de referencia.

Al activar una capa, el botón muestra **Cargando puntos…** y ambos botones quedan
deshabilitados hasta terminar la consulta. Si falla, se muestra el error y la capa
solicitada no se activa vacía. Apagar una capa ya visible es inmediato y no vuelve
a consultar las fuentes. Después de actualizar el código, volver a abrir el Wizard
y recargar la página con **Ctrl+F5** para usar los recursos actualizados.

### Corrección de compatibilidad con V2 — 2026-10-31

El servidor devolvía puntos válidos de V2, pero el cliente exigía los campos de
diagnóstico del motor legado, como `employment_mode`. La validación rechazaba
la respuesta con «La referencia de demanda no coincide con el método de ocupados
seleccionado» y descartaba los puntos antes de dibujarlos.

La validación ahora distingue los contratos: para V2 comprueba
`diagnostics.engine`, la identidad de la referencia y el año 2025 del informe
candidato; para el legado conserva las comprobaciones de ocupados, EIC y método
de empleo. Una respuesta de otro motor sigue siendo rechazada. El informe de
empleo V2 se obtiene de `diagnostics.candidate_report.workplaces`.

La corrección está en `tools/static/wizard/app.js`, con su hash actualizado en
`tools/static/wizard/manifest.json`. La regresión se cubre con
`tests/wizard_density_ui.cjs`, incluido en `tools/check_wizard_ui.py`: prueba ambas
capas, exclusión de POIs especiales, POIs con cuota pendiente en el estado del
cliente, prevención de consultas duplicadas durante la carga, apagado sin consulta,
errores y validación de respuestas V2/legado.

Verificación realizada en el checkout de desarrollo:

- Navegador con el proyecto de Torreón: se reprodujo el rechazo original;
  después de la corrección aparecieron los puntos rojos y azules. Al apagar
  empleo, los puntos residenciales permanecieron visibles.
- Pasaron `node tests/wizard_density_ui.cjs`, `node tests/demand_v2_ui.cjs`,
  las cuatro pruebas de `test_poi_pending_demand.py` y las doce de
  `test_wizard_delivery.py`. Estas últimas se repitieron con acceso a sockets
  locales después de un bloqueo del sandbox (`WinError 10013`).
- Pasaron la comprobación de sintaxis JavaScript, los hashes del manifiesto
  y `git diff --check`.

El 2026-11-03 se actualizaron los arneses de compilación, empleo residencial y
tamaño de celda para incluir sus dependencias actuales. Pasó
`python tools/check_wizard_ui.py` completo. La prueba de compilación verifica
que un POI con cuota pendiente bloquea el envío y que definir la cuota permite
continuar con la validación de fuentes.

Esta comprobación cubre la visualización en el Wizard. No valida una nueva
compilación ni la jugabilidad de un paquete. La instancia temporal de prueba se
detuvo al finalizar.

## Verificación reproducible

Desde la raíz, con las dependencias de `requirements.txt` instaladas:

```sh
python -m unittest discover -s tests -q
python tools/check_wizard_ui.py
node tests/wizard_density_ui.cjs
node tests/residential_employment_ui.cjs
node tests/historical_benchmark_ui.cjs
node tests/historical_transfer_ui.cjs
```

Las herramientas `compare_*`, `verify_wizard_*` y `audit_build_impact.py` producen
comparaciones locales con fuentes y paquetes disponibles en la máquina. Sus
resultados se conservan fuera de Git, vinculados al proyecto y ejecución usados.

## Toponimia: revisión, selección y compilación

En Colonias, **Escanear** y su menú abren la misma revisión. Escanear guarda
primero la configuración y consulta las fuentes autorizadas; incorporar nombres
requiere revisar la selección y pulsar **Incorporar a la Ciudad**. El informe
identifica fuentes incompletas. Los filtros de municipio, fuente, nombre y estado
no eliminan las selecciones fuera de la vista; los botones de selección operan
sobre todas las coincidencias, no solo la página visible.

El resumen indica nuevos nombres, existentes conservados y eliminaciones.
Fusionar conserva las ediciones manuales. Reemplazar muestra las eliminaciones
y exige confirmarlas; un cambio posterior de lista exige repetir el escaneo.
Deshacer restaura la lista y retira sus descartes. El éxito se anuncia después
del guardado confirmado; un fallo deja un borrador con un mensaje persistente.

DENUE aporta direcciones y conteos de establecimientos, no población ni un
catálogo oficial de colonias. La mediana comercial no representa necesariamente
el centro residencial. Las localidades sugeridas por DENUE se marcan como
jerarquía inferida. La opción inicialmente activada de escala pequeña aplica a
las nuevas localidades inferidas; conserva las escalas previamente elegidas.
Reducir el mínimo DENUE permite revisar nombres con menor actividad comercial,
incluyendo errores y variantes. OSM no usa ese mínimo.

La poda propone **Rotulación sin peso comercial** en la interfaz: puntúa
procedencia y escala, sin conteo DENUE ni bonificación de taxonomía lexical.
Prioriza ediciones manuales, polígonos OSM, puntos OSM y finalmente DENUE,
coherente con la deduplicación espacial. Sigue siendo una heurística con sesgo
de cobertura de las fuentes. El criterio histórico de actividad sigue disponible;
el criterio de nombre corto considera solo longitud. El detalle de puntuación
se puede consultar al pasar sobre candidatos. Ciudades y asentamientos menores,
y territorios con claves distintas, no compiten en la misma zona. La poda solo
afecta candidatos representados en la revisión y respeta guardados y descartes.

**Al compilar las etiquetas** permite elegir:

- `toponymy_mode: replace`: comportamiento histórico; una lista no vacía sustituye
  cada capa para la que aporta nombres. Las capas sin candidatos conservan OSM.
- `toponymy_mode: merge`: complementa OSM, aplica renombres locales y respeta
  descartes sin eliminar homónimos remotos. Útil para listas incompletas.

La ausencia de `toponymy_mode` mantiene `replace`. El Wizard no modifica esa
decisión automáticamente. La lista y el mapa editor muestran los nombres
guardados; **Comparar OSM** permite inspeccionar también la cobertura nativa.
La cartografía exporta IDs, procedencia, identidades territoriales y descartes.
Verificar las capas compiladas y la carga en el juego sigue siendo necesario
antes de afirmar cobertura exhaustiva o jugabilidad.


### Zonas aisladas por polígono

En el mapa del proyecto, «Trazar Polígono» añade un vértice por clic. Enter o
un doble clic cierran el contorno; Retroceso elimina el último vértice y Escape
cancela. Después de guardar el nombre, selecciona la zona para arrastrar sus
vértices. Se rechazan contornos cruzados o sin área. «Manual» conserva la entrada
por caja para zonas rectangulares y los proyectos existentes siguen siendo válidos.

El Wizard conserva `isolated_zones[].polygon` como pares `[lon, lat]`, lo muestra
en ambos mapas y lo guarda sin sustituirlo por su caja envolvente. El motor usa
el contorno real, incluyendo su frontera; `polygon` prevalece si también hay `bbox`.
La auditoría previa a compilar verifica la geometría.

### Compilación de edificios con memoria acotada

El backend predeterminado `bounded` descarga las huellas de Overture a Parquet y procesa la
geometría por lotes. La selección global, los GeoJSON y los índices usan archivos
temporales en ext4. Conserva el BBOX, el núcleo urbano, las opciones LOD y el
límite global existente de 850.000 edificios. Los empates de área en el corte
usan el orden de lectura de la caché; el informe registra cuántos hay.

Los presupuestos se ajustan a la memoria disponible: DuckDB tiene un techo
inicial de 2 GiB y Planetiler de 3 GiB de heap, con dos hilos para esas etapas.
El registro muestra la etapa activa y los recuentos. La compilación requiere
espacio adicional en disco para cachés y archivos de trabajo. El backend está
verificado con `depot==1.2.7`; otra versión requiere una revisión de compatibilidad.

Una compilación nueva reutiliza la descarga completa cuando coinciden su BBOX,
versión de caché y checksum. La caché conserva la versión de Overture registrada
hasta pedir una actualización. Cambiar núcleo, LOD o simplificación vuelve a
generar la geometría derivada. Cada ejecución tiene su directorio de trabajo;
el modo de etiquetas reutiliza una base anterior sólo si coinciden sus fuentes
y opciones geométricas.

Opciones de diagnóstico al iniciar el Wizard desde PowerShell:

```powershell
$env:SB_BUILDINGS_BACKEND = 'legacy'  # Comparar con el motor anterior.
.\wizard.bat
```

`SB_BUILDINGS_BACKEND=bounded` selecciona el motor nuevo explícitamente.
`SB_BUILDINGS_REFRESH=1` solicita otra descarga de edificios. Reiniciar el
servidor es necesario para cargar cambios de código o de estas variables.

Tras una terminación forzada puede quedar un archivo de bloqueo. Su ruta
aparece en el error y contiene el PID del propietario. Verificar que ese proceso
ya terminó antes de retirar el bloqueo. Un código 9 o 137 indica terminación
abrupta; el registro del kernel permite confirmar si faltó memoria.

Para revisar un índice generado:

```powershell
python tools/check_building_index.py ruta/buildings_index.bin.gz
```

El verificador comprueba por bloques la cabecera, tamaños, offsets, referencias
y coordenadas.
La evidencia y los límites de validación están en
[los resultados de CDMX](cdmx-building-memory-results.md).

### Lectura de DENUE con codificaciones mixtas

El lector valida cada CSV completo antes de consumir sus registros. Acepta
UTF-8 y CP1252 mediante decodificación estricta. Cuando un byte indefinido en
CP1252 pertenece a una secuencia UTF-8 válida y recuperable, prepara una copia
temporal UTF-8 por bloques. El CSV original conserva sus bytes y checksum.
Los pares de bytes ambiguos que ya son válidos en CP1252 mantienen su lectura.

El registro identifica la fuente, política y número de recuperaciones, por
ejemplo `cp1252+utf8; repairs=1`. Los errores sin interpretación segura muestran
la ruta y el desplazamiento del byte; detienen la ingesta para evitar una carga
incompleta. El informe de empleo fino conserva la auditoría y la revisión del
lector forma parte de las identidades de caché.

La corrección de Guerrero se verificó contra los ocho CSV seleccionados para
CDMX, sin retirar registros ni cambiar estratos, coordenadas o controles de
empleo. Se puede continuar con **Omitir (Reutilizar existente)** en cartografía,
**Compilar Demanda Completa** y empaquetado ZIP habilitado, siempre que las
opciones cartográficas sigan correspondiendo a los archivos preparados.
Ver [resultados y estado del reintento](denue-mixed-encoding-fix-results.md).

### Memoria de asignación en mapas grandes

Antes de agrupar cohortes fijas, Demanda V2 verifica que cada residencia
original tenga al menos un destino permitido. La comprobación termina en el
primer destino válido de cada origen.

Cuando una asignación ordinaria con cohortes exactas supera cinco millones de
pares potenciales, el motor guarda los pesos de distancia en un archivo temporal
y evalúa el mismo objetivo de entropía por bloques. Un grafo de intervalos
comprueba la factibilidad del soporte completo. El redondeo conserva el
presupuesto de cada origen, el tamaño exacto de cohorte y los límites enteros
por destino. La reparación del redondeo empieza con los pares propuestos y sus
vecinos permitidos más cercanos, con acceso al soporte completo si hace falta.
El registro muestra el espacio temporal requerido y el avance de la asignación.
