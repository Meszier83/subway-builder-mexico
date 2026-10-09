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

En v2, el modo de tamaño fijo conserva restos locales cuando no puede completar
el tamaño dentro de 500 m, municipio, zona aislada y componente vial. También
conserva restos para relaciones municipales pequeñas y cuotas de POIs. La interfaz
muestra el conteo final después de evaluar; antes muestra un mínimo. Véase
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

## Verificación reproducible

Desde la raíz, con las dependencias de `requirements.txt` instaladas:

```sh
python -m unittest discover -s tests -q
python tools/check_wizard_ui.py
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
