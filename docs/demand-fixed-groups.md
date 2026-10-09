# Tamaños fijos de cohortes — candidato v2

## Contrato vigente desde la etapa 3

El tamaño 50/100/200 es el máximo y objetivo de una cohorte completa. Se admiten
restos locales adicionales: ya no se completa un grupo con residentes lejanos.
La partición incluye municipio, zona aislada y componente vial. Cada contribución
se representa en una ubicación residencial existente, a un máximo de **500 m**
medidos con la geodésica WGS84. Vecinos encadenados tampoco pueden superar ese
límite respecto al ancla. Los orígenes especiales de POIs no se agrupan.

El límite mide solamente el desplazamiento añadido desde el punto estadístico
consolidado hasta el ancla de la cohorte. No incluye la posición censal, malla,
consolidación o snapping anteriores y no garantiza acceso a una estación.
El modo adaptativo conserva directamente los puntos estadísticos en esta etapa.

La selección es determinista, conserva población y viajeros y reconstruye el
presupuesto de cada origen mediante `source_contributions`. El reporte registra
coordenadas, municipio, zona y componente de cada contribución, desplazamiento
máximo, grupos completos y restos. Los originales permanecen en
`demand_model.json/statistical_points`. La interfaz explica el límite y los
restos; hasta evaluar el candidato, el conteo por división global se muestra
como mínimo y no como conteo final.

La comparación espacial de etapa 3 usa las fuentes y política laboral de etapa 2 y está
en `reports/demand-playable/3/CUR/verification.json`. No migra proyectos ni cambia
el legado. La entrega desde Wizard y la aceptación dentro del juego continúan
pendientes de sus etapas. No se exige mejorar el rendimiento respecto al legado.

| Modo de CUR en etapa 3 | Cohortes | Máximo añadido por agrupar |
| --- | ---: | ---: |
| 50 | 15.425 | 496,65 m |
| 100 | 7.772 | 499,51 m |
| 200 | 3.970 | 499,87 m |
| Adaptativo, objetivo/máximo 200 | 4.945 | 0 m |

Todos conservan 766.033 viajeros. Tamaño 50 añade 103 cohortes respecto a la
etapa 2. El ajuste municipal empeoró: KL 0,12317 frente a 0,04295 anterior y
0,07453 del legado. La etapa 4 corrigió esa pérdida y los extremos coincidentes:
con objetivo/máximo 50 exporta 19.607 cohortes, 4.182 adicionales por conservar
relaciones municipales y cuotas de POIs. Mantiene 766.033 viajeros y 496,65 m
de máximo añadido. KL final: 0,046655. Véase `demand-od-rounding.md` y
`reports/demand-playable/4/CUR/verification.json`. Los conteos anteriores son
históricos; cumplir el radio residencial por sí solo no acredita corrección OD.

## Referencia histórica anterior a la corrección espacial

Las cifras y descripción siguientes pertenecen al agrupamiento global anterior.
Se conservan como referencia; no describen el contrato vigente ni su cobertura CE.

En el Wizard, seleccionar el motor candidato v2 y **Modo rígido → Tamaño fijo**.
El valor representa personas por cohorte: por ejemplo, 50, 100 o 200. El objetivo
de número total queda desactivado en este modo. En YAML puede usarse
`demand.fixed_cohort_size: 200`; no puede combinarse con `demand.cohort_count`.
El motor anterior sigue activo por defecto. No se han migrado proyectos.

## Resultado reproducido en Cancún/Riviera Maya

Todas las ejecuciones conservan exactamente **766.033 viajeros**. El escenario
sin calles separa las dos zonas configuradas; la ejecución con calles también
separa los componentes viales. Se utiliza conducción canónica de respaldo,
identificada en los informes, no tiempos OSRM medidos para estos resultados.

| Tamaño | Cohortes completas | Restos | Total de cohortes |
| ---: | ---: | --- | ---: |
| 50 | 15.319 | 40 y 43 personas | 15.321 |
| 100 | 7.659 | 40 y 93 personas | 7.661 |
| 200 | 3.829 | 93 y 140 personas | 3.831 |
| 200, con calles reales | 3.829 | 29, 64 y 140 personas | 3.832 |

Se forman grupos a partir del presupuesto completo de los orígenes ordinarios
de cada territorio permitido, antes de resolver OD. Los grupos comparten una
coordenada residencial existente, elegida entre sus contribuyentes. Después de
asignar destinos, el exportador conserva los pares y tamaños. Los restos no se
eliminan ni cruzan zonas aisladas o componentes viales para completar grupos.
Los orígenes protegidos de POIs mantienen su tratamiento y pueden añadir restos
propios; sus cifras, reglas e IDs no se reinterpretan.

## Coste de representación y límites

Exigir grupos globales completos reduce el número de cohortes, pero desplaza
la representación residencial de parte de los viajeros. En la ejecución de 200
con calles, la distancia máxima entre una residencia original y la coordenada
representada es **38,00 km**. El desplazamiento ponderado suma **37.080,87
persona-km**, equivalente a 48,41 metros por viajero contando también a quienes
no se desplazaron. Este promedio no hace irrelevante el máximo.

El informe conserva las contribuciones originales de cada grupo, sus distancias
y los cambios municipales de representación. `demand_model.json` conserva
`statistical_points` con la población y los viajeros originales, separados de
los puntos representados para el juego. Se comprueba la reconstrucción exacta
de cada presupuesto original y se verifica que el agrupamiento no oculte un
origen sin destinos permitidos.

No se afirma que esta representación sea más precisa. Quien priorice la
localización residencial puede utilizar el modo adaptativo; imponer un radio
duro adicional al agrupamiento exigiría aceptar más restos y más cohortes.

## Verificación y entrega

- 101 pruebas Python aprobadas: núcleo, tamaños 50/100/200, restos, presupuestos,
  determinismo, restricciones, POIs, vista previa/exportación y entrega Wizard.
- Comprobaciones de interfaz del Wizard aprobadas, incluyendo carga de tamaño
  explícito, cambio de tamaño y desactivación del objetivo total en modo rígido.
- Las cuatro ejecuciones tienen identidades de código/configuración/fuentes y
  hashes de salida comprobados en `reports/demand-v2/fixed-verification.json`.
- El paquete aislado `reports/demand-v2/fixed-200-roads/CUR.zip` supera la
  validación estática de integridad, referencias, pertenencia y viajeros. La
  cartografía reutilizada es idéntica byte a byte a la del artefacto anterior.

Resultados: `reports/demand-v2/fixed-{50,100,200}/fixed-summary.json` y
`reports/demand-v2/fixed-200-roads/fixed-summary.json`. Logs:
`fixed-regression.log` y `fixed-ui.log`. No se modifica la entrega activa.
Falta verificar carga, simulación, rendimiento y guardado/recarga en el juego.
