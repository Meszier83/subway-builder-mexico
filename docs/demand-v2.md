# Motor candidato de demanda

El motor activo se conserva. `demand.engine: v2` selecciona el candidato; volver a
`legacy` restaura el anterior. No migra ciudades ni reinterpreta POIs.

Resultados medidos y límites de aceptación: [Estado del candidato](demand-v2-acceptance.md).

```yaml
demand:
  engine: v2
  target_year: 2025
  boundary_policy: closed
  cohort_count: null  # automático por tamaños del Wizard; un entero exige ese total exacto
  beta: null  # ajustar con flujos municipales EIC; respaldo declarado 0.12/km
  max_distance_km: null  # sin prohibición por distancia
  sources:
    cpv: [data/PROYECTO/cpv.csv]
    denue: [data/PROYECTO/denue.csv]
    marco: [data/PROYECTO/marco/conjunto_de_datos/23m.shp]
    ce: [data/PROYECTO/SAIC.csv]
    eic_indicators: [data/eic2025/conjunto_datos_eic2025_105.csv]
    eic_persons: [data/eic2025/personas23.csv]
```

Sin `sources`, el adaptador resuelve los mismos archivos del proyecto y la raíz
nacional compartida, y usa las rutas EIC configuradas en Macroeconomía. El núcleo
recibe rutas explícitas y no busca archivos. Las rutas relativas parten de la raíz
del repositorio. `source_metadata` admite edición y año real por rol. Una edición
DENUE no identificada aparece como desconocida; no se declara de 2025.

El año 2025 requiere EIC completa para todos los municipios representados. El
escenario 2020 puede usar CPV, declarando que toma ocupados como viajeros por falta
de movilidad equivalente. Otros años se rechazan en esta versión.

`build_demand(request, stage)` comparte las etapas `sources`, `population`,
`points`, `allocation`, `export`. Los resultados conservan evidencia, estimaciones
y demanda del juego separados. La vista previa `points` no ejecuta asignación ni
ruteo. La vista `allocation` incluye evaluación de movilidad.

La conciliación usa municipios completos y una categoría residual no espacial.
Los ocupados se ajustan dentro de la población por unidad. El recorte no absorbe
la masa exterior o no localizada. Los viajeros retenidos reciben destinos dentro
del mapa; la fracción municipal de viajes externos redistribuidos es una estimación
para el recorte, no un flujo BBOX observado.

El adaptador reutiliza la malla, zonas y POIs actuales y finaliza las coordenadas
antes de asignar. La etiqueta municipal de un punto consolidado se estima mediante
la referencia fuente más próxima. Las componentes de vértices viales limitan el
soporte cuando se proporcionan calles; sin calles se reporta conectividad no verificada.

`routing.demand_roads_path` permite proporcionar una red completa para calcular
demanda cuando `roads.geojson` del mapa está reducido para su visualización.
Preview y build leen la misma ruta; una ruta explícita ausente falla. Este insumo
no reemplaza las capas del paquete. La identidad candidata incluye su geometría;
las rutas vehiculares finales continúan comprobándose con OSRM.
Si un origen queda sin destinos por la partición geométrica, un proveedor OSRM
configurado puede comprobar hasta cinco destinos próximos de la misma zona.
Sólo une componentes cuando ambas direcciones son rutas aceptadas; ningún
respaldo estimado autoriza la unión. El reporte conserva esa evidencia y se
mantienen el presupuesto, las coordenadas y las restricciones de zonas.

La asignación conserva los orígenes exactamente y utiliza todos los destinos
permitidos por las restricciones territoriales y de distancia. Normaliza los pesos
laborales al presupuesto ordinario de cada componente del soporte y conserva esas
proporciones en la solución continua. Son márgenes del modelo, no capacidades
laborales observadas. Los pesos sin origen accesible se declaran excluidos; un
reparto incompatible impide exportar. Los flujos municipales permanecen como
objetivos suaves. La precisión de las celdas OD usa tamaño efectivo
Kish, no intervalos oficiales del diseño muestral. El 20% de identidades EIC se reserva
determinísticamente para evaluación. El score compara observaciones municipales
completas con un modelo del área retenida y tiene esa limitación.
El informe distingue evaluación OD continua y evaluación después del redondeo;
conservar presupuestos no garantiza conservar proporciones municipales. La
comparación usa además flujos exportados y cobertura municipal común entre motores.

El optimizador se inicializa con 30 pasos de escalado iterativo del mismo
objetivo dual, conservando sus límites y criterios de convergencia. Se registra
el objetivo antes/después; la inicialización no sustituye la optimización ni
autoriza exportar si esta falla. El log indica cada 25 iteraciones del cálculo.
La escala numérica usa la diagonal del Hessiano en esa inicialización, incluyendo
el acoplamiento de destinos que comparten un origen y grupo municipal.

