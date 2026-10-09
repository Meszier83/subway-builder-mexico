# Demanda útil y jugable: meta y seguimiento

Fecha de definición: 2026-10-07.

## Meta final

Poder compilar desde el Wizard un mapa mexicano con demanda reproducible y
geográficamente útil: residentes cerca de sus ubicaciones fuente, destinos
laborales estimados con el detalle que realmente publican las fuentes, viajes
permitidos por el territorio y una resolución de cohortes que el juego pueda
simular. El ZIP debe cargar, permitir jugar, guardar y recargar en la versión
objetivo de Subway Builder sin fallos. El tamaño de cohortes queda configurable.

La entrega inicial de aceptación es Cancún/Riviera Maya. Mérida y La Laguna
verifican que el método funciona también en otro patrón urbano y en un proyecto
multiestado. La calidad de la reseña del registry no es condición de éxito.
No se promete empleo observado por edificio ni flujos observados por manzana.

El candidato `v2` es el motor que se evalúa para esta meta. `legacy` permanece
como referencia reproducible y opción de regreso. Solo se decide su sustitución
al terminar la aceptación. Una mejora del lector compartido no autoriza cambiar
silenciosamente el método laboral del legado.

## Dónde estamos

**Cancún/Riviera Maya y la entrega de demanda de Mérida aceptadas por el usuario.**

El 2026-10-08, después de indicar que probaría el ZIP entregado de Mérida,
el usuario confirmó: «Estoy satisfecho con el resultado». Se cierra esta entrega
de demanda, vinculada al build `1e79a8cb02a243dd99a4a95861047fdd` y SHA-256
`6755dd46430ecbd98168e487660b37b2841e1e389025fa0c2da7a3cfda9ad139`.
Es aceptación comunicada por el usuario; no se atribuyen una duración ni pasos
individuales que no describió. La etapa 7 general sigue parcial: La Laguna y la
decisión de adoptar v2 quedan fuera de esta entrega.

El 2026-10-08 el usuario confirmó haber jugado, guardado y recargado el mapa
corregido de Cancún/Riviera Maya sin encontrar anomalías. No se atribuye una
duración de sesión ni una medición de rendimiento a esa confirmación.
Se autorizó después únicamente Mérida y únicamente su demanda. La Laguna
queda fuera de este trabajo; v2 sigue siendo candidato.

Mérida ya dispone de 20 exportaciones CE 2023 con actividad y tamaño,
descargadas del SAIC e instaladas conservando su CSV manual de totales.
El descargador prepara ese detalle para ambos motores y vincula la EIC
necesaria para v2 de 2025. La compilación aislada de Mérida reutiliza las capas
existentes. Su paquete pasó guardado, build, vista exportada y descarga HTTP reales
del Wizard: 663.630 viajeros, 23.763 cohortes de máximo 50 y 3.343 puntos,
sin coordenadas duplicadas; las 24 cuotas de POIs se cumplen. El agrupamiento
máximo es 499,05 m. Los dos primeros cálculos fueron rechazados por falta de
convergencia; la inicialización y escala numérica corregidas conservaron el
objetivo y las tolerancias. El intento verificado convergió en 25 iteraciones
y equilibró los márgenes continuos con error relativo inferior a 1e-8.

La prueba de juego de Mérida tiene aceptación del usuario registrada. El redondeo de cohortes tiene
variación total del 4,80% respecto a los pesos laborales normalizados y máximo
error por destino de 254,28 viajeros. De 22.873 pares exportados, 795 usan
estimaciones de conducción por controles de ajuste vial; no hay `NoRoute`
confirmado ni respaldo por error de conexión. La cobertura CE del BBOX antes
del núcleo/POIs es parcial (23,81% de atracción); el resto conserva DENUE.
Estas limitaciones se registran y no se presentan como empleo observado.

Entrega: `reports/demand-playable/7/MID/demand-only/ce-detail/MID-v2-demand.zip`.
Evidencia: `verification.json`, `README.md` y `../downloader-verification.json`
en esa misma carpeta. No se promueve v2 ni se modifica el proyecto activo.

La revisión del 2026-10-08 reabrió la aceptación del reparto laboral de etapa 4
y exigió repetir la entrega Wizard de etapa 5. La comparación municipal aprobada
no detectó la pérdida de destinos de la zona hotelera por soporte escaso ni la
distorsión interna de las proporciones laborales. El mismo rectángulo diagnóstico
tenía 76.833 llegadas en el legado y 28.651 en el candidato. Estas cifras son
demanda asignada, no una medición independiente de empleo.

