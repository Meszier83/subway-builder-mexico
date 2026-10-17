# Pipeline de nombres territoriales

`official_v1` separa los nombres del mapa de los establecimientos y de la demanda.
El Wizard activa este contrato al escanear o adquirir fuentes de nombres. Abrir
un proyecto antiguo no sustituye su lista ni cambia automáticamente su mapa.

## Flujo en el Wizard

1. En POI Studio → Colonias, pulsar **Preparar etiquetas**. Guarda el proyecto,
   reutiliza las fuentes locales válidas y adquiere las ausentes o antiguas.
2. Incorpora los registros oficiales sin eliminar nombres existentes; conserva
   IDs, nombres, posiciones y descartes. Las correspondencias ambiguas se conservan
   separadas y aparecen en la bandeja de excepciones.
3. Calcula tamaños y aparición por zoom con `label_v1`, guarda los campos de
   toponimia de forma atómica y crea un respaldo recuperable. Los trabajos atrasados
   no pueden aplicarse si cambia el YAML o si pertenecen a otra ciudad.
4. La **Vista de etiquetas** muestra la presentación calculada. **Catálogo completo**
   muestra puntos, incluso fuera del núcleo, para inspeccionar, editar y arrastrar.
   Las etiquetas exteriores se ocultan sin borrar lugares. Sin núcleo se usa el BBOX.
5. Revisar únicamente las excepciones que se quieran resolver. «Conservar separados»
   reconoce una coincidencia ambigua sin eliminar registros. Las fuentes contradictorias
   y los nombres sin geometría se identifican para revisar su procedencia.
6. Compilar cartografía. El build utiliza las fuentes y nombres guardados, sin
   descargar proveedores ni inferir nombres desde DENUE. Su adaptador reemplaza
   `MapGen.add_labels`; las heurísticas antiguas no intervienen en `label_v1`.

La flecha del botón conserva descarga, importación, escaneo selectivo y diagnóstico
avanzados. Estas operaciones no aplican automáticamente una nueva presentación.
La poda por radio deja de ser la acción principal para despejar el mapa.

Los proyectos existentes conservan sus nombres hasta que se revisen. El modo
«Usar lista guardada» es estricto en `official_v1`: sustituye todas las capas
territoriales, incluso cuando alguna queda vacía. «Complementar OSM» conserva
etiquetas nativas adicionales. La opción «Complementar candidatos con OSM» del
escaneo controla las propuestas y es independiente de la política de compilación.
`label_v1` requiere «Usar lista guardada»; no incorpora etiquetas OSM adicionales.

## Presentación automática y excepciones

`toponymy.layout` declara `engine: label_v1`, `profile: balanced` y
`periphery: core_only`. El catálogo y sus tipos territoriales se mantienen separados
de las decisiones de presentación. Una localidad urbana de 100 000 habitantes o más
es grande desde zoom 6; otra urbana es mediana desde 10; una rural de 2 500 habitantes
o más es mediana desde 11. Regiones, sectores y supermanzanas aparecen desde 12;
el resto de localidades y colonias, desde 14; despobladas, privadas y cotos, desde 15.
La población ausente sigue siendo desconocida, sin convertirse en cero.

El planificador prioriza jerarquía y población; para asentamientos usa categoría
y superficie como criterios secundarios. Desempata por ID. Calcula ocupación en
Web Mercator usando tamaño y longitud del nombre; retrasa etiquetas densas hasta
un zoom posterior. Todo el detalle válido y elegible llega al zoom máximo 15.
La vista resuelve además colisiones de pantalla. Es una aproximación del estilo del
juego, sin certificación de sus fuentes, tamaños o cámara.

Los controles individuales y por lote guardan `label_overrides.scale` (`large`,
`medium` o `small`) sin cambiar `type` ni `feature_kind`. El popup permite volver
a la presentación automática, ocultar una etiqueta y ajustar su prioridad entre
-100 y 100. Ninguna excepción de visibilidad permite salir del núcleo. A−/A+
solo amplía o reduce la vista del editor.

«Deshacer» restaura los campos de la última operación confirmada. «Restaurar respaldo
anterior» recupera los campos guardados en `<data_dir>/.toponymy/backups`; no revierte
demanda ni otras modificaciones del proyecto. Una falla de guardado conserva el YAML
anterior. Cancelar durante la consulta evita aplicar sus resultados; el guardado
final es una operación atómica.

## Interfaces y compilación de etiquetas

- `GET /api/toponymy/prepare?file=...` inicia o consulta un trabajo usando `job=...`.
  Devuelve progreso por fuente/etapa, catálogo preparado, resumen y excepciones.
- `POST /api/toponymy/apply-prepared` recibe `file` y `job_id`; verifica la huella
  del YAML y publica exclusivamente campos de toponimia con respaldo.
- `POST /api/toponymy/layout` recibe `config` y calcula la presentación de un
  borrador; `GET` con `file` la calcula desde el YAML guardado.
- `POST /api/toponymy/restore` recibe `file` y restaura su respaldo anterior.

Las capas siguen siendo `city_labels`, `suburb_labels` y `neighborhood_labels`.
Cada etiqueta tiene una sola capa, nombre, ID y prioridad. La extensión `tippecanoe`
se escribe en el Feature, con `minzoom`, `maxzoom` y `layer`. La ruta nativa y WSL
usan el mismo adaptador y rechazan compiladores incompatibles. La huella de
reutilización incluye catálogo, núcleo, política, excepciones y código del motor.
La compilación guarda `toponymy-layout.json` junto a la cartografía.

## Fuentes y almacenamiento

