# Detalle CE en el candidato v2

Con `demand.engine: v2` y `macroeconomics.workplace_employment: auto`, el
candidato lee los niveles publicados de SCIAN: sector, subsector, rama,
subrama y clase. El legado conserva su política laboral. Los métodos laborales
explícitos guardados también se respetan.

## Selección y significado

Por municipio se construye una partición de actividades sin solapamiento: cuando
hay descendientes publicados, el padre deja de ser un control aplicable. Cada
establecimiento DENUE se asigna a una sola actividad de esa partición mediante
su código de seis dígitos. Una clase reservada o sin detalle de tamaño conserva
su estimación DENUE; no se rellena con el promedio del sector ni se reconstruyen
celdas confidenciales restando hijos de padres.

Para cada grupo municipio/actividad/tamaño utilizable, el objetivo histórico es
`establecimientos DENUE elegibles × personal CE / unidades económicas CE`.
Se distribuye proporcionalmente a los pesos DENUE, respetando sus bandas
individuales. Si el promedio contradice la banda CE o el objetivo excede las
capacidades DENUE, se conserva la estimación anterior y se reporta el motivo.
Esto transfiere una intensidad histórica a un conjunto actual de unidades;
no fuerza los totales actuales a igualar CE ni mide empleados por edificio.

La banda 251+ tiene mínimo 251 y máximo abierto. El peso inicial 450 continúa
siendo una estimación de respaldo, nunca un límite superior. Un promedio
histórico válido puede superar 450; las cifras individuales siguen siendo
estimadas y no se consideran observadas.

La política de unidad censal sigue limitada a los sectores 31–33, 43, 46, 53 y
72 y al ámbito privado/paraestatal reconocido. Los registros excluidos o de
ámbito incierto conservan estimaciones declaradas. Se exige un solo año CE para
los controles pertinentes; cifras contradictorias detienen el build.
La comparabilidad de edición, cobertura rural y universo permanece condicional.
No hay expansión TIL1 ni nuevas ubicaciones laborales sintéticas.

## Adquisición y reportes

El descargador CE conserva las dos consultas sectoriales existentes y añade
otras dos consultas con el árbol SCIAN de esos sectores para ambos motores,
con y sin tamaño. Se valida año, municipio, actividad, número de filas y
procedencia antes de publicar los CSV. El lector también acepta descargas
manuales equivalentes; poseer un CSV no demuestra cobertura suficiente.

`demand_pipeline_report.json`, bajo `workplaces`, identifica fuentes y código por
hash, grupos utilizados, reservas, incompatibilidades y respaldos. La cobertura
se expresa por registros y por peso laboral, para todo el archivo DENUE y para
la caja del mapa. Es peso laboral previo al recorte urbano y tratamiento de
POIs; no es proporción de viajeros exportados ni empleo observado.

## Comprobación de CUR, etapa 2

La comparación aislada está en `reports/demand-playable/2/CUR/`. Dentro de la
caja hay 53.472 establecimientos DENUE y 452.624,89 de peso laboral. Se aplica
transferencia a 20.785 unidades con 157.412,13 de peso (34,78%): 19.929 unidades
usan clase y 856 sector. Las restantes mantienen el respaldo declarado.

El legado conserva exactamente su `demand_data.json` de referencia: 766.052
viajeros. El candidato conserva 766.033 viajeros y 15.322 cohortes de tamaño
configurado 50. El KL municipal reservado en cobertura común baja de 0,12293
a 0,04295, frente a 0,07453 del legado. Ese resultado no cierra los problemas
espaciales, de POIs o la validación dentro del juego.
