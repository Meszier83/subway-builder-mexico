# Demanda y Wizard: comportamiento actual

## Compilación habitual

Los proyectos nuevos y las configuraciones sin selección explícita usan:

- `city.residential_placement: official_blocks`: geometría oficial INEGI con
  cobertura y respaldo documentados.
- `macroeconomics.residential_employment: census_employed`: población ocupada
  censal y reconstrucción controlada de valores reservados.
- `macroeconomics.workplace_employment: auto`: inspección del contenido de las
  fuentes CE seleccionadas. Aplica transferencia histórica cuando hay detalle
  municipal, sectorial y por tamaño utilizable; conserva estimaciones DENUE
  acotadas cuando falta y muestra el motivo.

La política se aplica igual a todos los proyectos. No se deduce comparabilidad
CE/DENUE del nombre de la ciudad, ni se afirma ajuste a empleo contemporáneo
solo por disponer de una referencia histórica. Los controles anteriores y CE
manual siguen disponibles en «Compatibilidad»; cargar un proyecto con métodos
explícitos los conserva y muestra un aviso. Los reportes de cobertura permanecen
visibles en el flujo principal.

El Wizard guarda la configuración antes de solicitar la vista previa o compilar.
La selección CONAPO conserva año y procedencia. Las zonas aisladas continúan
separando asignaciones; ninguna corrección de empleo las conecta entre sí.

## Balance y diagnóstico

Los presupuestos de origen retenidos se conservan. La sincronización de campos
visuales no sustituye los presupuestos originales en la tabla territorial.

Si un grupo de destinos exige más masa de la que sus orígenes alcanzables pueden
proporcionar, el balance puede corregir sus objetivos de atracción conservando
pesos relativos y redistribuyendo la diferencia dentro del bloque presupuestario.
Se registran los objetivos solicitados y efectivos, el corte probado y los errores
contra ambos. No se agregan enlaces de viaje. Si las probabilidades anteriores
ya cumplen los objetivos corregidos, se conservan para evitar cambios innecesarios
en el muestreo. Una convergencia lenta sin déficit probado no reescribe objetivos;
los casos sin resolver siguen mostrando `iteration_limit`.

Estos errores corresponden a flujos esperados antes del muestreo y consolidación.
No certifican marginales exactos de destinos en las cohortes exportadas ni son
conteos observados de empleo.

`demand_pipeline_report.json` incluye presupuestos territoriales, balance y
procedencia vial por par, con motivos de respaldo y viajeros afectados. La
procedencia se obtiene durante la consulta, no por coincidencia numérica con la
fórmula estimada. Los campos de diagnóstico no se agregan al esquema del juego.
El respaldo vial no modela explícitamente accesos, esperas y tiempos de ferry.

## Entrega y compatibilidad

El manifiesto `wizard-build.json` identifica configuración, ejecución, estado y
paquete. Una ejecución fallida o de solo demanda sin cartografía compatible no
habilita la descarga de un ZIP anterior como si fuera nuevo. La compatibilidad
con el juego requiere comprobar importación, ejecución, guardado y recarga; las
pruebas automatizadas no sustituyen esa comprobación.

## Archivos locales y Git

`data/`, `dist/`, `reports/` y `plans/` permanecen locales. Los proyectos de
`cities/*.yaml` también, salvo `_template.yaml`. Un commit del código no respalda
los proyectos ni sus fuentes: conservar por separado configuraciones, fuentes,
ZIP probado y reportes correspondientes.

Los recursos de `tools/static/wizard/`, incluido `manifest.json` y las licencias,
son necesarios para servir el Wizard y se versionan. También se versionan los
archivos de construcción y bloqueo de dependencias de `tools/wizard-assets/`;
`node_modules/` no se versiona. Para reconstruir los recursos:

```sh
npm ci --prefix tools/wizard-assets
node tools/build_wizard_assets.cjs
```

## Verificación reproducible

Desde la raíz, con las dependencias de `requirements.txt` instaladas:

```sh
python -m unittest discover -s tests -q
python tools/check_wizard_ui.py
node tests/residential_employment_ui.cjs
node tests/historical_benchmark_ui.cjs
node tests/historical_transfer_ui.cjs
```

Las herramientas `compare_*`, `verify_wizard_*` y `audit_build_impact.py` producen
comparaciones locales con fuentes y paquetes disponibles en la máquina. Sus
resultados se conservan fuera de Git, vinculados al proyecto y ejecución usados.
