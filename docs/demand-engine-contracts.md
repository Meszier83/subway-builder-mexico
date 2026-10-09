# Contratos del legado y del candidato

Etapa 1 del plan de demanda útil y jugable. Cerrada el 2026-10-07 mediante
traza del código congelado en la etapa 0 y comprobaciones dirigidas sobre CUR.
Evidencia local: `reports/demand-playable/1/CUR/contract-evidence.json` y
`verification.json`. Este trabajo documenta lo que hace el código; no cambia
fórmulas, controles, proyectos ni paquetes de referencia.

Actualización del 2026-10-08: las mediciones de etapa 1 que siguen son históricas.
El candidato actual usa soporte completo permitido, proporciones laborales
normalizadas en el continuo, redondeo conjunto con arrastre de errores y mapeo
de sitios coincidentes. Contrato vigente: `docs/demand-od-rounding.md`;
seguimiento y resultados actuales: `docs/demand-playable-roadmap.md`.

## Dos motores y una entrega compartida

`pipeline.execute_pipeline` selecciona el candidato solo cuando
`demand.engine == v2`. Sin esa selección ejecuta el legado. El candidato entra
por `integration.execute_candidate` y utiliza `engine.build_demand`.

| Etapa | Legado de referencia | Candidato fijo de 50 |
| --- | --- | --- |
| Fuentes | Detección del pipeline; CPV, DENUE, CE, Marco y referencia EIC | `prepare_request` resuelve roles; requiere CPV, DENUE, Marco y referencia EIC para 2025 |
| Residencia | CPV POCUPADA y geometría oficial; `apply_reference` escala pesos con controles municipales EIC | Mismo lector CPV; `allocate_population` reparte controles completos, con capacidad poblacional y residual no espacial |
| Enteros | Suma decimales en la malla y redondea cada celda | Reparte enteros por municipio entre manzanas retenidas antes de crear la malla |
| Empleo | `load_workplaces`; estimaciones DENUE con transferencia CE histórica donde es utilizable | Desde etapa 2, `auto` usa `load_fine_workplaces` con partición SCIAN disjunta; métodos explícitos conservan `load_workplaces`. Los empleos se convierten en pesos de atracción |
| Ubicación | Malla, snapping y POIs; después clustering, con mapeo de presupuestos | Malla y consolidación final antes de asignar; después grupos fijos residenciales |
| POIs | Asignación especial primero; conserva los viajes realizados en la solución entera final | Desde etapa 4 reserva la cuota configurada después de captura DENUE; reporta solicitado, realizado y faltante |
| OD | Propuesta gravitatoria + objetivos Furness + `finalize_integer_od` | Entropía con orígenes duros y objetivos laborales/municipales suaves |
| Cohortes | Referencia: mínimo 10, objetivo 35, máximo 60; conserva restos necesarios | Referencia: máximo/objetivo 50; redondea personas por municipio antes de formar cohortes, con restos municipales y de POIs |
| Ruteo | Enriquecimiento OSRM posterior a OD, con respaldo registrado | Adaptador OSRM/cache o respaldo canónico; rechazo de `no_route` confirmado |
| Exportación | Sincroniza puntos y cohortes; verifica márgenes enteros finales | Sincroniza; verifica presupuestos de origen y celdas OD finales |
| Paquete | `package_demand_outputs` y entrega del Wizard | Mismas funciones de paquete y entrega; modelos/informes quedan fuera del ZIP del juego |

La identidad comparada es la copia de código real, incluidos cambios sin commit,
registrada en `reports/demand-playable/0/CUR/reference-manifest.json`. Las
referencias históricas de la skill pueden describir una implementación anterior;
el contrato del legado actual incluye la finalización entera.

## Qué significan las cifras

CPV 2020 aporta la distribución residencial histórica, POCUPADA publicada y
respaldo para celdas reservadas. Marco aporta ubicación. EIC 2025 aporta controles
municipales de residentes en viviendas particulares habitadas y microdatos
ponderados sobre ocupación y viajes al trabajo. Los universos históricos y de
EIC no se declaran idénticos.