La corrección usa todos los destinos permitidos y conserva proporciones laborales
normalizadas en el continuo. El redondeo conjunto arrastra errores entre tamaños
sin añadir cohortes. Los puntos ordinarios exactamente coincidentes se unifican
para el juego, conservando su mapeo a las celdas estadísticas. Se mantienen fuentes,
CE, presupuestos, cuotas de POIs y agrupamiento de 500 m. Las referencias 0–5 y
el proyecto activo permanecen intactos.

La entrega corregida pasó guardado, vistas previas, compilación y descarga HTTP
reales del Wizard. En el mismo rectángulo hay ahora 77.122 llegadas, frente a
28.651 del candidato anterior y 76.833 del legado; no se impone una cuota para
esa zona. El mapa conserva 766.033 viajeros, en 21.318 cohortes de máximo 50 y
2.421 puntos de juego, sin coordenadas duplicadas. Las 13 cuotas de POIs se
cumplen; el agrupamiento máximo sigue en 496,65 m. Los 20.236 pares de rutas
no tienen `NoRoute` confirmado; dos usan respaldo por atajo vial imposible.
Pasaron 86 pruebas Python y las comprobaciones de interfaz. El KL municipal
reservado es 0,053506 frente a 0,074531 del legado, con la misma cobertura.
El redondeo aproxima las proporciones laborales y su error queda registrado.

Evidencia: `reports/demand-playable/6/CUR/correction/verification.json` y
`README.md`. **ZIP aceptado por el usuario:**
`reports/demand-playable/6/CUR/correction/CUR-corrected.zip`.
El usuario confirmó cargar, jugar, guardar y recargar este paquete en el juego.
La corrección sigue en v2 y no cambia el motor del proyecto activo.

Los cierres anteriores que siguen son históricos; no acreditan por sí solos
la precisión laboral dentro de cada municipio.

La entrega histórica de etapa 5 produjo el candidato mediante los endpoints reales del Wizard:
guardado, vista previa de asignación, inicio de build, estado, vista exportada y
descarga HTTP. El ZIP descargado coincide con el hash del build y reproduce
exactamente la demanda de etapa 4: 766.033 viajeros, 19.607 cohortes y 5.019 puntos.
Las cinco capas cartográficas conservan sus hashes de referencia.

Se corrigieron el año forzado de la vista previa, la selección de calles del
build, la pérdida del contrato de rutas al guardar y el aviso laboral v2, que
ahora utiliza la misma selección de fuentes que la compilación. Se rechaza
una descarga tras cambiar de motor o mientras hay un build activo. Pasaron
58 pruebas y las comprobaciones de interfaz; no se modificó el proyecto activo.
Evidencia: `reports/demand-playable/5/CUR/verification.json` y `README.md`.
El candidato descargado está en `reports/demand-playable/5/CUR/CUR-downloaded.zip`.
Ese ZIP conserva la distorsión laboral detectada después y queda como referencia.
La prueba de juego debe hacerse con `CUR-corrected.zip`, indicado arriba.
No se adopta v2 automáticamente.

### Cierre de etapa 4

La etapa 4 conserva 766.033 viajeros en 19.607 cohortes y 5.019 puntos.
El máximo añadido por agrupamiento sigue siendo 496,65 m. No quedan extremos
coincidentes, cruces territoriales ni pares con `NoRoute` confirmado.
El KL municipal reservado del ZIP es 0,046655 frente a 0,074531 del legado,
con la misma cobertura y muestra. El redondeo conserva primero personas por
municipio y después forma cohortes; esto añade restos para no perder relaciones
pequeñas. Hay 4.182 cohortes adicionales respecto a etapa 3.

Por decisión explícita del usuario, los POIs reservan la cuota configurada
después de la captura DENUE. Las 13 cuotas se cumplen: Universidad del Caribe
pasa de 20.882 viajeros en etapa 3 a los 4.950 solicitados; Aeropuerto Cancún,
de 19.126 a 6.547. Se conserva el presupuesto total, sin añadir viajeros.
De 19.104 pares, 19.103 tienen rutas OSRM aceptadas y uno usa respaldo estimado
por atajo imposible. Pasaron 113 pruebas y las comprobaciones de interfaz.

