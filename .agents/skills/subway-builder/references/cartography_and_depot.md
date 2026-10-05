# Compilacion Cartografica 3D, Zonificacion LOD y Parches de depot.maps

Este documento detalla la arquitectura de generacion de mapas vectoriales tridimensionales en formato **PMTiles v3**, el puente con WSL 2 y la suite de parches de estabilidad y estandares canonicos.

Los parches descritos son convenciones y mecanismos del proyecto, no requisitos
universales del juego. Inspeccionar la revision de depot.maps y el parche concreto
antes de ejecutarlo: modifica codigo fuera de la ciudad y puede afectar otros
builds. Aplicarlo solo dentro del alcance autorizado y conservar una diferencia
revisable. Confirmar el resultado en las capas generadas, no solo en el log.

---

## 1. Arquitectura de Compilacion en WSL 2 (Regla 10)

En sistemas Windows, la compilacion cartografica se ejecuta obligatoriamente dentro de **WSL 2 (Ubuntu)** utilizando `depot.maps`:

- **Motor de Teselas:** `planetiler.jar` y `tippecanoe`.
- **Compilacion en Disco Nativo ext4:** La compilacion ocurre en el directorio nativo de Linux (`~/build_<city>`), transfiriendo los artefactos finales a `dist/<city>/`. Esto elimina la degradacion de velocidad de entrada/salida (I/O) de Windows NTFS.
- **Recorte Previo con `osmium extract`:** Inspeccionar el recorte del PBF al
  BBOX antes de compilar. Medir su tiempo y memoria para el archivo y hardware
  actuales; no prometer tiempos universales.

---

## 2. Zonificacion Concentrica y Nucleo Urbano LOD (Regla 13)

### 2.1. La Paradoja de los Horizontes Infinitos vs. Carga GPU 3D
En metropolis extensas, compilar edificios y calles menores de todo el BBOX puede
aumentar teselas y carga de renderizado. Medir el costo actual; el tamano PMTiles
por si solo no determina un umbral universal de FPS o memoria.

### 2.2. Filtrado Concentrico en Dos Niveles (`apply_urban_lod_filtering`)
1. **BBOX Metropolitano Completo:**
   - Conserva autopistas, carreteras primarias, secundarias, troncales, vias ferreas y cuerpos de agua para mantener horizontes visuales limpios e interconexion regional continua.
   - Suprime etiquetas toponimicas de asentamientos perifericos (`patch_urban_core_labels`).
2. **Nucleo Urbano Denso (`urban_core_polygon`):**
   - Conserva la red capilar residencial, calles locales, andadores peatonales y toponimia urbana.
   - Restringe la extrusion de edificios 3D a esta area (`patch_urban_core_lod`).
     Medir la reduccion sobre el build actual y comprobar los bordes del nucleo.

### 2.3. Autodeteccion Censal (Concave Hull)
En el Wizard Studio (Paso 1), el modelador puede trazar el poligono manualmente o pulsar **Auto Censo**, lo que calcula la envolvente concava (*Concave Hull*) vectorizada sobre los centroides censales de manzanas del INEGI (`/api/auto-urban-polygon`).

---

## 3. Filtrado de Macro-Parques Urbanos (`urban_parks_only` - Regla 13)

En OpenStreetMap, gigantescas selvas virgenes, reservas de biosfera y sierras rurales estan clasificadas como `leisure=nature_reserve` o `boundary=protected_area`. Al renderizarse, tiñen arbitrariamente de verde cientos de kilometros cuadrados rurales.

Cuando `urban_parks_only: true` (o al seleccionarlo en el Wizard):
- Se activa el parche `patch_urban_parks` (`SB_URBAN_PARKS_ONLY=1`) en WSL 2.
- Se descartan reservas masivas no urbanizadas.
- Se preservan unicamente parques civicos, plazas, jardines y camellones urbanos.

---

## 4. Regla Canonica "Campus Wins" y Etiquetado Dual (Regla 16)

### El Conflicto de Solapamiento Comercial/Universitario
En muchas ciudades universitarias, los campus centrales contienen locales comerciales (bancos, tiendas, librerias). Si el motor vectorial renderiza ambos poligonos en el mismo espacio, el estilo comercial de MapLibre sobrescribe el campus, desapareciendo la representacion visual de la universidad.

### La Solucion "Campus Wins" (`patch_campus_wins`)
1. **Mascara Vectorial:** Construye una mascara unificada de todos los poligonos universitarios:
   $$\text{college\_mask} = \operatorname{unary\_union}(\text{college\_geoms})$$
2. **Sustraccion Geometrica:** Sustrae dicha mascara de todos los poligonos comerciales superpuestos:
   $$\text{geom}_{\text{comercial}} = \text{geom}_{\text{comercial}} \setminus \text{college\_mask}$$
3. **Etiquetado Dual:** Inyecta explicitamente los pares canónicos:
   - Para universidades: `type: 'college', kind: 'college'`.
   - Para comercio: `type: 'commercial', kind: 'commercial'`.

---

## 5. Resiliencia de Edificios 3D y Salvaguardas de RAM (Regla 16)

El script `tools/patch_depot_wsl.py` aplica parches fundamentales sobre el codigo fuente de `depot.maps`:

1. **Simplificacion Nativa en GeoPandas (`patch_resilient_buildings`):**
   Sustituye la dependencia fragil de comandos CLI de Mapshaper/Node.js por una rutina vectorizada nativa en Python con GeoPandas y Shapely, inmune a fallos por limites de argumentos en la consola de Linux.
2. **Salvaguarda de RAM en MapGen (`patch_ram_safety`):**
   El parche trata un calculo historico de memoria con conversion duplicada por
   1024. Comprobar que la revision instalada aun contiene ese patron antes de
   aplicar el reemplazo; un mensaje Killed no identifica por si solo este defecto.
3. **Desbloqueo CPU y Zoom de Agua:**
   Revisar opciones de CPU y zoom de la mascara de agua en el parche vigente.
   Verificar rendimiento y resolucion de salida para la costa objetivo.
4. **Toponimia Universal NWR (`patch_polygon_neighborhoods`):**
   Extrae asentamientos y colonias representados en OSM como relaciones o vias cerradas (`nwr/place`), convirtiendolos a centroides de punto y fusionandolos con el catalogo de DENUE.
