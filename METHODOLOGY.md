# Metodología vigente de Subway Builder México

Actualizada el 2026-10-05. Describe el camino predeterminado del pipeline y del
Wizard. Las selecciones explícitas de métodos anteriores permiten reproducir
proyectos históricos; no deben confundirse con este comportamiento.

## Fuentes y significado de los conteos

| Fuente | Uso actual | Límite |
| --- | --- | --- |
| CPV 2020, RESAGEBURB | POCUPADA publicada por manzana como origen residencial | Reconstrucción acotada de registros sin conteo utilizable |
| Marco Geoestadístico | Colocación residencial con geometría oficial, por localidad | La cobertura retenida se registra; no se imputa el centro del BBOX |
| CONAPO | Factores municipales de crecimiento demográfico al año seleccionado | Proyectar población no mide empleo nuevo ni su distribución real |
| DENUE | Coordenadas, SCIAN y bandas de personal ocupado como atracción laboral | Las bandas no son headcounts exactos ni un registro exclusivamente formal |
| CE / SAIC | Referencia histórica y medias municipales de sector/tamaño donde hay detalle utilizable | Comparabilidad temporal, cobertura y unidad de observación condicionan su uso |
| ENOE | Indicadores estatales que el Wizard puede inspeccionar y guardar | El camino actual no añade empleos mediante TIL1 |
| OSM / OSRM | Cartografía y métricas viales de los pares exportados | Perfil de automóvil en flujo libre; los respaldos estimados se reportan |

Este pipeline no ingiere flujos O/D observados. Utiliza demanda gravitatoria
sintética. Conservar objetivos del modelo no los convierte en mediciones.

## Orígenes residenciales

El método `census_employed` utiliza POCUPADA de 2020 cuando está publicada.
Aplica reconstrucción acotada donde falta un conteo utilizable, dejando constancia
de la cobertura y masa reconstruida. No deriva toda la distribución residencial
de una tasa ENOE estatal uniforme. Esa fórmula pertenece a la selección `legacy`.

Los factores municipales CONAPO se aplican como supuesto de proyección de la
masa de origen al año del modelo. El reporte distingue año solicitado/efectivo,
base del factor y masa excluida por filtros espaciales. La selección de geometría
oficial respeta localidades, manzanas/AGEB, exclusiones y núcleo urbano.

El presupuesto entero autorizado se fija sobre la demanda retenida y agregada
por el grid. Se conserva su correspondencia con los puntos finales después del
clustering; el display sincronizado no es la evidencia independiente del presupuesto.

## Destinos laborales y referencia CE

El método `auto` inspecciona las fuentes del proyecto mediante el mismo mecanismo
para todas las ciudades. No exige una excepción específica para Cancún.

Si existen celdas utilizables de personal ocupado y establecimientos por municipio,
sector y tamaño, se construye un contrato ligado a esas fuentes. La transferencia
histórica emplea la media personal ocupado / establecimientos en cada grupo,
acotada por la banda DENUE aplicable. Se aplica sobre registros clasificados de
DENUE antes del recorte espacial. Los registros o grupos no transferibles conservan
sus priors acotados y se reportan como respaldo.

En el CUR verificado, la referencia es actividad de 2023, con grupos municipales
de sectores SCIAN 31–33, 43, 46, 53 y 72 y estratos de tamaño publicados. Tener
SCIAN de seis dígitos en DENUE no significa disponer de controles CE de seis
dígitos. La media histórica sobre establecimientos actuales no fuerza la suma
actual a igualar el total CE de 2023.

Sin ese detalle utilizable, el modo `ce_bounded` conserva estimaciones DENUE
acotadas sin ajuste CE no autorizado. El reporte explica la razón del respaldo.
La presencia de un archivo CE no certifica comparabilidad ni un ajuste a sus totales.