El ZIP aislado pasó CRC, referencias, igualdad de demanda y cartografía.
En ese cierre quedaron pendientes Wizard y juego; la etapa 5 resuelve la entrega HTTP.
Evidencia: `reports/demand-playable/4/CUR/verification.json` y `README.md`;
contrato: `docs/demand-od-rounding.md`. Proyecto activo y referencias intactos.

### Resultados históricos de etapas anteriores

La etapa 3 está verificada en `reports/demand-playable/3/CUR/verification.json`.
Los tamaños 50/100/200 conservan los 766.033 viajeros y reconstruyen cada
presupuesto estadístico. Ninguna contribución cruza municipio, zona aislada o
componente vial por agrupamiento; el máximo añadido es inferior a 500 m.
El adaptativo conserva directamente esos puntos estadísticos en esta etapa.

| Modo, fuentes de etapa 2 | Cohortes | Máximo añadido por agrupamiento |
| --- | ---: | ---: |
| Tamaño 50 | 15.425 | 496,65 m |
| Tamaño 100 | 7.772 | 499,51 m |
| Tamaño 200 | 3.970 | 499,87 m |
| Adaptativo, objetivo y máximo 200 | 4.945 | 0 m |

Pasaron 105 pruebas Python y las comprobaciones de interfaz. El Wizard explica
los restos locales y muestra un mínimo hasta disponer del conteo evaluado.
No se migraron proyectos; el legado y las cápsulas de etapas 0 y 2 permanecen
intactos. Se continúa evaluando tamaño 50 por la experiencia del usuario en CUR,
sin imponer un criterio de rendimiento ni adoptar todavía el candidato.

El cierre de etapa 3 fue espacial, no de OD: con tamaño 50 el KL municipal reservado pasó
de 0,04295 a 0,12317 frente a 0,07453 del legado, en cobertura común. Persisten
112 cohortes (5.545 viajeros) con extremos coincidentes y la diferencia de
asignación de POIs. Los modos 100/200 y adaptativo tampoco superan el KL del
legado. La etapa 4 resolvió esa regresión, el soporte y las cuotas de POIs.

La etapa 2 está documentada en `docs/demand-ce-detail.md`, con comparación final
y hashes en `reports/demand-playable/2/CUR/verification.json`. En v2 con política
laboral `auto`, los grupos SCIAN más finos compatibles sustituyen la selección
sectorial. La transferencia cubre el 34,78% del peso laboral DENUE dentro de la
caja, antes de recorte urbano y POIs; el resto conserva estimaciones declaradas.
El legado reproduce exactamente su demanda de referencia y el YAML activo no
cambió. Pasaron 92 pruebas, incluido preview y compilación del detalle de clase.

En la ejecución de etapa 2, el KL municipal reservado del candidato baja a 0,04295 en
cobertura común, frente a 0,07453 del legado. La salida conserva 766.033 viajeros
y 15.322 cohortes. No cierra las etapas espaciales y OD: mantiene un máximo de
agrupamiento de 38,34 km, 147 grupos por encima de 500 m y 101 cohortes
(5.029 viajeros) con extremos coincidentes. La versión anterior tenía 93;
el cambio de pesos laborales cambia esos pares, pero todavía no corrige su causa.

El contrato está documentado en `docs/demand-engine-contracts.md`, con traza y
comprobaciones en `reports/demand-playable/1/CUR/`. Ambos motores retienen las
mismas 16.737 manzanas. Los 19 viajeros de diferencia proceden del orden de
redondeo: por celda en el legado, por municipio antes de la malla en v2.
La traza reprodujo ambos presupuestos sin alterar fórmulas ni añadir viajeros.

En la referencia de etapa 1 quedó comprobado que v2 permitía extremos coincidentes con IDs distintos
y que comparte la preparación de POIs, pero no su asignación especial: no reserva
cuotas como el legado. Aeropuerto Cancún recibe 6.457 viajeros en el legado y
20.150 en el candidato. Esta diferencia debe resolverse explícitamente en la
corrección OD; no se declarará que los POIs conservan el mismo comportamiento.

La referencia de CUR está en `reports/demand-playable/0/CUR/README.md` y sus
comprobaciones en `verification.json`. Dos ejecuciones por motor reprodujeron
la demanda y configuración; el candidato reprodujo además su modelo estadístico.
Los POIs coinciden salvo su fecha de generación. Ambos ZIPs de referencia
pasaron la validación estática. Se conservaron código, fuentes, configuración
y cartografía con hashes, sin cambiar el proyecto activo ni las fórmulas.

