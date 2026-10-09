# Guía de fuentes de datos — Subway Builder México

Revisada el 6 de octubre de 2026. Esta guía describe los archivos que seleccionan el Wizard, la vista previa y la compilación. Para los métodos y defaults actuales, consulta [Demanda y Wizard](docs/demand-wizard.md).

## Preparación y ubicación

En el paso 2 del Wizard, pulsa **Preparar descargas automáticas**. Guarda el BBOX,
consulta sus entidades y municipios en el catálogo geográfico INEGI y muestra las
fuentes que se pueden descargar. Revisa las selecciones y pulsa **Descargar fuentes
seleccionadas**. Para un mapa multiestado, elige la entidad de referencia ENOE;
sus tasas se aplican al proyecto completo. El periodo inicial es **2026 T2**, no
una búsqueda del trimestre más reciente: puedes elegir otro periodo publicado.

El descargador incluye EIC 2025, CPV/Marco 2020, SAIC con año de referencia 2023, la ruta vigente
de DENUE y el extracto nacional OSM. Extrae los archivos de datos y los componentes
de manzanas/AGEB automáticamente. Los diccionarios de los ZIP no se seleccionan
como datos. OSM se guarda en `data/`; las demás entradas van al `data_dir` del proyecto.

Las descargas originales se conservan en `data/.source-cache/`, con URL, consulta,
fecha, tamaño y SHA-256. Los archivos instalados quedan registrados en
`.source-downloads.json`. Una segunda ejecución verifica y reutiliza la caché;
**Volver a descargar desde el proveedor** permite actualizarla. El proceso conserva
archivos manuales y no mezcla automáticamente sus ediciones: las fuentes existentes
aparecen deshabilitadas; puedes conservarlas o excluirlas y preparar de nuevo.
Solo reemplaza archivos gestionados cuyo hash sigue intacto, y retira los archivos
gestionados anteriores de esa fuente cuando cambia la cobertura o el periodo.

Cada fuente se valida antes de publicarse: firmas de ZIP/XLS/PBF, columnas CSV,
componentes de shapefile y, para SAIC, año, claves y número de filas de la consulta.
Se muestran los fallos por fuente y las demás pueden terminar. El servicio no crea
celdas censales reservadas ni garantiza cobertura estadística de cada desglose.
Leer las tasas del XLS ENOE requiere `xlrd`, incluido en `requirements.txt`.
Si falta o el tabulado no se puede interpretar, el resultado indica que el XLS se
descargó pero sus tasas no pudieron leerse; no confirma su uso en el modelo.
Si falla un proveedor, usa las instrucciones manuales siguientes. Después de
descargar, revisa **Detectar**, la selección efectiva y la vista previa antes del build.

Usa la carpeta `data_dir` asignada al proyecto, por ejemplo `data/PROYECTO/`. Los CSV y tabulados ENOE se colocan directamente en esa carpeta. La compilación también selecciona fuentes directamente en `data/`, sin recorrer carpetas de otras ciudades: reserva esa raíz para fuentes nacionales o compartidas.

Descomprime los ZIP antes de usar sus datos. El endpoint de carga guarda el paquete, pero no lo extrae. Un ZIP de DENUE no sustituye el CSV. El Marco Geoestadístico en modo oficial sí puede mantener sus subcarpetas y debe conservar todos los componentes del shapefile.

«Encontrado» indica presencia de candidatos, no validación de columnas, fechas o cobertura. Revisa la procedencia y los diagnósticos de vista previa antes de compilar. DENUE y CPV son las entradas básicas; las demás dependen del método, proyección y tipo de compilación.

## 1. DENUE · establecimientos económicos

**Uso:** Necesaria. Ubica establecimientos y aporta su actividad y estrato de empleo.

