---
name: subway-builder
description: >-
  Configurar, compilar y diagnosticar mapas del repositorio Subway Builder Mexico:
  fuentes INEGI/CONAPO, demanda gravitatoria, POIs, OSRM y cartografia depot.maps.
  Usar para este pipeline y sus herramientas Wizard/POI Studio, no para desarrollo
  general de juegos ni para optimizar redes ferroviarias en otros proyectos.
---

# Subway Builder Mexico

Trabajar sobre el checkout y la ciudad solicitados. Respetar el alcance del usuario:
una revision es de solo lectura; un ajuste de YAML no autoriza cambios al simulador,
parches a depot.maps, instalacion de dependencias ni publicacion de mapas.

## Contratos y evidencia

- No imputar coordenadas del centro del BBOX a registros censales sin geometria.
- Conservar el presupuesto de PEA retenido despues de los filtros espaciales y
  documentar la masa excluida. Comprobar el total exportado, no inferirlo del YAML.
- Separar conservacion global, marginales por origen/destino y soporte OD.
  Si el usuario exige marginales duras, no renormalizar destinos, reasignar pares
  ni remuestrearlos para aparentar cumplimiento; informar incompatibilidad.
  Leer [gravity_model_and_math.md](references/gravity_model_and_math.md).
- Mantener `include_driving_path: false` como valor predeterminado del proyecto.
  Su costo depende de la geometria y del runtime; medir bytes y memoria cuando
  se habilite. No deducir un limite de carga solo del numero de cohortes.
- Aislar microdatos y salidas por ciudad; respetar `data_dir` y las opciones CLI.
  La raiz `data/` puede contener fuentes nacionales compartidas. Registrar rutas
  efectivamente utilizadas y evitar fallbacks a microdatos de otra ciudad.
- Preservar IDs existentes. `AIR_`, `UNI_` y nombres legibles son convenciones
  del repositorio; comprobar comportamiento del juego/mod para la version usada.
- Mantener UTF-8 sin BOM y LF conforme al proyecto. Diagnosticar bytes y lector
  antes de atribuir mojibake a un caracter particular.

El codigo actual y los esquemas locales son evidencia del pipeline. Una regla
del repositorio no prueba un contrato del juego. Para afirmar compatibilidad o
paridad, registrar version del juego/mod, fuente oficial o codigo inspeccionado,
revision y resultado observado. Si falta evidencia, indicar lo no verificado.

## Elegir el flujo

Para defaults y controles actuales, consultar primero [Demanda y Wizard](../../../docs/demand-wizard.md). Las referencias históricas pueden describir métodos antes de su activación por defecto.

Leer solo las referencias necesarias para la tarea:

| Tarea | Referencia y puntos de entrada |
| --- | --- |
| Configurar ciudad o POIs | [city_configuration_guide.md](references/city_configuration_guide.md); `cities/*.yaml`, `tools/poi_studio.py` |
| Ingesta y calibracion | [data_sources_and_census.md](references/data_sources_and_census.md); `sb_mexico/inegi.py` |
| Demanda y cohortes | [gravity_model_and_math.md](references/gravity_model_and_math.md); `sb_mexico/gravity.py`, `sb_mexico/pipeline.py` |
| Ruteo y cache OSRM | [routing_osrm_wsl.md](references/routing_osrm_wsl.md); `sb_mexico/osrm.py` |
| Cartografia y depot | [cartography_and_depot.md](references/cartography_and_depot.md); `tools/patch_depot_wsl.py` |
| Wizard y entrega | [wizard_and_tooling.md](references/wizard_and_tooling.md); `tools/wizard.py`, `build.py` |
| Importacion o fallo del juego | [game_engine_specs.md](references/game_engine_specs.md), [troubleshooting_and_standards.md](references/troubleshooting_and_standards.md) |

Antes de editar, leer los puntos de entrada pertinentes y las diferencias locales.
Aplicar la correccion minima y verificar el comportamiento afectado. Los ejemplos
de Cancun ilustran comandos; sustituir la ciudad por la seleccionada por el usuario.

## Ejecucion local

Ejecutar desde la raiz del repositorio con un Python que tenga las dependencias
del proyecto. Comprobar el entorno existente antes de instalar o cambiarlo.
En PowerShell, un ejecutable con ruta explicita se invoca con `& 'ruta/python.exe'`.

```powershell
wsl.exe --exec python3 -c "import yaml; print(yaml.__version__)"
wsl.exe --exec python3 .agents/skills/subway-builder/scripts/validate_city.py cities/cancun_riviera_maya.yaml
```

WSL normalmente hereda el directorio actual convertido; comprobarlo con
`wsl.exe --exec pwd`. Usar rutas Linux (`/mnt/c/...`) para rutas absolutas dentro
de WSL. Si aparece `Wsl/Service/CreateInstance/E_ACCESSDENIED` bajo sandbox,
reintentar la misma comprobacion de lectura mediante el mecanismo de escalacion
disponible antes de concluir que WSL no funciona. No reiniciar WSL ni servicios
ajenos para resolver un fallo de permisos. Una escalacion rechazada sigue siendo
un bloqueo que debe comunicarse.

Con el interprete elegido, los comandos habituales son:

```bash
python3 .agents/skills/subway-builder/scripts/validate_city.py cities/<ciudad>.yaml
python3 build.py cities/<ciudad>.yaml
python3 build.py cities/<ciudad>.yaml --skip-map
python3 tools/wizard.py
python3 tools/poi_studio.py --city cities/<ciudad>.yaml
python3 -m unittest discover -s tests -p 'test_gravity.py'
```

El validador de ciudad hace comprobaciones estaticas parciales; no certifica
fuentes, convergencia OD, cartografia, paquetes ni compatibilidad del juego.
`--skip-map` requiere cartografia existente y compatible, incluyendo PMTiles y
`roads.geojson`; su sola existencia no demuestra vigencia.

## Cierre de una entrega

1. Registrar ciudad, configuracion efectiva, fuentes y anos seleccionados,
   revision del codigo y opciones usadas. Para CONAPO sin ano explicito, pedir
   confirmacion del ano; no inferirlo del nombre del archivo o de la fecha actual.
2. Verificar total de pasajeros, referencias de puntos/cohortes y restricciones
   espaciales. Si se exigen marginales OD, medirlas sobre los datos finales.
3. Verificar que el archivo entregado pertenece a esta ejecucion y que sus
   contenidos coinciden con los artefactos revisados. No entregar un ZIP anterior
   si el build solo produjo demanda. Leer el procedimiento de empaquetado en
   [game_engine_specs.md](references/game_engine_specs.md).
4. Para afirmar que es jugable, observar importacion y carga de mapa/demanda en
   la version objetivo, guardar y recargar. Si no hay acceso al juego, entregar
   como validado estaticamente con prueba de juego pendiente.

Reportar cambios, comprobaciones ejecutadas, resultados y limites concretos.
Un comando exitoso o un ZIP existente no constituye un gate de correccion global.