No se crea una capa adicional de empleo informal mediante TIL1, ni se afirma
completitud rural, empleo formal exacto o intensidad observada por establecimiento.
La banda superior 251+ sigue abierta. Las ubicaciones iniciales provienen de
DENUE; el grid, snapping y clustering producen puntos finales agregados, no
necesariamente las coordenadas intactas de cada establecimiento. Los POIs
especiales mantienen su política de cuotas separada.

## Gravedad, soporte e integerización

`balanced_integer_v1` es el método predeterminado compartido. La propuesta inicial
conserva la gravedad, semilla, fricción, alcance, afluencia y asignación de POIs
configurados. El ajuste de objetivos por presupuesto alcanzable ya existente se
documenta con objetivos solicitados y efectivos; no es calibración estadística CE.

Después de finalizar la geometría:

1. Se agregan los presupuestos originales por ID final y se descuenta el consumo
   realizado de POIs, conservando sus cuotas y ubicaciones.
2. Se agregan objetivos de destino solicitados y efectivos. Solo se corrige ruido
   acumulado de suma float32 dentro de un límite comprobado, antes de fijar objetivos.
3. Se prohíben auto-viajes y cruces entre zonas aisladas. El respaldo preexistente
   a cinco vecinos para filas/columnas sin soporte se registra y también excluye self.
4. Se redondean conjuntamente los destinos a piso/techo, con suma exacta por zona
   y factibilidad sobre el soporte permitido.
5. Se repara la propuesta mediante flujo residual entero sobre soporte disperso,
   ampliándolo con vecinos permitidos si hace falta. No se afirma optimalidad
   global de costos. Una incompatibilidad completa produce diagnóstico explícito.
6. Se divide o fusiona cada par O/D por separado. El máximo de cohorte es obligatorio;
   el mínimo es una preferencia. Un residuo pequeño nunca se mueve a otro par.

Las cohortes conservan exactamente los presupuestos enteros de origen, objetivos
enteros de destino y masas por par. Se comparan contra vectores independientes
antes de retirar huérfanos, después de empaquetar y leyendo el JSON y ZIP finales.
El error de redondeo fraccionario se reporta por separado del residual entero.

El campo de display `jobs` representa llegadas realizadas del modelo, no empleo
CE observado. Esta corrección no acredita automáticamente la categoría
`synthetic_measured_marginals` ni aumenta un tier de calidad.

## Rutas, exportación y Wizard

OSRM enriquece los pares finales con distancia y tiempo vial sin alterar sus
cantidades. Se usa respaldo canónico cuando corresponde y se registra su motivo.
Las zonas aisladas, exclusiones, POIs, proyección y política de empleo conservan
sus contratos independientes.

El Wizard compila en una carpeta de build aislada y vincula la descarga a proyecto,
configuración, resultado y hash. Su preview lee ese mismo artefacto y valida el
contrato de asignación; no ejecuta un segundo simulador distinto. Los diagnósticos
permanecen en sidecars y no añaden campos al esquema de demanda del juego.

## Evidencia de la revisión CUR y MID

CUR conserva 898,658 viajeros (848,780 continente y 49,878 Cozumel), con 2,710
puntos y 27,966 cohortes. Cero diferencias contra los márgenes enteros autorizados,
auto-viajes o cruces de zonas. Las cuotas de los 13 POIs permanecen conservadas.
La prueba adicional MID conserva 799,587 viajeros y usa el respaldo DENUE.

La comparación de fases CUR midió 91.51 s frente a 105.74 s de referencia y
prácticamente el mismo pico de memoria. Pasaron 103 pruebas de corrección y las
comprobaciones de preview/descarga de ambos paquetes. El usuario confirmó que el
candidato funciona en el juego; esa evidencia es reportada por el usuario, no
una prueba automatizada de importación, guardado o recarga.

Estos resultados corresponden a builds locales verificados. Actualizar el código
del pipeline no sustituye automáticamente un paquete publicado en el registry.

La implementación y contrato detallados están en [docs/od-integer-allocation.md](docs/od-integer-allocation.md).
Los reportes reproducibles locales están en `reports/od-marginals-correction/`.
