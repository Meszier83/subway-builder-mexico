# OD entero y cuotas especiales del candidato

Desde etapa 4, v2 conserva el reparto municipal continuo antes de formar
cohortes. El legado, las fuentes laborales/residenciales y el límite de
agrupamiento de 500 m permanecen intactos.

## Soporte y POIs

Se excluyen IDs o coordenadas iguales y zonas aisladas/componentes viales
incompatibles. Se respeta la distancia máxima configurada. El exportador
verifica nuevamente soporte, presupuestos originales y celdas enteras.

Los POIs conservan IDs, ubicaciones, captura DENUE, modos y metadatos. Su cuota
es la atracción resultante. Primero se reservan aeropuertos, después otros POIs
por ID estable, con presupuesto residencial remanente, soporte permitido y
fricción declarada 0,04/km. Se asignan piezas completas cuando es posible y
los restos necesarios. No se añaden viajeros ni ubicaciones. Una cuota sin
presupuesto accesible reporta solicitado, realizado y faltante. Si el remanente
ordinario carece de destinos permitidos, el build falla.

Este cambio fue autorizado por el usuario: el candidato anterior trataba los
POIs como destinos ordinarios sin reservar sus cifras. Se comparan realizaciones
anteriores, nuevas cuotas y legado. No son empleados observados.

## Personas antes de cohortes

Desde la corrección de precisión del 2026-10-08, la asignación ordinaria mantiene
orígenes duros y proporciones laborales normalizadas en la solución continua.
Los flujos municipales siguen siendo objetivos suaves. Se utilizan todos los
destinos permitidos, sin recortar el acceso por un número fijo de vecinos.
Los márgenes laborales son estimaciones del modelo, no empleos observados por
edificio. La beta se calibra con entrenamiento cuando corresponde. La evaluación
reservada no interviene en el redondeo.

Una red de conteos por origen/municipio, dentro de cada territorio permitido,
produce una solución integral con capacidades y márgenes enteros: cada total
municipal es un piso o techo del continuo en **personas**, conservando cada
origen y los ceros del soporte. Se minimiza la desviación local del continuo.
Después se forman cohortes dentro de cada parte municipal y se seleccionan
trabajos conjuntamente por tamaño. El error acumulado de llegadas corrige los
objetivos del siguiente tamaño mediante proyección sobre sus filas y soporte.
La última matriz tiene los conteos de origen correctos aunque la corrección
deseada sea incompatible. Cada conteo de destino queda entre piso y techo de
las columnas de esa matriz factible. No se cambian tamaños, presupuestos, cuotas ni partes
municipales para conseguirlo. Los errores finales se miden en personas.
Una relación de 17 personas no desaparece
por usar objetivo 50; admite una cohorte de 17. El empleo individual es estimado.

El total explícito incluye partes municipales y segmentos de POIs. El mínimo
factible suma `ceil(viajeros_parte / máximo)` y puede superar el mínimo por
origen anterior. Una petición incompatible falla con límites explícitos.
El exportador conserva las celdas, tamaños y conteos ya decididos.
Los puntos ordinarios coincidentes se unifican exclusivamente a las mismas
coordenadas y territorio. El mapeo exportado conserva la trazabilidad de las
celdas estadísticas; no añade desplazamiento ni cambia rutas o viajeros.

## Rutas

La compilación busca primero el PBF del proyecto y después el nacional compartido.
CUR utilizó red existente con huella coincidente, OSRM 5.26.0, automóvil MLD y
PBF identificado por SHA-256. Se verificó en un contenedor temporal.

Un `NoRoute` confirmado se excluye y se resuelve la asignación antes de exportar,
con los mismos presupuestos y cuotas solicitadas. Hay hasta ocho intentos;
soporte imposible o que no cierra impide exportar. Se reutilizan rutas aceptadas
solo con igual identidad de red, coordenadas y opción de geometría. Los demás
respaldos se declaran estimados.

## Referencia histórica de etapa 4

Objetivo/máximo 50: **766.033 viajeros, 19.607 cohortes, 5.019 puntos**. Máximo
añadido por agrupamiento: 496,65 m. Cero extremos coincidentes y cruces
territoriales. Las 13 cuotas de POIs se cumplen. Se necesitan 4.182 cohortes
más que en etapa 3: siete por separar cuotas de POIs y las restantes para
conservar relaciones municipales. No se afirma rendimiento dentro del juego.

KL reservado exportado: 0,046655 frente a 0,074531 del legado; continuo: 0,046498.
La cobertura común suma 153.739 de peso de validación. Es una comparación
municipal condicionada al área retenida, no flujo observado por manzana.

De 19.104 pares, 19.103 tienen OSRM aceptado, incluidos 4.100 reutilizados de la
misma red. Uno usa respaldo por atajo imposible; ningún `NoRoute` confirmado
queda en la salida. Traza y 113 pruebas en `reports/demand-playable/4/CUR/`.
La etapa 5 verificó la entrega desde Wizard. La revisión espacial de etapa 6
detectó distorsión laboral dentro del municipio que este KL no mostraba.
Corrección y nueva entrega: `reports/demand-playable/6/CUR/correction/`.
La aceptación dentro del juego sigue pendiente.
