# Diagnostico basado en evidencia

Las reglas de modelacion locales estan en `.agents/rules/subway_builder_standards.md`.
Aplicar las convenciones pertinentes al alcance solicitado y distinguirlas de
contratos comprobados del juego. Las referencias especializadas contienen los
detalles; este indice evita convertir un sintoma en una causa unica.

## Fallos frecuentes

| Sintoma | Comprobaciones y siguiente paso |
| --- | --- |
| Juego vacio, congelado o cerrado | Capturar error, version y bytes del JSON. Revisar IDs/referencias, formato y geometria de rutas. Comparar con un paquete conocido que cargue; no asumir un umbral fijo de V8. Ver [game_engine_specs.md](game_engine_specs.md). |
| Viajes sobre mar o lagunas | Inspeccionar pares finales, zonas aisladas, coordenadas, snapping y causa de fallback. Una linea recta de deseo no prueba que OSRM encontro una carretera. Ver [routing_osrm_wsl.md](routing_osrm_wsl.md). |
| Campus tapado por comercio | Inspeccionar geometria, atributos y orden de capas. Si se reproduce un solapamiento, revisar el parche Campus Wins para la revision de depot usada antes de aplicarlo. Ver [cartography_and_depot.md](cartography_and_depot.md). |
| Proceso Killed o salida 137 | Revisar logs del kernel, RAM y limites antes de concluir OOM. Inspeccionar el calculo de memoria del parche vigente y costo de la AOI. No cambiar .wslconfig ni reiniciar WSL fuera del alcance autorizado. |
| OSRM rechaza conexion o agota timeout | Comprobar URL/puerto, estado y logs del daemon, respuesta HTTP, supervision y reintentos. No atribuir todo fallo a 15 segundos de inactividad o 512 sockets. |
| WSL devuelve E_ACCESSDENIED | Repetir la comprobacion de lectura mediante escalacion disponible. Distinguir permisos del sandbox de instalacion o dependencias faltantes; comunicar una denegacion efectiva. |
| Acentos corruptos | Comparar bytes UTF-8, BOM, texto original y codificacion usada por el lector. Evitar reescribir todo el archivo hasta localizar donde se corrompio. |
| Build exitoso pero descarga incorrecta | Verificar ciudad/configuracion actual, procedencia del ZIP y miembros contra artefactos de la ejecucion. Un build de solo demanda puede dejar un ZIP previo. |

Un parche a depot, una reconstruccion completa o un cambio de servicio requiere
estar dentro de la tarea autorizada. Para revisiones de solo lectura, informar
el hallazgo y la correccion concreta sin ejecutar esas mutaciones.

## Comprobaciones reproducibles

Seleccionar el Python existente segun [SKILL.md](../SKILL.md).
Desde la raiz del repositorio:

```bash
python3 .agents/skills/subway-builder/scripts/validate_city.py cities/<ciudad>.yaml
python3 -B .agents/skills/subway-builder/scripts/test_validate_city.py
python3 -m unittest discover -s tests -p 'test_encoding.py'
python3 -m unittest discover -s tests -p 'test_gravity.py'
python3 -m unittest discover -s tests -p 'test_road_routing.py'
```

Ejecutar solo las comprobaciones pertinentes y revisar errores, no solo exit codes.
El validador auxiliar comprueba tipos, rangos y algunas relaciones de configuracion.
No valida cobertura/ano de fuentes, topologia completa, factibilidad o balance OD,
artefactos cartograficos, contenido del ZIP ni carga del juego.
Registrar esos limites al cerrar la tarea; ver [game_engine_specs.md](game_engine_specs.md)
para entrega y [gravity_model_and_math.md](gravity_model_and_math.md) para demanda.
