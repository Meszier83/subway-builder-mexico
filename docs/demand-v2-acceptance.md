# Estado del candidato de demanda — 6 de octubre de 2026

Estado actual y corrección de precisión del 8 de octubre:
[Meta y seguimiento](demand-playable-roadmap.md). Los resultados de esta página
son referencias históricas; no describen el ZIP corregido de etapa 6.

**Actualización posterior:** el Wizard también permite tamaños fijos globales
de 50, 100 o 200, con restos separados por restricciones territoriales y POIs.
La verificación más reciente consta de 101 pruebas y cuatro ejecuciones de
Cancún/Riviera Maya. Véanse los resultados y el coste de desplazamiento
residencial en [Tamaños fijos de cohortes](demand-fixed-groups.md).
Los resultados comparativos y el paquete de 24.000 cohortes descritos debajo
corresponden a la revisión anterior del modo adaptativo; sus identidades no
son las de la revisión actual. No constituyen validación de la nueva agrupación.

Implementadas las etapas de fuentes, población, puntos, asignación y exportación
en `sb_mexico/demand_v2/`. Compilación, Wizard y POI Studio usan ese núcleo. El
motor anterior continúa activo; ninguna ciudad fue migrada. El candidato usa 2025
por defecto, conserva las fechas reales de las fuentes y reutiliza las reglas e
identificadores actuales de POIs.

## Verificación

- 96 pruebas Python aprobadas, incluyendo controles incompatibles, población y
  ocupados, masa residual, territorio, multiestado, fuentes duplicadas,
  correspondencias municipales, restricciones, enteros, cohortes, POIs,
  determinismo, caché, HTTP y entrega del Wizard.
- Suite de interfaz del Wizard aprobada: guardado previo, evaluación pasiva,
  conservación de POIs y rechazo de respuestas de otro proyecto. La interfaz
  distingue el ajuste continuo del resultado entero.
- Identidades de esa revisión de código/configuración/fuentes y hashes de salidas
  comprobados en las tres comparaciones. `git diff --check` aprobado.
- Cancún con calles reales y total explícito: 766.033 viajeros, 2.266 puntos y exactamente 24.000 cohortes;
  restricción a componentes de vértices viales aplicada, sin pérdida de presupuesto.
  ZIP aislado validado por el comprobador de entrega existente. Sus archivos
  cartográficos son idénticos a los del artefacto previo.

Los logs y resultados reproducibles están en `reports/demand-v2/`. El ZIP para
pruebas es `reports/demand-v2/cur-24000/CUR.zip`; no sustituye la entrega activa.

Se corrigió la omisión de los controles de cohortes del Wizard. El candidato
anterior repartía viajeros entre muchos pares pequeños antes de aplicar el
tamaño máximo; ignoraba el tamaño objetivo. Ahora la cantidad y los tamaños de
cohortes se deciden durante la asignación, antes de congelar la matriz OD entera.
El exportador conserva esa matriz y ese número exacto. Los valores mínimo,
objetivo y máximo del Wizard vuelven a gobernar el candidato. Un nuevo campo
permite solicitar un total exacto; el cálculo informa los límites factibles.

## Comparaciones sobre las mismas fuentes

Las tres ejecuciones estadísticas usan el mismo respaldo canónico de conducción
y ninguna geometría vial. La prueba adicional de Cancún sí usa calles. No se
mezclan tiempos EIC de distintos modos con tiempos de automóvil.

| Mapa | Viajeros anterior → candidato | Cohortes anterior → candidato | JSON anterior → candidato, MB decimales |
| --- | ---: | ---: | ---: |
| Cancún/Riviera Maya | 766.052 → 766.033 | 23.774 → 22.070 | 3,63 → 3,97 |
| Mérida | 663.544 → 663.630 | 20.921 → 19.275 | 3,31 → 3,59 |
| La Laguna | 506.845 → 506.758 | 6.084 → 3.743 | 1,04 → 0,83 |