| Referencia | Viajeros exportados | Cohortes | Tamaño configurado |
| --- | ---: | ---: | --- |
| Legado actual | 766.052 | 23.735 | Mínimo 10, objetivo 35, máximo 60 |
| Candidato fijo de 50 | 766.033 | 15.322 | 50, con restos por partición |

La referencia candidata conserva problemas que hay que corregir: desplazamiento
máximo de agrupamiento de 38,34 km, 150 grupos con alguna contribución a más de
500 m y 93 cohortes (4.650 viajeros) con extremos en la misma coordenada y IDs
distintos. Su KL municipal reservado es 0,12293 frente a 0,07453 del legado en
cobertura común. Son resultados de la referencia, no condiciones de aceptación
ya superadas. El ruteo usa el mismo respaldo canónico en ambos motores; OSRM y
la prueba de juego siguen pendientes.

Existe implementación del candidato, integración con Wizard y POI Studio y
evidencia de pruebas anteriores. Esto no significa que la revisión actual ya
haya pasado todas las etapas de aceptación de este plan.

Evidencia inspeccionada al definir el plan:

- `docs/demand-v2-acceptance.md`: la revisión adaptativa anterior mejoró el
  ajuste municipal en Mérida y La Laguna, pero empeoró en Cancún después de
  discretizar. No trasladar sus métricas a la agrupación fija posterior.
- `docs/demand-fixed-groups.md` y
  `reports/demand-v2/fixed-200-roads/fixed-summary.json`: la ejecución fija de
  200 conservó 766.033 viajeros en 3.832 cohortes, con un desplazamiento máximo
  de representación residencial de 38 km. Menos cohortes no demuestra mejor
  localización ni rendimiento del juego.
- `reports/demand-v2/fixed-verification.json`: validación estática del paquete
  aprobada en esa ejecución; `game_validation` sigue pendiente. La prueba de
  juego comunicada anteriormente por el usuario para otro mapa no valida este ZIP.
- Revisión CE del 2026-10-07: en los seis municipios principales y cinco sectores
  admitidos hay 385 combinaciones municipio/clase con personal publicado y 632
  con personal reservado. El descargador pide sectores; el lector no reconoce
  los rótulos de otros niveles; la transferencia solo admite los sectores
  seleccionados. El candidato reutiliza esta preparación laboral.

Las consultas CE de esta revisión, sus archivos originales y hashes quedaron
conservados en `reports/demand-playable/0/CUR/source-audit/` y
`reference-manifest.json`. La evidencia ya no depende de la carpeta temporal
`ce-fidelity-check-20261007`.

## Etapas y condiciones de cierre