Los viajeros considerados son ocupados de 12 años o más con categoría de traslado
al trabajo 1–6. Los que no se trasladan y los casos sin tiempo especificado no
entran en ese presupuesto. La distribución por manzana de 2025 sigue siendo una
estimación basada en pesos CPV; no se observó el domicilio actual de cada viajero.

El candidato usa origen/destino municipal de la muestra EIC. Esa información
sirve como objetivo municipal suave; no es una matriz observada por manzana.
Reserva aproximadamente un quinto de las identidades mediante hash para evaluar,
sin usar sus pesos de validación en el ajuste. El KL es una comparación municipal
interna a la encuesta, no validación independiente de viajes individuales.
Los destinos externos/desconocidos se redistribuyen bajo una frontera cerrada;
no se exportan commuters externos adicionales. Las categorías de tiempo de la
encuesta son multimodales; no equivalen a tiempos de automóvil OSRM.

La geometría no localizada, el residual no espacial, lo situado fuera del BBOX
y lo excluido por núcleo/zona no se absorben automáticamente en puntos urbanos.
Los presupuestos se fijan después de esos filtros. No se acredita cobertura
rural completa a partir de los archivos existentes.

Los `residents` y `jobs` del JSON final son sumas sincronizadas de viajes que
salen/llegan. No representan toda la población residente ni una nueva medición
de puestos de trabajo. Los pesos anteriores a la simulación se conservan en
informes/modelos; no deben reconstruirse desde campos de display exportados.

## Los 19 viajeros de diferencia

La traza volvió a ejecutar la ingesta y malla del legado congelado, deteniéndose
antes de OD. Retiene exactamente las mismas **16.737 identidades de manzana**
que el modelo candidato guardado. La diferencia máxima por manzana entre sus
estimaciones decimales es inferior a 0,000001 viajeros.

| Operación | Legado | Candidato |
| --- | ---: | ---: |
| Presupuesto decimal retenido | 766.033,697949 | 766.033,697903 |
| Cambio por redondeo | +18,302051 | −0,697903 |
| Presupuesto entero exportado | 766.052 | 766.033 |

El legado agrupa decimales y redondea cada celda en `build_demand_grid`. El
candidato usa mayores restos con desempate por identidad dentro de cada municipio
en `integer_budgets`, con capacidades de población/ocupación, y entrega enteros
a esa misma malla. El segundo procedimiento se reprodujo alimentando el registro
entero candidato al adaptador compartido: conservó exactamente 766.033.

La diferencia decimal entre las asignaciones es aproximadamente −0,000046;
no explica los 19 enteros. El distinto orden de redondeo sí los explica. No se
perdieron 19 viajeros durante OD, ruteo o exportación. No se añadirá una corrección
artificial para igualar ambos presupuestos: cada motor debe conservar el suyo.

## Restricciones duras y objetivos suaves

**Legado actual (`balanced_integer_v1`).** Conserva los presupuestos enteros
de origen después del mapeo del clustering, los objetivos enteros efectivos
de destino y las celdas OD finales. Estos últimos son objetivos del modelo:
normaliza atracción al presupuesto disponible y puede corregir objetivos según
factibilidad antes de congelarlos. No son totales CE actuales observados.
Conserva las asignaciones especiales realizadas, que pueden diferir de las
cuotas solicitadas. Verifica estos márgenes después de fusionar, sincronizar
y empaquetar. No permite mismo ID origen/destino ni cruce de zona aislada.

Su límite de distancia se aplica con Haversine al soporte regular, pero admite
respaldo de los cinco vecinos más próximos para filas/columnas sin soporte;
por ello no es un máximo absoluto para todos los proyectos. CUR registró cero
pares de ese respaldo en la finalización entera. El legado no utiliza el
componente vial del candidato como restricción OD.

**Candidato.** Conserva presupuestos enteros por origen, tamaño máximo y
celdas OD exportadas. Limita soporte por zona aislada, componente vial asociado,
distancia geométrica aproximada e IDs distintos. Si un origen retenido carece
de soporte falla; no amplía automáticamente el radio como el legado.
Los componentes proceden de vértices compartidos y asociación a la vía más
cercana: no prueban conectividad automóvil, giros o ferris; OSRM es otra prueba.

