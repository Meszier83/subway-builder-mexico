# Demanda gravitatoria: contratos, limites y cohortes

Inspeccionar `sb_mexico/gravity.py` y `sb_mexico/pipeline.py` antes de modificar
el modelo. Esta referencia describe el checkout revisado; confirmar las funciones
vigentes cuando cambie el codigo. La conservacion global no demuestra balance OD.

## Malla y presupuesto

`build_demand_grid` agrupa registros en celdas segun `grid_size` y calcula
posiciones ponderadas. Los grados no equivalen a una distancia metrica uniforme:
el espaciamiento longitudinal depende de la latitud.
Registrar PEA antes y despues de BBOX, exclusiones, nucleo urbano y consolidacion.
El presupuesto que el pipeline compara con `sum(pop["size"])` es la suma de
`pea_15ymas` de los puntos retenidos, no toda la poblacion censal original.

## Asignacion especial y regular

`simulate_gravity_demand` separa POIs con `is_special` de empleos regulares.
Los POIs consumen capacidad residencial remanente y usan pesos con beta 0.04.
La asignacion es por cohortes con capacidad disponible, zona y distancia; la cuota
declarada puede quedar sin cubrir si el soporte o el presupuesto son insuficientes.
No describirla como cuota siempre satisfecha sin medir el flujo final.

`furness_ipfp_balance` usa friccion exponencial y ajustes alternos. Para vectores
autorizados O y D, el contrato matematico duro seria:

- `sum_j T[i,j] = O[i]` para cada origen.
- `sum_i T[i,j] = D[j]` para cada destino.
- `T[i,j] = 0` fuera del soporte permitido y `T[i,j] >= 0`.
- `sum(O) = sum(D)` con valores finitos y no negativos.

La implementacion revisada **normaliza D al total O**, usa float32 para el balance,
agrega respaldo a filas/columnas sin conectividad, evalua error de columnas justo
despues de ajustarlas, y finalmente normaliza filas para devolver probabilidades.
La simulacion muestrea destinos y puede consolidar puntos/cohortes posteriormente.
Por ello, ni `furness_tol: 0.02` ni el nombre "doblemente acotado" prueban
marginales exactas, soporte duro o convergencia final. No aumentar iteraciones como
sustituto de un diagnostico estructural.

## Verificacion segun contrato solicitado

Para conservacion global, comparar total exportado con el presupuesto retenido.
Para origenes y destinos, reconstruir flujos desde cohortes finales y compararlos
con vectores autorizados **anteriores** a sincronizar campos de display.
Para soporte, inspeccionar pares prohibidos, auto-viajes, max_distance y zonas.

Si se requieren marginales duras, comprobar factibilidad antes de IPFP (incluidos
componentes y restricciones de capacidad del soporte), medir residuos de filas y
columnas sobre la misma matriz final y explicitar tolerancias absolutas/relativas.
La integerizacion y el empaquetado deben conservar cada celda OD; no mover personas
a otro par ni remuestrear destinos para ocultar incompatibilidad.
Estas son condiciones del contrato duro solicitado, no funciones que este checkout
pueda darse por supuesto que implementa. Si faltan, reportar la limitacion antes
de afirmar cumplimiento y mantener cualquier remediacion dentro del alcance pedido.

## Zonas y posiciones finales

- `affluence_zones` modifica atraccion/alcance; inspeccionar
  `get_zone_multipliers_for_point` y el modo `TARGET_CAPACITY`.
- `exclusion_zones` suprime demanda, no autoriza cortar infraestructura OSM.
  Comprobar puntos finales, incluidos POIs y posiciones consolidadas.
- `isolated_zones` particiona la asignacion mediante `assign_zones`.
  Comprobar todos los pares exportados y aclarar solapamientos o zonas sin empleo;
  una declaracion YAML no prueba ausencia final de viajes entre islas.

## Empaquetado de cohortes

El pipeline revisado lee `min_pop_size` (default 25), `target_pop_size` (180)
y `max_pop_size` (200). Son parametros del proyecto. La formula historica
`max(35, round(PEA / 18000))` no es el default observado ni garantiza rendimiento.

`merge_identical_commutes` combina cohortes del mismo par. Si el total del par es
menor que el minimo, conserva ese total; en modo rigido puede emitir un resto
pequeno (p. ej. 201 personas con techo 200 produce 200 y 1).
Conservar personas y pares autorizados prevalece sobre una preferencia de tamano.
Verificar que `consolidate_small_pops` o clustering no cambie el contrato OD exigido.
Medir histograma de tamanos, suma por par y tiempos de simulacion; no prometer FPS.