| Etapa | Trabajo concreto | Condición para cerrar | Estado |
| --- | --- | --- | --- |
| 0. Referencias reproducibles | Identificar configuración, fuentes, versión de código, ZIP y parámetros efectivos de cada motor. Conservar una copia aislada del legado y una del candidato actual. Resolver las rutas realmente utilizadas; no asumir que archivos movidos siguen en el proyecto. | Dos ejecuciones reproducibles de CUR, con identidad de entradas/salidas y referencia de comparación. Registrar por separado adaptativo y fijo; no mezclar revisiones. | Completada: `reports/demand-playable/0/CUR/verification.json`; legado actual y candidato fijo de 50 |
| 1. Contratos de los dos motores | Trazar fuentes → residentes → pesos laborales → OD → cohortes → ruteo → ZIP. Identificar etapas compartidas, controles aplicables, objetivos duros y suaves y POIs. Definir qué cambiará exclusivamente en v2. | Comparación documentada que explique las diferencias de presupuesto y significado de las cifras, sin exigir igualdad artificial entre motores. | Completada: `docs/demand-engine-contracts.md` y `reports/demand-playable/1/CUR/verification.json` |
| 2. Empleo con el detalle CE disponible | Extender consulta y lectura a niveles SCIAN publicados. Elegir grupos sin solapamientos; comprobar universo, unidad censal, bandas y años. Mantener estimaciones declaradas donde falte información. | CUR usa grupos más finos donde son utilizables, conserva celdas reservadas y reporta cobertura laboral por masa y por registros. Ningún ajuste incompatible se presenta como exacto. Pruebas de lector y selección compartida, con legado de referencia protegido. | Completada: `docs/demand-ce-detail.md` y `reports/demand-playable/2/CUR/verification.json`; comparabilidad histórica condicional, no ajuste contemporáneo exacto |
| 3. Resolución espacial y cohortes | Comparar adaptativo y tamaños 50/100/200. Mantener conservación y restricciones, evitando reunir residentes distantes para completar grupos. Aceptar restos adicionales cuando sean necesarios. | Todos los viajeros originales se reconstruyen por contribución. Para el modo que se proponga como habitual: cero cruces de municipio por agrupamiento, cero cruces de zona aislada/componente permitido, desplazamiento máximo de agrupamiento de 500 m y ninguno de decenas de km. | Completada: `reports/demand-playable/3/CUR/verification.json`; modos fijos cumplen y adaptativo no añade desplazamiento. OD/juego pendientes |
| 4. OD final y ruteo | Evaluar los viajes que realmente se exportan. Medir pérdida entre solución continua y cohortes finales y resolver la regresión de CUR. Resolver la asignación especial de POIs, actualmente distinta del legado. Usar rutas OSRM reales para la entrega de juego, con respaldos identificados. | Cero self-commutes por ID o extremos coincidentes, y cero pares territorialmente prohibidos; presupuestos enteros reconstruidos; solicitado/realizado de POIs explícito; por autorización del usuario, reservar cuotas configuradas después de captura DENUE y comparar con referencias anteriores. El KL municipal reservado del ZIP candidato no empeora frente al legado en cobertura común, salvo tolerancia numérica documentada. Las proporciones laborales estimadas se conservan en el continuo y se informa su error al formar cohortes. No hay pares con `no_route` confirmado. | Revalidada tras la corrección: `reports/demand-playable/6/CUR/correction/verification.json`; precisión interna, cuotas y rutas verificadas, juego pendiente |
| 5. Entrega desde el Wizard | Producir el ZIP candidato con configuración guardada, fuentes y cartografía correspondientes. Verificar vista previa, compilación y descarga, y mostrar datos faltantes/respaldo laboral. | El archivo descargado es el verificado; CRC, miembros, referencias y hashes correctos. Cambiar de proyecto o fallar un build no entrega un ZIP previo. El selector de motor y los controles guardados se respetan. | Revalidada tras la corrección: `reports/demand-playable/6/CUR/correction/verification.json`; guardado, preview, build y descarga HTTP del ZIP corregido, juego pendiente |
| 6. Juego en CUR | Cargar el candidato con cohortes de referencia de 50, construir una línea sobre un corredor de demanda, simular, guardar y recargar. | Dos sesiones completas del candidato, con al menos 10 minutos reales de simulación cada una y guardado/recarga. Sin errores; pasajeros y POIs funcionan. Sin umbral comparativo de rendimiento. | Aceptada por el usuario; carga, juego y guardado/recarga comunicados. Duración y número de sesiones no registrados |
| 7. Generalización y decisión | Repetir los controles de fuentes, exportación y Wizard en Mérida y La Laguna; hacer carga y guardado/recarga de ambos paquetes. Decidir preset habitual y adopción de v2. | CUR cumple la prueba completa y los otros dos mapas superan compilación y prueba de juego. Se entrega configuración/preset elegido, respaldo explícito, ZIPs probados y posibilidad de volver al legado. | Parcial: entrega de demanda de Mérida aceptada por el usuario. La Laguna y adopción de v2 fuera del alcance actual |

Las etapas pueden reutilizar evidencia anterior solo cuando coincide su identidad
de código, configuración y fuentes. Una ejecución anterior no cierra una etapa
modificada. Detectar ahora una limitación no equivale a haberla corregido.

## Decisiones para evitar ambigüedades

### Empleo

CE 2023 es referencia histórica. Solo un universo compatible permite imponer un
total como control de ese universo; no se convierte automáticamente en empleo
exacto de 2025. En el resto de casos, el efecto sobre las estimaciones se declara
como transferencia histórica. El total de unidades coincidente es una comprobación,
no una prueba suficiente de comparabilidad.

La banda 251+ permanece abierta. Una media censal calculada con personal/unidades
es válida como descripción del grupo; no mide cada establecimiento. No inventar
un máximo ni usar 450 como headcount observado. Resolver las restricciones de
grupo cuando sean compatibles y conservar explícita la incertidumbre individual.

Una consulta con sector, subsector y clase contiene totales solapados. El modelo
debe elegir una partición de grupos que cubra cada establecimiento una sola vez,
sin sumar padres e hijos ni tratar supresión como cero. Se probará también el caso
de personal publicado sin desglose utilizable por tamaño.