Los pesos de empleo y proporciones municipales son objetivos suaves. La
integerización sistemática conserva filas pero puede empeorar destinos y ajuste
municipal. En CUR el KL candidato pasa de **0,043665** continuo a **0,122931**
exportado; la referencia del legado en cobertura común es **0,074531**.
No convertir destinos estimados en capacidades observadas para obligar igualdad.

## Agrupamiento y extremos coincidentes

`fixed_groups.resolve` agrupa por **zona aislada y componente vial**. No
particiona por municipio ni impone radio máximo. Escoge como ancla una ubicación
fuente existente y registra contribuciones. Eso conserva masa, pero no garantiza
proximidad: la referencia de 50 todavía alcanza 38,34 km. El control inicial
de soporte de los puntos estadísticos solo prueba que cada origen tiene algún
destino permitido; no prueba los viajes de cada contribuyente una vez agrupado.

El grupo copia la coordenada del ancla y recibe un nuevo ID `dv2_fixed_…`.
El punto original permanece como destino si tiene atracción. `allocation.support`
solo compara IDs y por ello permite que el grupo viaje al punto original en
la misma coordenada. El exportador conserva esa celda y no la rechaza.

Se reprodujo con una pareja real del artefacto: distancia geométrica cero,
IDs distintos, soporte permitido y `validate_export` aprobado. Afecta a
**93 cohortes y 4.650 viajeros**. El respaldo puede escribir una distancia
mínima de automóvil positiva aunque las coordenadas sean iguales; ese mínimo
no demuestra un desplazamiento real.

Contrato de aceptación para la corrección candidata: los extremos finales del
juego deben ser ubicaciones distintas, además de IDs distintos. La representación
fija no puede crear un viaje ficticio al ancla original. Resolverlo en el soporte
antes de asignar; conservar origen y masa, o fallar con diagnóstico cuando no
haya alternativa. No eliminar esas cohortes después de exportar ni inventar
coordenadas para que parezca un desplazamiento.

## POIs: preparación compartida, asignación diferente

Desde etapa 4, el usuario autorizó reservar la cuota configurada después de
captura DENUE, comparando explícitamente el cambio. Las 13 cuotas de CUR se
cumplen: Universidad del Caribe recibe 4.950 y Aeropuerto Cancún 6.547 viajeros.
Los valores del aeropuerto difieren del legado por la preparación laboral;
no se fuerza igualdad artificial. Véase `docs/demand-od-rounding.md` y la
comparación completa en `reports/demand-playable/4/CUR/verification.json`.

La descripción y tabla siguientes registran la referencia histórica de etapa 1.
El adaptador compartido absorbe establecimientos dentro del radio y aplica
`BOOST`, `MAX` o `OVERRIDE` a la atracción del POI. Conserva IDs, coordenadas y
metadatos. Esto no convierte la asignación OD candidata en la del legado.

| POI | Atracción solicitada | Legado realizado | Candidato realizado |
| --- | ---: | ---: | ---: |
| Aeropuerto Cancún | 6.422 | 6.457 | 20.150 |
| Universidad del Caribe | 4.950 | 4.950 | 21.250 |
| Aeropuerto Cozumel | 683 | 683 | 500 |

La tabla completa está en `contract-evidence.json`. El legado asigna especiales
antes de demanda ordinaria y conserva la realización en `special_quotas`.
El candidato no tiene esa reserva: los POIs compiten por atracción con los demás
destinos. No se puede afirmar que su demanda permaneció igual. Tampoco puede
suponerse que solicitado y realizado son siempre iguales en el legado.

Contrato para la corrección candidata: mantener reglas de captura, modos, IDs,
ubicaciones y metadatos, y una asignación especial explícita que reporte
solicitado/realizado/insuficiencia. La comparación de correcciones debe conservar
la realización de la referencia candidata autorizada antes de cada cambio;
la adopción necesita además resolver y comparar esta diferencia con el legado.
No asumir que viajes especiales son puestos laborales medidos ni exigir que
una cuota arbitraria se cumpla con presupuestos insuficientes. Los restos
necesarios para conservar cuotas no justifican mover residentes lejos.

