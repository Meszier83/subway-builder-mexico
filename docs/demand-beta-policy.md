# Fricción automática de demanda v2

La compilación v2 usa `demand.beta_selection: reference` por defecto (también
cuando falta ese campo): una asignación con 0,120, declarada como referencia,
sin afirmar calibración. Un `demand.beta` positivo prevalece en modo manual.
No cambia el motor legado. La política automática `final_integer_60_20_20_v1`
se activa explícitamente con `demand.beta_selection: automatic`; sin beta manual,
utiliza 0,120 como referencia y
considera 0,060; 0,090; 0,180 y 0,240 como alternativas. Un `demand.beta` positivo
explícito conserva el modo manual; el deslizador `macroeconomics.gravity_beta`
continúa siendo exclusivo del legado.

## Datos separados y decisión

El hash SHA-256 de `(entidad, ID_PERSONA)`, módulo 10, asigna los grupos 0–1
a comprobación, 2–3 a selección y 4–9 a entrenamiento. La división es estable
ante reordenamiento, fragmentación o duplicación coherente de archivos. Solo
los pesos de entrenamiento entran en los objetivos municipales del solver.
Población, ocupados y presupuestos residenciales siguen usando los controles
completos: separar la evidencia OD no elimina viajeros.

Las comprobaciones usan las probabilidades municipales de las celdas **enteras
finales**, incluidas las reservas de POIs. Todos los candidatos tienen los mismos
presupuestos, atracciones estimadas, restricciones y configuración de cohortes.
La pérdida es logarítmica ponderada por FACTOR. Las comparaciones de candidatos
comparten observaciones y cobertura; un cero del modelo tiene piso 1e-12, sin
crear viajes ni alterar la exportación.

Se exige tamaño efectivo Kish de al menos 30 tanto en selección como en
comprobación y más de un destino municipal accesible para algún origen.
Este mínimo es una regla de ingeniería, no una garantía de identificación.
Si el mapa solo representa un municipio, no se declara calibración automática.

Se calcula la mejora pareada con 200 réplicas Poisson de registros, semilla
60120, respetando el FACTOR individual mediante histogramas de pesos por par.
La regla de un error estándar elige el valor más cercano a 0,120 entre candidatos
indistinguibles del mejor en selección; el empate se resuelve por valor menor.
Solo se adopta ese candidato si su percentil 5 de mejora en comprobación es
estrictamente positivo. Si no, se conservan las celdas exactas de la referencia.
No se reajusta con los registros de selección/comprobación tras decidir.

Los percentiles son diagnósticos de estabilidad empírica: **no** son intervalos
de confianza del diseño muestral EIC. La partición es por persona, no por
conglomerado censal. Los dos conjuntos reservados participan en la decisión;
`validation` y `validation_integer` reúnen esos conjuntos y no deben presentarse
como una prueba independiente de rendimiento posterior a la selección.

## Límites y errores

La evaluación está condicionada a municipios de origen/destino representados.
Se registra el peso externo/no representado excluido. Los flujos de municipios
completos no observan exclusivamente el BBOX retenido; la comparación no prueba
precisión entre colonias ni longitudes reales. Los tiempos multimodales EIC no
se convierten en distancias de automóvil. 0,120 es un respaldo declarado, no un
valor universal demostrado.

Si una alternativa no converge, se excluye y se registra el error. Si la
referencia tampoco puede cumplir los contratos, la ejecución falla y no produce
una exportación. La política no modifica márgenes laborales, cuotas POI, zonas,
coordenadas o límites para aparentar factibilidad.

El soporte se reutiliza entre candidatos de una misma selección; fuentes y
geometría no se vuelven a leer. No se ejecuta OSRM cinco veces: se rutea el
candidato elegido. Un `NoRoute` confirmado vuelve a ejecutar toda la selección
sobre el soporte corregido, con un nuevo respaldo en ese mismo soporte. Se
conserva el límite existente de ocho intentos de ruteo. Los tiempos de evaluación
y las alternativas fallidas quedan en el reporte.