### Ubicación y tamaño de cohortes

El límite inicial de 500 m se aplica al peor caso: distancia desde cada origen
contribuyente a la coordenada que representa su cohorte, no al promedio. Es una
decisión de aceptación para el desplazamiento añadido por agrupar cohortes;
no es una propiedad de INEGI ni
un límite de acceso a estaciones del juego. No certifica la precisión de los
puntos censales, grid o snapping anteriores. Esas distancias se informan aparte.

Se prioriza conservar ubicación antes que lograr el menor número posible de
cohortes. Si un grupo no puede completarse dentro de sus límites, se exporta un
resto adicional o se mantiene resolución adaptativa; no se mueve el residente
lejos para cumplir el tamaño. Una solicitud incompatible debe explicarse.

Esta regla sustituyó en etapa 3 el contrato del modo fijo global anterior. Se
muestra en el Wizard y en `docs/demand-fixed-groups.md`. No prometer simultáneamente grupos
exactos globales, mínima cantidad de restos y conservación espacial estricta.

### OD

El candidato conserva presupuestos de origen. Desde la corrección de precisión
del 2026-10-08, conserva además proporciones laborales normalizadas en la solución
continua sobre soporte completo permitido. No las convierte en capacidades
medidas por destino. Las proporciones municipales permanecen suaves. El redondeo
conserva los inventarios de cohortes y mide desviaciones laborales finales, además
de la comparación municipal, para evitar aprobar una distribución local incorrecta.

La muestra EIC reservada para evaluar no se usa para ajustar los flujos. Comparar
misma cobertura, muestra y referencia temporal y registrar tamaño muestral.
El KL es una comparación municipal, no validación de viajes por edificio ni una
fuente independiente de la encuesta. Si hay poca evidencia, registrar el límite
y utilizar la revisión espacial y de juego; no inventar precisión.

### Configuración de referencia y prueba de juego

Cancún/Riviera Maya se evalúa con cohortes de 50 como referencia. El usuario ha
confirmado que ese tamaño funciona bien en Cancún. Esa observación fija el tamaño
de trabajo; no constituye por sí sola la validación del nuevo ZIP candidato.

Se retira el criterio propuesto del 20% de regresión de rendimiento. No se requiere
un estudio de FPS, memoria o tiempos dentro del juego para cerrar este plan.
En ciudades grandes el tamaño sigue configurable. Se conserva la cantidad de
cohortes en los informes para saber qué se ha generado.

La aceptación se centra en ubicación, coherencia de viajes, conservación y
funcionamiento: cargar, simular, guardar y recargar el paquete final. No hay
migración automática mientras esa prueba esté pendiente.

## Cómo mantenemos el seguimiento

Este documento es el registro principal del plan. Los informes locales de cada
etapa se guardan bajo `reports/demand-playable/<etapa>/<mapa>/` con identidad de
entradas, salidas, pruebas y fecha. No se guardan microdatos en Git.

Estados: **Pendiente**, **En curso**, **En verificación**, **Completada** y
**Bloqueada**. Solo una etapa está activa. Bloqueada significa que falta un
requisito identificado; no se marca completada para poder avanzar. Una prueba
de juego que requiera intervención del usuario queda visible como pendiente.

Al cerrar una etapa, registrar en esta tabla la evidencia concreta. Cada reporte
al usuario empieza con:

1. Etapa actual y condición de cierre.
2. Qué se hizo y qué prueba lo respalda.
3. Qué falta o qué impide cerrar.
4. Próxima acción concreta.

Si cambia un contrato, registrar el cambio, motivo y comprobaciones que hay que
repetir antes de implementar. No reabrir partes ya verificadas por trabajo ajeno
al alcance. La promoción de v2 y la publicación del mapa son decisiones finales
separadas de implementar y comprobar el candidato.

## Primera acción de implementación (registro histórico)

Las etapas 0 y 1 están cerradas. La siguiente acción es extender consulta y
lectura CE y seleccionar grupos compatibles más finos para v2, manteniendo la
referencia y política laboral del legado. La ampliación del lector compartido
debe ser compatible; no activa automáticamente una nueva estimación en legacy.
Registrar cobertura y celdas reservadas antes de usar el nuevo detalle. Los
problemas espaciales, extremos coincidentes y asignación de POIs quedan trazados
para las etapas 3–4; todavía no están corregidos.