El candidato cambia el ajuste residencial municipal anterior al recorte y el
redondeo jerárquico; por ello el presupuesto retenido no tiene que coincidir con
el anterior aunque se usen las mismas fuentes. Dentro de cada candidato, OD y
cohortes conservan exactamente ese presupuesto. El residuo máximo entero por
origen es cero en las tres ejecuciones. No se atribuye toda la diferencia a una
sola operación residencial: esa descomposición causal todavía no se ha medido.

El 20% de identidades EIC se reserva para evaluar flujos, sin usar esos flujos para
calibrar beta o los objetivos municipales. La comparación siguiente usa las
cohortes finales, cobertura municipal común y las mismas observaciones reservadas.
KL menor indica mejor ajuste de proporciones municipales, no precisión calle por
calle ni una predicción del juego.

| Mapa | KL anterior | KL candidato entero | Peso de observaciones evaluadas |
| --- | ---: | ---: | ---: |
| Cancún/Riviera Maya | 0,07782 | 0,14250 | 153.739 |
| Mérida | 0,23340 | 0,07977 | 139.204 |
| La Laguna | 0,22262 | 0,07859 | 121.634 |

**Cancún empeora después de discretizar.** Su solución continua obtiene KL 0,04623,
pero la asignación de cohortes por origen no preserva las proporciones municipales.
El informe registra ambas evaluaciones y el cambio absoluto de masa entre la
solución continua y la entera. Las cohortes reconstruyen fielmente la matriz
entera: el empaquetado no reasigna pares para ocultar esta diferencia.

## Limitaciones de observación

- EIC observa municipios completos; las proporciones de un recorte parcial son
  estimaciones. La evaluación condiciona a los destinos municipales representados.
- Las ubicaciones residenciales siguen apoyándose en CPV/Marco disponibles. No
  se localizan automáticamente barrios nuevos de 2025. La masa residual permanece
  separada y sin coordenadas inventadas.
- Una edición DENUE sin metadatos queda desconocida. CE es referencia histórica
  de intensidad; no se declara control laboral exacto de 2025.
- Kish aproxima precisión; no sustituye intervalos del diseño muestral EIC. El
  conjunto reservado pertenece a la misma encuesta; no es una validación con
  otra fuente independiente. Los controles demográficos completos sí utilizan la
  encuesta completa y no forman parte del experimento de reserva de flujos.
- La etiqueta municipal de puntos consolidados y de puntos anteriores se estima
  por la referencia fuente más próxima. Las componentes de calles no modelan
  restricciones de giro ni infieren ferris. OSRM real no se validó en estas ejecuciones.

## Pendientes para aceptación y migración

El núcleo cumple conservación y validación estática. **No se acepta todavía como
sustituto general de mayor precisión.** Debe resolverse o justificarse la pérdida
de ajuste entero en Cancún y contrastar resultados con observaciones independientes.

El exceso de entre 7 y 30 veces de la versión anterior del candidato se resolvió
aplicando los controles del Wizard durante la asignación. Las ejecuciones candidatas
tardaron aproximadamente 51, 119 y 81 segundos respectivamente; el pico del
proceso comparador fue aproximadamente 0,55, 0,77 y 1,03 GB e incluye imports y
la referencia cargada. Son mediciones locales, no promesas de rendimiento.

La solicitud de 24.000 cohortes en Cancún se verificó con calles reales, manteniendo
exactamente los 766.033 viajeros y un máximo de 60 por cohorte. Para esos orígenes
el mínimo factible es 13.792; se informa y rechaza una solicitud menor. El ZIP
candidato pasó la validación de integridad, referencias y membresías del proyecto.

Faltan carga, simulación y guardado/recarga en la versión objetivo de Subway
Builder, incluyendo los comportamientos existentes de POIs. Esta sesión no
dispone de control nativo del juego. Los manifiestos conservan
`game_validation: pending`; no se migró ningún proyecto. La migración individual
se hará después de esas verificaciones, preservando configuración y artefactos
anteriores para volver atrás.

Configuración y uso: [Motor candidato de demanda](demand-v2.md).
