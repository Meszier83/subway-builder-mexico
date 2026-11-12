# POIs universitarios: investigación con IA y revisión en el Wizard

Flujo aprobado con el caso de Torreón el **31 de octubre de 2026**. Se usa una
guía para pedir la investigación a una IA con navegación y revisar su propuesta
en el Wizard. No requiere un generador nuevo.

## Criterio de demanda

Para campus nuevos, la **matrícula escolarizada de educación superior** es la
aproximación de demanda estudiantil. Se incluyen TSU, licenciatura, normales y
posgrado del campus; se excluyen alumnos de secundaria o preparatoria.

1. Incluir únicamente campus con **al menos 500** estudiantes en esta base.
2. Aplicar el umbral **antes** de redondear.
3. Redondear la cuota `jobs` al múltiplo de **50** más cercano:
   `jobs = 50 * floor((matricula_escolarizada + 25) / 50)`.
4. Registrar matrícula mixta y no escolarizada por separado, sin sumarlas a la base.
5. Usar **BOOST** para sumar la cuota estudiantil al empleo DENUE capturado. No
   agregar otro cálculo de docentes o personal sobre esa cuota.
6. Para puntos nuevos, **100 m** es el radio inicial utilizado en este flujo.
   Conservar radios y posiciones ya corregidos; ajustarlos en el Wizard cuando haga falta.

| Matrícula escolarizada | Decisión | `jobs` propuesto |
|---:|---|---:|
| 493 | No incluir: no alcanza 500 | — |
| 514 | Incluir | 500 |
| 896 | Incluir | 900 |
| 5,139 | Incluir | 5,150 |

Es una aproximación práctica para diseñar el mapa. **No se intenta estimar la
asistencia diaria exacta** mediante horarios, prácticas clínicas, residencias,
internados o uso actual del transporte público. Tampoco se duplican las cifras
para representar el viaje de regreso. La matrícula no es una medición de viajeros.

Este umbral se aplica a las propuestas de universidades. No cambia las reglas de
aeropuertos u otros tipos de POI, ni recalcula automáticamente los mapas existentes.

## Investigación suficiente

- Empezar con **ANUIES** para matrícula por campus, nivel y modalidad. Contrastar
  identidad y ubicación con DENUE, SIC y directorios oficiales de las instituciones.
- Cubrir todos los estados y municipios que atraviesa el área de demanda del mapa.
- Agrupar facultades en un mismo campus físico; separar sedes alejadas. No repetir
  una matrícula institucional completa en cada sucursal.
- Si falta la matrícula, usar una estimación identificada como tal cuando tenga
  una base defendible: una cifra anterior del campus, una distribución documentada
  o una comparación pertinente. Explicar brevemente la cuenta y su fuente.
- Cuando no haya evidencia suficiente, dejar el caso pendiente en la investigación.
  **No asignar cero ni inventar 500 para que pueda entrar.**
- Entregar ubicación WGS84 en grados decimales: **`loc: [longitud, latitud]`**.
  Una posición aproximada sustentada es suficiente para corregirla después en el
  Wizard; no hace falta investigar la puerta exacta. Evitar centros de ciudad o
  puntos repetidos usados como sustitutos de un campus desconocido.

La [guía detallada de investigación](college-poi-ai-research-guide.md) conserva
las comprobaciones de fuentes, identidad y estimaciones. El criterio sencillo
de esta página es la convención predeterminada para las propuestas nuevas.

## Prompt listo para copiar