- Localidades: API tabular INEGI `wscatgeo/v2/localidades/EE/MMM`. Se consultan
  todos los municipios que intersectan el BBOX, incluidos los de otras entidades.
  Se conservan las coordenadas oficiales, población original, ámbito y periodo.
- Catálogo de asentamientos: `wscatgeo/v2/asentamientos/EE/MMM`. Sirve para detectar
  nombres sin polígono. Nunca se convierte su nombre en un punto de localidad.
- DCAH: el catálogo de productos INEGI del programa 3374 suministra el enlace SHP.
  Se selecciona la edición publicada más reciente; no se construyen enlaces por año.
  El paquete nacional se valida y se materializa solo el área de interés como
  `toponymy/sources/dcah_automatic.geojson`. Su cobertura no es nacional completa.
  El ZIP nacional contiene ZIP internos; se utiliza la capa integrada una sola vez.
  Los registros originales rechazados siguen visibles en el diagnóstico. Si crece
  el BBOX, el escaneo avisa que debe descargarse de nuevo la materialización.
- Importaciones: ZIP de shapefiles con `.prj`, GeoJSON o tablas de localidades
  JSON/CSV. Para DCAH son obligatorios polígonos, `NOM_ASEN`, `TIPO`, la clave de
  localidad y `CVE_ASEN`. No se admiten centroides estimados como polígonos DCAH.

Las fuentes residen en `<data_dir>/toponymy/sources`; las respuestas originales,
con hashes y metadatos HTTP, en `<data_dir>/.toponymy/http`. Cada materialización
incluye `.source.json` con su hash y procedencia. El caché de catálogos expira
en siete días; el ZIP publicado se reutiliza verificando su hash. Un archivo
modificado después de adquirirse se reporta como error y no se utiliza.

La descarga DCAH 2025 publicada pesa aproximadamente 530 MB. El lector aplica
el BBOX en el CRS de la fuente antes de cargar geometrías y transforma a WGS84.
Las importaciones se validan antes de publicarse; los límites son 768 MB de
archivo y 4 GB de tamaño descomprimido declarado, sin extracción de rutas ZIP.

También pueden declararse archivos adicionales explícitamente, relativos a
`data_dir` o absolutos:

```yaml
toponymy:
  engine: official_v1
  osm_fallback: false
  sources:
    - kind: localities
      path: localidades.json
    - kind: dcah
      path: colonias.geojson
toponymy_mode: replace
```

Los archivos de ciudades vecinas y las fuentes DENUE compartidas no se descubren
recursivamente. No mezclar ediciones contradictorias del mismo territorio: se
reportan y excluyen las claves en conflicto. Retirar de la carpeta de fuentes
las ediciones que ya no se quieran usar, o ajustar las fuentes explícitas.

## Geometría e identidad

Una localidad tiene ID `inegi_locality_<clave de 9 caracteres>` y un asentamiento
`inegi_settlement_<clave de 13 caracteres>`. El ID no depende de su nombre o
coordenadas. Homónimos con claves diferentes y entidades de distinta jerarquía
permanecen separados. El tipo cartográfico de una localidad urbana usa población
original para distinguir escala grande (`>=100000`) y mediana; no certifica rango
político de ciudad. Las localidades rurales usan escala pequeña.

Las etiquetas DCAH se colocan en un punto interior del polígono intersectado
con el BBOX, conservando el límite original para revisión. Se rechazan CRS
ausente, claves incoherentes, geometrías no territoriales y coordenadas no finitas.
Las reparaciones de geometría y los registros rechazados se contabilizan.

`<data_dir>/.toponymy/official-catalog.json` registra fuentes, hashes, conteos,
errores, conflictos, candidatos, asentamientos sin geometría y la huella de la
configuración. «Fuente leída» no certifica cobertura exhaustiva. El catálogo
tabular puede contener localidades despobladas y nombres que parecen negocios:
siguen siendo registros territoriales oficiales y se revisan antes de incorporarlos.

## Entrega y comprobación

La colección cartográfica incluye `pipeline: official_v1` y `authoritative: true`.
Esto evita el enriquecimiento DENUE automático incluso con una lista vacía.
Las geometrías de revisión no se incrustan como propiedades en las teselas de puntos.
La reutilización de mapas del Wizard compara también `deleted_places`,
`toponymy_mode` y `toponymy`; cambiar esos valores requiere recompilar cartografía.

```powershell
python -m unittest discover -s tests -p 'test_toponymy*.py'
python tools/check_wizard_ui.py
python tools/verify_toponymy_official.py --city cities/<ciudad>.yaml --output reports/toponymy/<ciudad> --download
python tools/verify_toponymy_layout.py --city cities/<ciudad>.yaml --sources-city <yaml-con-fuentes-locales> --output reports/toponymy/<ciudad>-automatic
```

La última orden genera evidencia aislada sin sustituir los nombres del YAML original.
Las pruebas cubren geometría, CRS, homónimos, edición, descartes, aislamiento,
adquisición, HTTP, persistencia y el puente WSL. La compilación de PMTiles y la
observación de etiquetas dentro del juego son comprobaciones adicionales.

La verificación automática genera una configuración aislada y comprueba conservación
de nombres/IDs/posiciones/tipos, repetibilidad y coincidencia de vista y exportación.
En WSL, `python3 -m tools.verify_toponymy_layout --compile-existing <curated_places.geojson>
--output <carpeta> --bbox <oeste> <sur> <este> <norte>` compila la etapa de etiquetas
con el adaptador instalado sobre una base sintética y decodifica todas las teselas.
Verifica identidades, nombres, prioridad, capa y zoom. Esa prueba no recompila
geometrías ni demanda y no sustituye una carga del mapa completo en el juego.