[Portal de descarga](https://www.inegi.org.mx/app/descarga/)

1. Selecciona DENUE, la entidad federativa del proyecto y la edición que quieres utilizar. Descarga el paquete CSV.
2. Descomprime el ZIP y conserva `conjunto_de_datos/denue_inegi_NN_.csv`, donde NN es la clave estatal de dos dígitos. Colócalo directamente en la carpeta de datos del proyecto; no copies el diccionario como entrada.
3. Pulsa Detectar y comprueba los archivos encontrados. Usa una sola edición por entidad; el nombre no certifica cobertura ni fecha.

CSV: *denue*.csv. Se leen todos los archivos seleccionados. Un ZIP pendiente de extracción no sirve como entrada.

Ruta vigente por entidad: `https://www.inegi.org.mx/contenidos/masiva/denue/denue_NN_csv.zip`.
Por ejemplo, [Quintana Roo, clave 23](https://www.inegi.org.mx/contenidos/masiva/denue/denue_23_csv.zip).
No inventes rutas con fechas: INEGI puede responder HTTP 200 con una página de error.
La ruta vigente puede cambiar de edición; conserva fecha y hash de la descarga.

## 2. CPV 2020 · población por manzana

**Uso:** Necesaria. Aporta población, claves territoriales y ocupación censal por manzana urbana.

[Portal de descarga](https://www.inegi.org.mx/programas/ccpv/2020/#Microdatos)

1. En Microdatos elige Principales resultados por AGEB y manzana urbana y descarga el CSV de cada entidad del proyecto.
2. Descomprime el ZIP y copia `conjunto_de_datos/conjunto_de_datos_ageb_urbana_NN_cpv2020.csv` directamente a la carpeta del proyecto. No copies el diccionario ni uses las tablas de Personas o Viviendas del cuestionario ampliado.
3. Conserva todas las columnas originales, incluidas las claves ENTIDAD, MUN, LOC, AGEB y MZA y los indicadores POBTOT, P_15YMAS y POCUPADA.

El nombre oficial contiene `cpv2020`; no hace falta renombrarlo a RESAGEBURB.
Ruta estatal: `https://www.inegi.org.mx/contenidos/programas/ccpv/2020/datosabiertos/ageb_manzana/ageb_mza_urbana_NN_cpv2020_csv.zip`.
Ejemplo: [CSV de Quintana Roo](https://www.inegi.org.mx/contenidos/programas/ccpv/2020/datosabiertos/ageb_manzana/ageb_mza_urbana_23_cpv2020_csv.zip).
El lector también admite archivos heredados RESAGEBURB y *censo*.xlsx. El CSV contiene estadísticas, no polígonos: añade el Marco Geoestadístico para la geometría oficial.

## 3. Marco Geoestadístico · manzanas y AGEB

**Uso:** Geometría oficial. Aporta los polígonos para colocar la población censal. Los proyectos nuevos usan Manzanas oficiales INEGI.

[Portal de descarga](https://www.inegi.org.mx/app/biblioteca/ficha.html?upc=889463807469)

En la ficha abre la descarga **por entidad**, elige el estado y su ZIP. Ejemplo:
[Marco 2020 de Quintana Roo](https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/889463807469/23_quintanaroo.zip).
El descargador resuelve los enlaces de las 32 entidades desde el catálogo de esa ficha.

1. Descarga el Marco Geoestadístico del Censo de Población y Vivienda 2020 de cada entidad, compatible con las claves del CPV 2020.
2. Descomprime el paquete completo dentro de la carpeta del proyecto; puedes conservar marco_2020_ENTIDAD/conjunto_de_datos/ y su estructura.
3. Conserva juntos .shp, .dbf, .shx, .prj y .cpg cuando exista. En los nombres de las capas, el prefijo numérico identifica la entidad: NNm.shp corresponde a manzanas y NNa.shp a AGEB urbanas; sustituye NN por la clave de dos dígitos de la entidad.

SHP, GeoJSON o GPKG con claves territoriales y polígonos de manzanas/AGEB. El modo oficial busca capas en subcarpetas. Encontrado no significa cobertura completa: comprueba el reporte de ubicación en la vista previa.

## 4. Censos Económicos 2024 · SAIC

El descargador obtiene cuatro exportaciones por entidad y lote de hasta 20
municipios: sectores con tamaños, sectores totales, detalle SCIAN con tamaños y
detalle SCIAN total de los sectores admitidos. Lo hace para ambos motores;
seleccionar el legado no limita los archivos disponibles para una futura
compilación v2. La política
`auto` del candidato aprovecha ese detalle compatible y reporta el respaldo
cuando falta; véase [detalle CE del candidato](docs/demand-ce-detail.md).
Las instrucciones manuales siguientes describen las dos exportaciones
sectoriales; esos archivos por sí solos no proporcionan detalle de clase.

Un CSV manual de totales no bloquea la adquisición de los desgloses adicionales.
El descargador conserva ese archivo y comprueba los controles combinados antes
de publicar: celdas contradictorias o años incompatibles impiden marcar la
descarga como completa. No sobrescribe archivos manuales ni reconstruye datos
reservados. Para CPV, DENUE y otras fuentes que pueden duplicar microdatos,
se conserva la protección de selección y de archivos manuales.

**Uso:** Complementaria. Puedes continuar sin SAIC con estimaciones DENUE.

**Descarga automática: 4 CSV por lote de hasta 20 municipios y por entidad.**
Incluye las tablas sectoriales y el detalle de actividad que aprovecha v2.
La alternativa manual siguiente produce **2 CSV sectoriales por entidad**:
puede activar la transferencia sectorial del legado con celdas municipales
publicadas de UE y H001A, pero no sustituye el detalle de clase del candidato.
Los totales solos no activan ninguna transferencia.

[Portal de descarga](https://www.inegi.org.mx/app/saic/)

Selección común para ambas consultas:

1. **Año censal: 2023**, correspondiente a los Censos Económicos 2024.
2. **Área geográfica:** entidad y municipios del proyecto. Puedes incluir todos los municipios de una entidad en un mismo CSV. Si el proyecto abarca varias entidades, repite las dos consultas para cada una. El total estatal no sustituye el detalle municipal.
3. **Actividad económica:** marca **Todos los sectores** y **Con totales**.
4. **Variable censal:** selecciona juntas **UE · Unidades económicas** y **H001A · Personal ocupado total**. Cada CSV incluye ambas variables; no son dos descargas separadas por variable.

**Archivo 1: detalle por sector y tamaño**

1. En **Estrato de personal ocupado** selecciona **0 a 10, 11 a 50, 51 a 250 y 251 y más**. Incluye **Agrupados por confidencialidad** si aparece y conserva los valores reservados.
2. Pulsa **Consultar**, abre **Exportar** y selecciona **Exportar como: CSV**.
3. Renombra la descarga como `SAIC_ENTIDAD_2023_sectores_tamanos.csv`.
4. Comprueba las columnas: Año censal, Entidad, Municipio, Estrato, Actividad económica, UE y H001A.

**Archivo 2: totales por sector (adicional)**

1. Conserva año, geografía, actividades y variables. Cambia **Estrato de personal ocupado** a **Suma de estratos**.
2. Pulsa **Consultar**, abre **Exportar** y selecciona **Exportar como: CSV** para el segundo archivo.
3. Renómbralo como `SAIC_ENTIDAD_2023_totales.csv`. Contiene las mismas variables, sin el desglose por tamaño.

Coloca ambos CSV en la carpeta de datos mostrada por el Wizard y pulsa **Detectar**. Sustituye `ENTIDAD` por el nombre de la entidad, sin espacios, conservando `SAIC_` y `.csv`. Los nombres originales `SAIC_Exporta_*.csv` también son compatibles; renombrar solo ayuda a distinguir las descargas.

El lector combina los CSV seleccionados. No sumes manualmente totales y desgloses, no sustituyas valores reservados por cero ni mezcles años. Actualmente la transferencia utiliza los sectores 31-33, 43, 46, 53 y 72. Encontrar un archivo no garantiza que sus celdas sean utilizables.

## EIC 2025 · referencia demográfica municipal

Activa EIC en Macroeconomía y prepara las descargas automáticas. También se
recomienda automáticamente cuando v2 tiene referencia 2025, aunque todavía no
se hayan configurado sus rutas. Se recomienda
EIC y se deja CONAPO sin seleccionar: no se usa para proyectar estas magnitudes
cuando EIC está activa. El BBOX determina todos los estados que deben descargarse.

- Indicadores nacionales: [ZIP oficial de indicadores EIC 2025](https://www.inegi.org.mx/contenidos/programas/eic/2025/datosabiertos/conjunto_de_datos_eic2025_105_csv.zip), que contiene `conjunto_datos_eic2025_105.csv`.
- Personas por estado: `https://www.inegi.org.mx/contenidos/programas/eic/2025/microdatos/eic2025_micro_NN_csv.zip`, del que se extrae `personasNN.csv`.

Se valida la estructura y se reproducen los controles de población/ocupados de
los municipios del BBOX ponderando FACTOR antes de instalar los archivos. El
Wizard guarda sus rutas explícitas al terminar en el proyecto que inició la
descarga. Una referencia pendiente de descarga se puede guardar, pero bloquea
la compilación. Los archivos manuales no se sobrescriben.

El panel distingue fuente requerida, archivos faltantes y estructura inválida.
La revisión de estructura no certifica cobertura municipal: la compilación
repite la conciliación. Las rutas se cambian en Macroeconomía; las exclusiones
por nombre de otras fuentes no alteran las rutas EIC explícitas. CPV y Marco
2020 siguen siendo necesarios para distribución y ubicación.

## 5. CONAPO · población municipal 1990–2040

**Uso:** Según proyección. Aporta población municipal para el año objetivo; no es intercambiable con una serie estatal.

[Ficha de la base municipal](https://www.datos.gob.mx/dataset/proyecciones-de-poblacion/resource/3c3092be-583e-4490-8c23-67ef9a64b198)
· [Descarga directa de pobproy_quinq1.csv](https://www.datos.gob.mx/dataset/f2b9b220-3ef7-4e3a-bde6-87e1dac78c6a/resource/3c3092be-583e-4490-8c23-67ef9a64b198/download/pobproy_quinq1.csv)

1. Abre la ficha de la base municipal y pulsa Descargar, o usa la descarga CSV directa anterior. No descargues únicamente indicadores demográficos o totales estatales.
2. Descomprime y coloca el CSV municipal en la carpeta del proyecto. La base quinquenal compatible es pobproy_quinq1.csv; también se reconocen *conapo*.csv y data-*.csv.
3. Comprueba que contiene CLAVE municipal, ANO (o equivalente) y población total, por ejemplo POB_TOTAL o POB_MIT_MUN. Selecciona el año objetivo en Macroeconomía; si no hay columna de año, confirma el año de la fuente.

CSV municipal. Se utiliza el primer archivo seleccionado: revisa cuál aparece marcado y excluye candidatos que no correspondan. No se combinan automáticamente varios CSV de CONAPO.

## 6. ENOE · participación e informalidad

**Uso:** Complementaria. Aporta tasas de participación laboral y de informalidad laboral 1 (TIL1) para la entidad y el periodo seleccionados.

[Portal de descarga](https://www.inegi.org.mx/programas/enoe/15ymas/#Tabulados)

1. En Tabulados elige Indicadores estratégicos para población de 15 años y más, el trimestre y la entidad que quieres utilizar.
2. Descarga el paquete XLS de Indicadores estratégicos del periodo. Ejemplo: [2026 T2, paquete oficial](https://www.inegi.org.mx/contenidos/programas/enoe/15ymas/tabulados/enoe_indicadores_estrategicos_2026_trim2_xls.zip). Descomprime y copia únicamente `Entidades/2026_trim_2_Entidad_NOMBRE DEL ESTADO.xls` al proyecto. No uses los libros de Ciudades o Agregado_39_ciudades. Un PDF o los microdatos no sustituyen este tabulado.
3. Conserva Tasa de participación y Tasa de informalidad laboral 1 (TIL1) en la primera hoja. Usa un solo periodo y Total, ambos sexos; el lector toma el primer valor utilizable de cada indicador.

CSV, XLS y XLSX: nombres *trim* o *enoe*. Se utiliza el primer archivo seleccionado, no necesariamente el trimestre más reciente. Comprueba las tasas en Macroeconomía y su procedencia antes de guardar.

## 7. OpenStreetMap · cartografía y red vial

**Uso:** Compilación cartográfica. Aporta calles y cartografía para compilar el mapa y preparar rutas.

[Portal de descarga](https://download.geofabrik.de/north-america/mexico.html)
· [PBF nacional directo](https://download.geofabrik.de/north-america/mexico-latest.osm.pbf)

1. En Mexico descarga mexico-latest.osm.pbf. Los paquetes SHP/GPKG de Geofabrik no sustituyen el PBF en la compilación cartográfica.
2. Guarda el PBF en la carpeta del proyecto o, para compartir el extracto nacional, directamente en data/. Conserva la fecha o edición descargada.
3. Para regenerar solo demanda utiliza cartografía previa compatible en la salida del proyecto. roads.geojson por sí solo no permite generar un mapa nuevo.

PBF: *.osm.pbf. Los paquetes finales requieren cartografía completa y compatible; encontrar un archivo no certifica la integridad del mapa.

## Selección y proyectos multiestado

- CPV y DENUE: añade los CSV de todas las entidades. Se leen los candidatos seleccionados; conserva claves completas y una edición coherente. Los conflictos de identidad deben revisarse en los diagnósticos.
- Marco Geoestadístico: añade las capas de todas las entidades bajo la carpeta del proyecto. El modo oficial descubre subcarpetas y usa puntos interiores de polígonos; las fuentes de respaldo y población sin ubicación se documentan en los reportes.
- CE: se leen varios CSV; los controles contradictorios o distintos años de transferencia requieren corregir la selección. Totales y estratos no son filas que deban sumarse entre sí.
- CONAPO y ENOE: se utiliza el primer archivo del selector compartido. El Wizard lo identifica y muestra los candidatos restantes. Excluye archivos que no correspondan; no se elige automáticamente el trimestre más reciente.
- ENOE: la tasa de referencia se aplica a todo el proyecto; cargar tabulados de varios estados no calcula una tasa metropolitana ponderada.
- Archivos excluidos: se conservan en disco y pueden restaurarse. Para modificar las capas oficiales del Marco, gestiona sus archivos desde la carpeta del proyecto.

La guía no certifica cobertura estadística ni jugabilidad. La vista previa y la compilación deben confirmar las fuentes efectivas; importar, cargar y recargar en el juego es una verificación posterior distinta.