```text
Investiga POIs universitarios para este mapa de Subway Builder:
- Ciudad/región y país: [REGIÓN]
- Configuración, BBOX y polígono del área de demanda: [ARCHIVO O DATOS]
- Ciclo escolar de referencia: [CICLO]
- POIs existentes y correcciones manuales: [ARCHIVO O LISTA]
- Campus descartados durante revisiones anteriores: [LISTA O NINGUNO]

Investiga campus físicos con ANUIES y otras fuentes oficiales. Incluye todos los
estados que atraviese el mapa, TSU, normales, licenciatura y posgrado. Agrupa
facultades del mismo campus y no copies totales institucionales en varias sedes.
Separa la matrícula escolarizada, mixta y no escolarizada; excluye preparatoria.
Conserva la fuente, ciclo y alcance de cada cifra. Si falta matrícula, permite
estimaciones justificadas y etiquetadas; si no hay base suficiente, déjala pendiente.

Usa esta convención sencilla: matrícula escolarizada como aproximación de demanda;
mínimo 500 ANTES de redondear jobs al múltiplo de 50 más cercano. No sumes matrícula
mixta o no escolarizada a esa base. No investigues ajustes individuales de asistencia,
horarios, prácticas clínicas o alojamiento. No agregues multiplicadores de personal.
Propón mode: BOOST y radius_m: 100 para puntos nuevos. Conserva los radios existentes.

Entrega IDs legibles UNI_, type: uni y coordenadas numéricas WGS84 en formato
loc: [longitud, latitud]. Una posición aproximada sustentada basta para revisarla
después en el Wizard. Preserva IDs, nombres y posiciones ya corregidos. No vuelvas
a incorporar campus descartados manualmente sin que se solicite.

Devuelve: (1) tabla de campus con matrícula, fuentes y demanda propuesta;
(2) YAML con solo los campus que cumplen el mínimo y tienen ubicación y demanda
resueltas; (3) lista separada de campus pequeños, descartados y pendientes con motivo;
(4) conteos y total de jobs propuestos. Aclara que jobs es una aproximación para el
mapa, no una medición de viajeros. Mantén los archivos del mapa sin cambios durante
la investigación; la aplicación al Wizard es un paso posterior.
```

## Aplicación y revisión en el Wizard

1. Preparar la lista y revisar las fuentes principales. Conservar por separado el
   inventario completo y los descartes: el filtro no debe borrar la investigación.
2. Al aplicar la propuesta, respaldar la ciudad y sustituir solamente los campus
   comprendidos en esa revisión. Conservar otros POIs y la configuración del mapa.
3. Abrir **POI Studio**, revisar las posiciones, corregir discrepancias y retirar
   campus cuando la revisión lo justifique. Esos ajustes prevalecen al repetir el proceso.
4. Guardar y recargar: comprobar conteo, cuotas, coordenadas y notas de procedencia.
   La validación estática complementa esta revisión; no demuestra carga en el juego.

El Wizard permite `jobs: null` para revisar un campus con cuota pendiente y bloquea
la compilación mientras siga pendiente. La propuesta final debe resolverlo o dejarlo
fuera de la lista activa; no sustituirlo por cero.

En V2, los POIs reservan atracción dentro del presupuesto de viajeros existente;
no crean una población estudiantil independiente. Con BOOST, el total final puede
superar la suma manual al incorporar el DENUE del radio. Ver el
[contrato de cuotas especiales](demand-od-rounding.md).

## Caso de Torreón

La investigación del ciclo **2024–2025** localizó 78 candidatos: 75 con referencias
de matrícula y tres pendientes. El filtro produjo **30 campus**, 45 por debajo del
mínimo y tres pendientes. Retuvo el **89.44%** de la matrícula escolarizada del
inventario localizado: 49,966 alumnos, convertidos en **50,050 unidades manuales**
después de redondear. No se usaron estimaciones de matrícula en esos 30 casos.

Esa fue la carga inicial verificada en el Wizard. **La revisión posterior del mapa
prevalece sobre esa propuesta:** en la instantánea consultada el 2026-10-31,
`cities/torreon.yaml` contiene
25 universidades con **43,600 unidades manuales**, todas en BOOST y con radio de
100 m. Hay además un POI de aeropuerto, fuera de este criterio universitario.

Estos cinco candidatos de la propuesta inicial ya no están en la configuración
revisada; no deben reaparecer automáticamente al repetir la investigación:

- `UNI_DES Instituto 18 de Marzo`
- `UNI_IES Ignacio Zaragoza Torreón`
- `UNI_María Esther Zuno`
- `UNI_Vizcaya Torreón`
- `UNI_Politécnica Región Laguna`

La diferencia documenta la revisión del mapa; no establece un nuevo criterio
general de exclusión ni atribuye un motivo que no quedó registrado.

La [investigación y propuesta inicial](../reports/college-poi-ai-pilot/TORN/demand-review/README.md)
y la [verificación de su aplicación](../reports/college-poi-ai-pilot/TORN/demand-review/wizard-application-20261031T223519Z/verification.json)
son evidencia local bajo `reports/`, que no se versiona. Este resumen conserva el
procedimiento y las decisiones en la documentación del repositorio. No se afirma
una nueva compilación o validación dentro del juego.