En modo adaptativo, los controles del Wizard `min_pop_size`, `target_pop_size` y `max_pop_size`
gobiernan el candidato. La resolución se decide durante la asignación: se calcula
el número de cohortes por origen a partir del objetivo y se asignan sus destinos
después de conservar el reparto municipal en personas enteras. Dentro de cada
parte municipal se forman los tamaños necesarios. Una asignación conjunta por
tamaño selecciona trabajos y arrastra los errores de redondeo hacia los tamaños
restantes, conservando los presupuestos y el inventario de cohortes. El informe
mide los errores finales por destino; no promete igualdad exacta entre los
márgenes fraccionarios y las llegadas de cohortes indivisibles.
Se permiten restos para relaciones menores que el tamaño preferido. Después se
congela la matriz OD entera; el exportador conserva exactamente sus pares,
tamaños y número de cohortes. No hay una cohorte obligatoria por cada fracción de
la matriz continua.

`demand.cohort_count` permite solicitar un total exacto desde el Wizard. El total
se reparte entre orígenes conservando el presupuesto de cada uno y el máximo por
cohorte. El mínimo factible incluye cada parte municipal y segmento reservado
de POI: la suma de `ceil(viajeros_parte / max_pop_size)`;
el máximo es el número de viajeros. Una solicitud fuera del intervalo bloquea la
asignación e informa ambos límites. El mínimo de tamaño es una preferencia cuando
hay total explícito; no se eliminan orígenes ni restos pequeños.

En el Wizard, **Modo rígido → Tamaño fijo** selecciona un tamaño objetivo de 50, 100,
200 u otro tamaño positivo. También se puede usar `demand.fixed_cohort_size: 200`.
El motor agrupa viajeros antes de asignar destinos, a un máximo de 500 m de
cada punto estadístico contribuyente al ancla existente, por geodésica WGS84.
No mezcla municipio, zona aislada ni componente vial. Conserva tantos restos
locales como sean necesarios para respetar esos límites; no promete un único
resto global ni por territorio. Las excepciones de POIs se conservan.
El campo de número total queda desactivado
en modo fijo; la configuración de ambos a la vez se rechaza.

La representación de orígenes puede cambiar al reunir viajeros de ubicaciones
próximas: se usa una ubicación fuente existente y se registra la contribución
de cada origen, el desplazamiento y la conservación de etiqueta municipal. Los puntos
estadísticos anteriores se conservan en `demand_model.json.statistical_points`;
no se altera evidencia ni población estadística. Esta resolución puede afectar
distancias y cobertura local, sobre todo en áreas dispersas. Las coordenadas se
congelan antes de OD y el empaquetado no mueve viajeros entre pares. Los sitios
ordinarios exportados que comparten exactamente coordenadas y territorio se
unifican. `demand_model.json.export_point_mapping` permite reconstruir los
presupuestos y celdas originales; los IDs especiales se conservan separados.

`demand.beta` ausente o `null` solicita calibración, sin heredar la beta histórica.
`max_distance_km` y `max_pop_size` prevalecen sobre sus equivalentes macro. `null` en distancia
elimina el límite. Tasa PEA, TIL1, crecimiento, Furness y consolidación posterior
no gobiernan el candidato y se listan como no aplicables. Los POIs conservan todos
sus valores y reglas de captura; desde etapa 4 se reserva la cuota resultante
antes de la demanda ordinaria. No hay módulos nuevos de estudiantes o pasajeros.
Véase [OD entero y cuotas](demand-od-rounding.md).

El ruteo usa la preparación OSRM existente cuando hay PBF y Docker disponibles.
Un proveedor explícito `routing.osrm_url` requiere `routing.network_identity`
para identificar la red y el perfil usados. La caché
incluye esa identidad, coordenadas finales y opción de geometría; solo guarda rutas
OSRM verificadas. Sin proveedor usa respaldo canónico declarado. Un `no_route`
confirmado impide exportar ese resultado.

El Wizard ofrece el selector del motor y evaluación del candidato sin cambiarlo.
La evaluación usa un trabajo en segundo plano y consultas breves de estado,
para que modelos grandes no pierdan la respuesta por una conexión HTTP inactiva.
Los resultados se vinculan al proyecto y configuración guardada; cambiar la
configuración impide publicar el resultado anterior en la vista previa.
POI Studio usa la misma etapa de puntos al seleccionar v2. El bloque `demand`
se conserva al guardar. La compilación produce demanda, informe y manifiesto;
el empaquetado conserva el contrato anterior.

Comparación aislada, sin migración:

```powershell
python tools/compare_demand_v2.py cities/cancun_riviera_maya.yaml --output reports/demand-v2/cur
```

Para comparar años iguales, ambos motores deben recibir EIC 2025 en la
configuración de comparación. La herramienta desactiva únicamente servicios Docker
durante la referencia, usa respaldo de conducción en ambos y no cambia la matemática
del motor anterior. `--roads` suministra la misma geometría a ambos.

La validación estática no confirma juego: la carga, simulación y guardado/recarga
en Subway Builder siguen siendo requisito previo a una migración.