## Parámetros y caminos de entrega

En CUR ambos motores usan los mismos archivos CPV/DENUE/CE/Marco/EIC. Con EIC
el legado ignora CONAPO y factores manuales de crecimiento para esa calibración.
El candidato usa `demand.target_year` (2020 o 2025), no proyecta 2026; para 2020
declara la suposición de ocupados como commuters. No usa TIL1 para expandir
empleo, ni tasa PEA estatal para reemplazar POCUPADA. El legado CUR usa también
empleo automático con transferencia histórica; no hay expansión TIL1 activa.

V2 toma beta de `demand.beta`; si falta intenta elegirla con la muestra de
entrenamiento y usa respaldo si no hay evidencia suficiente. En esta referencia
la beta efectiva es 0,12. `macroeconomics.gravity_beta`, ajustes Furness y
experimento modal del legado no se trasladan al solver candidato. Su informe
lista esos parámetros ignorados. Los tamaños vienen de `demand` con respaldo
en macro; `fixed_cohort_size` prevalece y no se combina con `cohort_count`.

Ambos aplican multiplicadores/capacidad de zonas de afluencia al construir la
malla. El bono de alcance del solver gravitatorio legado no tiene un equivalente
en el asignador candidato; no se puede prometer paridad de todos los controles.

`preview_candidate` y `execute_candidate` comparten `build_demand`. Desde etapa 5,
el endpoint del Wizard respeta el año guardado y lee las calles del build de
esa configuración, si existe, o los activos del proyecto antes del primer build.
La compilación recibe las calles copiadas a su directorio propio; una geometría
diferente puede cambiar soporte e identidad. POI Studio respeta selección v2 y puede
modificar BBOX/núcleo para su vista. El preview de puntos no certifica el OD final.
La etapa 5 compara puntos, cohortes y descarga por HTTP con el build real de CUR.

Desde etapa 4, el adaptador busca primero el PBF del proyecto y después el
nacional compartido; admite proveedor explícito con identidad de red. CUR
verificó 19.103 pares con OSRM y conserva un respaldo estimado por atajo
imposible. Las rutas canónicas de la referencia congelada siguen identificadas
como tales.

## Qué cambiar y qué preservar

La etapa 2 amplió lectura/selección CE donde existen grupos compatibles,
sin sumar niveles solapados ni convertir supresión en cero. El lector es
compartido: cualquier mejora debe ser compatible, y la activación de una nueva
política laboral se limitará a v2 durante esta evaluación. La referencia legado
permanece congelada para detectar cambios accidentales en sus valores/defaults.
La implementación, cobertura y comparación final están en
`docs/demand-ce-detail.md` y `reports/demand-playable/2/CUR/verification.json`.
Las cifras de la traza de etapa 1 anteriores describen la referencia previa,
no la reasignación laboral de etapa 2.

La etapa 3 limita el agrupamiento residencial adicional a 500 m geodésicos y
separa municipio, zona aislada y componente vial. Conserva restos locales y
reconstruye cada presupuesto estadístico. Su comparación 50/100/200 y adaptativa
está en `reports/demand-playable/3/CUR/verification.json`; no cambia el legado.
Los municipios son las etiquetas de los puntos estadísticos, estimadas según
el contrato de geografía anterior; el límite no certifica las ubicaciones fuente.

La etapa 4 corrigió soporte, pérdida del ajuste municipal al discretizar y
cuotas de POIs. Conserva presupuestos originales y restricciones de Cozumel,
con restos adicionales. Excluye un `NoRoute` confirmado y vuelve a asignar
antes de exportar; si no hay soporte factible, falla. El ZIP aislado supera
el KL del legado, sin extremos coincidentes ni cruces prohibidos.

La etapa 5 verifica coherencia de fuentes/configuración/calles entre preview,
build y ZIP descargado; evidencia en `reports/demand-playable/5/CUR/`.
La adopción del candidato y su prueba funcional de juego siguen pendientes.
La evidencia de etapa 4 está en
`reports/demand-playable/4/CUR/verification.json`; no acredita juego ni adopción.