La caché `.demand-v2-beta-cache.json` del proyecto guarda celdas e informe de
la selección automática. Su clave incluye identidad de código/fuentes/geometría,
parámetros efectivos de asignación, puntos, evidencia y pares prohibidos; se verifica
además el checksum del contenido antes de reutilizarlo. Un cambio invalida la
caché. No se reutiliza para un beta manual, ni se omiten ruteo, comprobación de
exportación o comprobación final de cambios en fuentes. Una escritura de caché
fallida se reporta sin impedir una demanda válida. El primer cálculo automático sigue
requiriendo hasta cinco asignaciones completas: no se promete calibración rápida.
Cambiar solo la URL de OSRM o incluir geometría de conducción no invalida la
asignación anterior al ruteo. Los nuevos `NoRoute` sí cambian la clave. La
identidad completa de la petición y el chequeo final de fuentes siguen vigentes.

## Sensibilidad rápida

El botón habitual solicita `stage=sensitivity`, sin asignación, cohortes finales,
OSRM ni exportación. Sobre puntos preparados antes de resolver grupos fijos,
sortea 512 orígenes con reemplazo, ponderados por viajeros, con semilla 60120.
Para cada uno compara 0,060; 0,120 y 0,240 frente a todos los destinos permitidos
por las mismas restricciones geográficas del solver. Las preferencias se normalizan
por origen; cada sorteo representa la misma fracción del total retenido.

No balancea marginales laborales ni aplica reservas finales de POIs. Conserva
masa estimada, sin producir celdas OD exportables ni cambiar la beta configurada.
Las distancias son geométricas: mediana y P90 aproximados con intervalos de 250 m,
y proporción de más de 20 km. Los destinos se agregan por municipio y celdas de
2 km en proyección local equirectangular. Se señala variación importante con
cambio relativo de mediana/P90 >=10%, cambio de cola >=10 puntos porcentuales,
o variación total de destinos >=0,10 frente a 0,120. Son umbrales de ingeniería.

Sin cobertura de todos los municipios residenciales, sin soporte de un origen
muestreado o al agotar 15 s, devuelve inconcluso y ninguna comparación parcial.
El límite se comprueba entre orígenes y al terminar; una operación individual
puede rebasarlo antes de la siguiente comprobación. La preparación de fuentes
y puntos se mide aparte y no tiene este límite. La cobertura informa orígenes
distintos, su masa residencial y municipios omitidos; no afirma cobertura total
de todos los hogares. La caché `.demand-v2-sensitivity-cache.json` verifica identidad
de fuentes/código/configuración/puntos y checksum; solo guarda resultados completos.
Poca variación en esta aproximación no demuestra asignaciones finales iguales,
exactitud de demanda ni mejor jugabilidad.

## Interfaz y evidencia

En v2 se ocultan tasas ENOE, fricción del legado, laboratorio modal ignorado y
proyecciones CONAPO; se conservan en configuración y regresan al apagar v2.
Después de asignar o compilar, el panel puede mostrar la fricción final, su procedencia, motivo y distancias
geométricas ponderadas por viajeros (mediana y percentil 90). Antes de evaluar
indica pendiente. Cambiar de proyecto o configuración invalida el resumen.
La compilación también devuelve estos campos al visor. Los diagnósticos no
cambian el esquema de demanda del juego ni agregan reportes a su ZIP.
Durante la sensibilidad se muestran preparación, avance por orígenes y tiempo
transcurrido. La calibración avanzada conserva el progreso de hasta cinco opciones.
Las tareas HTTP no esperan una conexión abierta
durante toda la asignación.

`beta_policy` y `distance_summary` quedan en `demand_pipeline_report.json`.
`python tools/verify_beta_policy.py --snapshot <build> --output <directorio>`
relee EIC y compara la asignación completa sobre un modelo espacial guardado,
registrando hashes, tiempos, viajeros, cohortes y pérdida del respaldo/elegido.
Esta reproducción no reemplaza una nueva ingesta, entrega HTTP ni prueba de juego.
