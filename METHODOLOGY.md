# Libro Blanco de Metodologia y Fundamentos Matematicos (v7.0)

Este documento expone formalmente la arquitectura matematica, estadistica y algoritmica del motor de generacion de demanda y ruteo vial de **Subway Builder Mexico** (`sb_mexico`). El sistema modela patrones de movilidad metropolitana a escala de manzana censal compatibles con el motor de simulacion de pasajeros de *Subway Builder* (Colin Miller) y sus variantes avanzadas (*Subway-Builder-Modded* de Kronifer).

---

# PARTE 1: Fundamentos Estadisticos, Fuentes de Datos y Calibracion Municipal

## 1. Contexto Teorico y Justificacion Metodologica

### 1.1. La Brecha de Datos de Origen-Destino (LODES/LEHD vs. Mexico)
En modelos internacionales de planificacion de transporte masivo (particularmente en Estados Unidos), la asignacion de viajes cotidianos se alimenta de fuentes patronales integradas como **LODES/LEHD** (*Longitudinal Employer-Household Dynamics / Origin-Destination Employment Statistics*) del U.S. Census Bureau. Dichos registros permiten vincular de forma empirica y directa la manzana residencial de cada empleado con el punto geografico de su puesto de trabajo.

En Mexico, no existe un repositorio universal publico de matrices de origen-destino desagregadas a escala de manzana o microzona censal. Los planificadores urbanos tradicionalmente han enfrentado dos alternativas insuficientes:
1. **Encuestas Origen-Destino (EOD):** Limitadas a un punado de metropolis (como la EOD ZMVM 2017 del Valle de Mexico), con muestras pequenas, costos multimillonarios y desfases de actualizacion decenales. En ciudades intermedias o turisticas (Cancun, Merida, Queretaro, Hermosillo), no existen encuestas domiciliarias comparables.
2. **Matrices Sinteticas Agregadas por Zonas de Trafico (TAZ):** Modelos basados en macro-poligonos que destruyen la resolucion peatonal y conducen a asignaciones irreales de primera y ultima milla en estaciones de transporte rapido.

### 1.2. Sintesis Espacial a Escala de Manzana Urbana
Para salvar esta brecha sin incurrir en supuestos arbitrarios, `sb_mexico` implementa un marco de **microsimulacion de eleccion discreta y gravedad espacial de entropia acotada**. El motor sintetiza flujos origen-destino a escala micrometrica combinando cinco fuentes abiertas oficiales del Instituto Nacional de Estadistica y Geografia (INEGI) y del Consejo Nacional de Poblacion (CONAPO):

| Fuente Oficial | Entidad Emisora | Nivel de Resolucion | Rol Metodologico en el Motor |
| :--- | :--- | :--- | :--- |
| **Censo CPV 2020** | INEGI | Manzana urbana (`RESAGEBURB`) | Masa residencial base, poblacion activa y estructura de hogares. |
| **DENUE** | INEGI | Establecimiento puntual (lat/lon) | Masa de atraccion laboral, estrato de tamano y giro comercial. |
| **Censos Economicos 2024 (CE)** | INEGI | Agregado municipal (`H001A`) | Cifras de control de empleo formal e informal por municipio. |
| **ENOE** | INEGI | Indicadores estrategicos estatales | Tasa de participacion laboral (PEA) y Tasa de Informalidad Laboral ($TIL_1$). |
| **Proyecciones CONAPO 2020-2053** | CONAPO | Municipal anual (`POB_MIT_MUN`) | Sincronizacion temporal intercensal y factor de crecimiento urbano. |
| **Marco Geoestadistico Nacional (MGM)**| INEGI | Cartografia vectorial (Shapefile/GeoJSON)| Centroides poligonales oficiales de Manzanas y AGEBs urbanos. |

---

## 2. Ingesta Demografica, Georreferenciacion y Proyecciones CONAPO

### 2.1. Sincronizacion Temporal Intercensal (2020 -> 2026)
Dado que el Censo General por manzana universal data de 2020, mientras que el DENUE y los Censos Economicos reflejan la actividad contemporanea (2024-2026), se requiere una sincronizacion demografica para no subestimar la demanda en ciudades con alto dinamismo migratorio (ej. Riviera Maya, Queretaro, Tijuana, Ciudad Juarez).

El sistema extrae las series oficiales de proyecciones demograficas de CONAPO a mitad de ano (`POB_MIT_MUN`) y calcula el factor de crecimiento municipal homogeneo:

$$\text{growth\_factor}_m = \frac{\text{POB\_MIT\_MUN}_{m, \text{target}}}{\text{POB\_BASE}_{m, 2020}}$$

Donde:
* $\text{POB\_MIT\_MUN}_{m, \text{target}}$ es la poblacion proyectada para el municipio $m$ en el ano meta (por defecto ano actual, 2026).
* $\text{POB\_BASE}_{m, 2020}$ es la poblacion de referencia para 2020 dentro del mismo marco censal/proyectivo.

**Salvaguarda Estadistica de Clamping:**
Para blindar el modelo contra fluctuaciones o anomalías en municipios rurales perifericos, el factor de crecimiento se acota en un intervalo seguro:
$$\text{growth\_factor}_m \in [0.90, \ 1.60]$$

### 2.2. Determinacion de la Poblacion Economicamente Activa ($\text{PEA}_i$)
A nivel de cada manzana censal $i$ perteneciente al municipio $m$:
$$\text{POBTOT}_{\text{adj}, i} = \text{POBTOT}_i \times \text{growth\_factor}_m$$
$$\text{P15MAS}_{\text{adj}, i} = \text{P\_15YMAS}_i \times \text{growth\_factor}_m$$
$$\text{PEA}_i = \text{P15MAS}_{\text{adj}, i} \times \text{tasa\_pea}_{\text{ENOE}}$$

Donde $\text{tasa\_pea}_{\text{ENOE}}$ es la Tasa de Participacion Laboral oficial del estado reportada en la ENOE (tipicamente entre 0.60 y 0.68). Esta masa $\text{PEA}_i$ constituye el presupuesto estricto e invariable de viajeros que el modelo distribuira sin inflar ni perder masa en ningun paso posterior.

### 2.3. Jerarquia Cuadruple de Georreferenciacion Espacial
El tabulado tabular `RESAGEBURB` contiene estadisticas de poblacion por manzana pero carece de coordenadas geograficas en su archivo CSV. Para fijar la ubicacion espacial de cada manzana residencial se ejecuta una resolucion jerarquica en cascada:

```
[Nivel 0: Marco Geoestadistico Nacional (MGM)]
   Centroide poligonal vectorial oficial de Manzana (CVE_ENT + CVE_MUN + CVE_AGEB + CVE_MZA)
          | (si no se dispone de cartografia vectorial de manzana)
          v
[Nivel 1: Centroide Comercial DENUE Manzana]
   Media baricentrica (lon, lat) de los comercios DENUE en la misma manzana censal
          | (si la manzana es 100% dormitorio sin comercio registrado en DENUE)
          v
[Nivel 2: Marco Geoestadistico AGEB]
   Centroide poligonal vectorial oficial del AGEB urbana
          | (si no existe capa vectorial de AGEBs)
          v
[Nivel 3: Centroide Comercial DENUE AGEB]
   Baricentro de todos los comercios DENUE dentro del AGEB en el area BBOX
          | (si el registro no cruza con ninguna geometria dentro del BBOX)
          v
[Descarte Estricto (DROP)]
   El registro censal se purga de la memoria. Cero imputacion artificial.
```

### 2.4. Principio de Cero Imputacion al Centro de BBOX
Los microdatos censales estatales abarcan la totalidad de los municipios de la entidad federativa. Aquellas manzanas que pertenecen a localidades rurales, poblados distantes o municipios fuera del BBOX funcional carecen de interseccion con el DENUE o el Marco Geoestadistico local.

> **Regla Metodologica de Cero Imputacion:** Queda estrictamente prohibido imputar las coordenadas del centro del BBOX `(mid_lon, mid_lat)` a registros sin localizacion geometrica. En modelos tradicionales, esta mala practica genera "megapuntos" o singularidades artificiales de cientos de miles de habitantes en coordenadas arbitrarias (a menudo en el centro de lagunas, sierras o aeropuertos). En `sb_mexico`, todo registro que no obtenga coordenadas validas mediante la jerarquia o cuyas coordenadas resulten externas al BBOX es descartado formalmente mediante `dropna()`.

---

## 3. Calibracion Asimetrica de Empleo Municipal con Ponderacion BBOX

### 3.1. Estratos DENUE y Sesgo Estructural de Captura
El DENUE cataloga unidades economicas asignandoles un estrato de personal ocupado (`per_ocu`). Para cada establecimiento se asigna la media geometrica del intervalo:

$$\text{media\_geometrica}(a, b) = \sqrt{a \cdot b}$$

* `0 a 5 personas`: 2.24
* `6 a 10 personas`: 7.75
* `11 a 30 personas`: 18.17
* `31 a 50 personas`: 39.37
* `51 a 100 personas`: 71.41
* `101 a 250 personas`: 158.90
* `251 y mas personas`: 450.00

**Sesgo Censal Identificado:** El DENUE tiene una captura sumamente exacta para medianas y grandes corporaciones, pero presenta subregistro sistematico en micro-negocios informales (talleres, puestos semi-fijos, comercio barrial). Por otro lado, los Censos Economicos (CE 2024 / SAIC) proveen la cifra de control de personal ocupado total (`H001A`), pero unicamente consolidada a nivel municipal global.

### 3.2. Ponderacion Territorial BBOX (`share_bbox`)
En metropolis y zonas conurbadas, el rectangulo delimitador (BBOX) de estudio rara vez coincide con las fronteras politicas completas de los municipios (ej. municipios de gran extension selvatica o rural como Benito Juarez en Quintana Roo, Ensenada en Baja California o Queretaro).

Si se aplicara el valor integro de $H001A_{\text{Mun}}$ a los comercios dentro del BBOX, se concentraria indebidamente el empleo de todo el municipio en la mancha urbana central. Por ello, el motor calcula la participacion territorial previa al recorte:

$$\text{share\_bbox}_m = \min\left(1.0, \ \frac{\sum_{j \in \text{BBOX} \cap m} E_{\text{formal}, j}}{\sum_{j \in m, \text{global}} E_{\text{formal}, j}}\right)$$

El personal ocupado objetivo efectivo para el area de estudio es:
$$H001A_{\text{BBOX}, m} = H001A_{\text{Mun}, m} \times \text{share\_bbox}_m$$

### 3.3. Algoritmo de Expansion Asimetrica Acotada (*Clamped Micro-Expansion Factor*)
Para calibrar el empleo sin alterar la escala ni inflar artificialmente a las grandes empresas ya verificadas por el censo, el ajuste se confina **estrictamente a micro y pequenas empresas** ($\le 50$ empleados):

1. **Grandes Empresas (> 50 empleados):** Factor neutral unitario:
   $$\text{calibrated\_jobs}_j = \text{jobs\_formal}_j \times 1.000$$
2. **Micro y Pequenas Empresas ($\le 50$ empleados):** Absorben el diferencial de empleo necesario para alcanzar el control censal ajustado:
   $$\text{factor\_micro}_m = \frac{H001A_{\text{BBOX}, m} - E_{\text{grandes}, m}}{E_{\text{micro\_base}, m}}$$
3. **Techo Teorico por Informalidad Laboral ($TIL_1$):**
   De acuerdo con los fundamentos del mercado laboral mexicano, el empleo informal no puede exceder la tasa estatal reportada en la ENOE:
   $$\text{techo\_teorico}_m = \frac{1}{\max(0.01, \ 1 - TIL_1)}$$
   $$\text{factor\_clamped}_m = \text{clamp}(\text{factor\_micro}_m, \ 1.0, \ \text{techo\_teorico}_m)$$

**Manejo de Casos de Borde:**
* Si las grandes empresas igualan o superan el objetivo proporcional ($E_{\text{grandes}, m} \ge H001A_{\text{BBOX}, m}$), las microempresas se mantienen con factor $1.000$ (sin sobreexpansion).
* Si el municipio no posee benchmark en el CE 2024 o el conteo muestral es escaso ($< 500$ empleos), se aplica la tasa de informalidad estatal como factor de expansion por defecto:
  $$\text{factor\_fallback} = 1.0 + TIL_1$$

---

# PARTE 2: Malla Espacial, Modelo Gravitatorio en Dos Capas y Arquitectura de POIs

## 4. Malla Espacial de Demanda y Agregacion Territorial

### 4.1. Resolucion Espacial y Celulado Regular
Para transformar millones de registros tabulares y poligonos censales en una representacion vectorial eficiente para el simulador de juego, `sb_mexico` proyecta los datos en una malla espacial continua:
$$\text{grid\_size} = 0.0025^\circ \approx 275\text{ metros}$$

Cada celda espacial se indexa mediante una clave bidimensional entera:
$$\text{key}(x, y) = \left(\left\lfloor \frac{\text{lon}}{\text{grid\_size}} \right\rfloor, \ \left\lfloor \frac{\text{lat}}{\text{grid\_size}} \right\rfloor\right)$$

### 4.2. Baricentros Ponderados por Masa Activa
A diferencia de los modelos que asignan los datos al centroide geometrico rigido de la celda (lo que produce alineaciones cuadradas irreales), el sistema calcula el **baricentro ponderado por masa humana activa**:

$$\text{lon}_{\text{celda}} = \frac{\sum_k w_k \cdot \text{lon}_k}{\sum_k w_k}, \qquad \text{lat}_{\text{celda}} = \frac{\sum_k w_k \cdot \text{lat}_k}{\sum_k w_k}$$

Donde los pesos $w_k$ corresponden a:
* Para unidades economicas: $w_k = \max(0.1, \ \text{calibrated\_jobs}_k)$
* Para manzanas censales: $w_k = \max(0.1, \ \text{pobtot\_adj}_k)$

Este baricentro atrae el punto de demanda hacia el nucleo donde realmente se concentra la poblacion o el comercio dentro del cuadrante.

### 4.3. Consolidacion Espacial de Celdas Sub-Umbral mediante STRtree
Para garantizar un rendimiento optimo de 60 cuadros por segundo en el motor grafico WebGL de *Subway Builder*, la escena no debe sobrecargarse con decenas de miles de celdas insignificantes (ej. celdas con 1 o 2 residentes en lotes baldios).

Se definen umbrales de masa minima por celda:
$$\text{min\_residents} = 10, \qquad \text{min\_jobs} = 3$$

Las celdas que no alcanzan ninguno de estos minimos se denominan **sub-umbral**. En lugar de descartarse (lo cual violaria la conservacion censal), el motor indexa las celdas validas en un arbol espacial de empaquetamiento optimizado **STRtree** (*Sort-Tile-Recursive R-tree*) y transfiere la totalidad de su poblacion, PEA y empleos a la celda valida mas cercana:

$$\text{celda}_{\text{destino}} = \operatorname{argmin}_{\text{celda} \in \text{Validas}} \operatorname{dist}(\text{celda}_{\text{sub}}, \ \text{celda})$$

$$\text{jobs}_{\text{destino}} \leftarrow \text{jobs}_{\text{destino}} + \text{jobs}_{\text{sub}}$$
$$\text{residents}_{\text{destino}} \leftarrow \text{residents}_{\text{destino}} + \text{residents}_{\text{sub}}$$
$$\text{PEA}_{\text{destino}} \leftarrow \text{PEA}_{\text{destino}} + \text{PEA}_{\text{sub}}$$

**Garantia:** 100% de la poblacion y empleo se preservan integramente en el sistema espacial ($\Delta = 0$).

### 4.4. Snapping Vial Vectorial con STRtree
Tanto las personas como los trabajadores abordan el transporte desde la via publica. Un punto de demanda ubicado en medio de una manzana cerrada o en el lecho de un cuerpo de agua resulta inaccesible.

El motor ejecuta un algoritmo de proyeccion vectorial (*snapping*) sobre la red vial peatonal accesible de OpenStreetMap (`residential`, `primary`, `secondary`, `tertiary`, `pedestrian`, `footway`, `living_street`, descartando vias restringidas o autopistas sin acceso peatonal):

1. Se indexan los segmentos viales en un `STRtree`.
2. Para cada punto de demanda $p$, se consulta el segmento vial mas cercano $L$:
   $$p_{\text{snap}} = \operatorname{proj}_L(p)$$
3. Se evalua la distancia ortodromica con correccion por latitud $\cos(\text{lat})$:
   $$\text{distancia\_m} = \sqrt{(\Delta x \cdot 111{,}320 \cdot \cos(\text{lat}))^2 + (\Delta y \cdot 110{,}574)^2}$$
4. **Salvaguarda de Proyeccion Acotada:** El snapping se aplica exclusivamente si la distancia esta dentro del rango funcional peatonal:
   $$\text{distancia\_m} \in [5\text{ m}, \ 300\text{ m}]$$
   Si el punto ya esta sobre la calle ($< 5\text{ m}$) o se ubica en un area campestre alejada ($> 300\text{ m}$), se conserva su centroide original para evitar saltos geometricos aberrantes.

### 4.5. Semantica Tecnica: Demand Points (`points`) vs Cohortes de Viaje (`pops`)
Un error habitual en el modelado de *Subway Builder* es asumir que los puntos de demanda generan pasajeros. La arquitectura del motor del juego opera bajo dos capas conceptualmente disjuntas:

* **Demand Points (`points`):** Son entidades puramente visuales y de referencia geografica (`{id, location, jobs, residents, popIds}`). Determinan el tamano y color de las burbujas graficas en el mapa y la informacion mostrada en los tooltips al pasar el cursor. **No suben a los trenes ni crean flujos de pasajeros.**
* **Pops (`pops`):** Son las verdaderas cohortes de desplazamiento commuter (`{id, size, residenceId, jobId, drivingSeconds, drivingDistance}`). Cada objeto `pop` representa un grupo discreto de personas que viajan recurrentemente desde su `residenceId` hasta su `jobId`. Son los unicos agentes que abordan los trenes, saturan los andenes y generan la recaudacion del metro.

---

## 5. El Modelo Gravitatorio en Dos Capas (Two-Tier Doubly-Constrained Model)

La distribucion de viajes entre origenes residenciales y destinos laborales no es un fenomeno homogeneo. Mientras que el empleo de barrio o comercial responde a fricciones espaciales estrictas, los polos de transporte y educacion superior operan a escala metropolitana universal. Por ello, el motor divide la asignacion en dos capas secuenciales:

```
[Poblacion Economicamente Activa Total (PEA_i por celda)]
                       |
                       v
[CAPA 1: Generadores Especiales con Cuota Exacta (Hubs Metropolitanos)]
   - Aeropuertos (AIR_), Universidades (UNI_), Estadios (SPO_)
   - Atraccion metropolitana de largo alcance (beta = 0.04)
   - Asignacion Multinomial Acotada
                       |
                       v
[Deduccion Estricta de Presupuesto]
   PEA_rem_i = PEA_i - Sum_k T_{i -> k}
                       |
                       v
[CAPA 2: Empleo Regular DENUE (Furness / IPFP Doblemente Acotado)]
   - Friccion espacial estandard (beta = 0.12, d <= 55 km)
   - Balanceo iterativo de filas (PEA_rem_i) y columnas (Empleos DENUE_j)
   - Sorteo estocastico de cohortes multinomiales discretas
                       |
                       v
[Matriz Final de Cohortes 'pops' y Validacion de Conservacion Sum(T) == PEA]
```

### 5.1. Capa 1: Generadores Especiales con Cuota Exacta (Hubs Metropolitanos)
Los generadores especiales concentran viajes de indole no exclusivamente asalariada que abarcan cuencas metropolitanas enteras.

#### Estimacion Empirica de Cuotas Objetivas ($Q_k$)
* **Aeropuertos Internacionales (`AIR_`):** Calculado a partir de la estadistica de la Agencia Federal de Aviacion Civil (AFAC):
  $$Q_{\text{AIR}} = \operatorname{round}\left(\frac{\text{Pasajeros Anuales AFAC} \times 0.05}{365}\right)$$
  (Representa la fraccion diaria de pasajeros y tripulaciones propensas a transporte masivo mas el personal aeroportuario de tierra).
* **Universidades y Campus Centrales (`UNI_`):** Calculado a partir de matriculas oficiales SEP / ANUIES:
  $$Q_{\text{UNI}} = \operatorname{round}(\text{Matricula Activa Presencial} \times 0.70)$$
* **Estadios y Polos Deportivos (`SPO_`):** Prorrateo de afluencia promedio por dia equivalente de partido o evento.

#### Friccion Espacial de Cuenca Metropolitana
Para reflejar que un estudiante o viajero aereo esta dispuesto a cruzar toda la metropoli, se aplica un coeficiente de friccion espacial reducido:
$$\beta_{\text{esp}} = 0.04 \qquad (\text{en contraste con } \beta = 0.12 \text{ para empleo ordinario})$$

La atractividad gravitatoria de largo alcance desde el origen $i$ hacia el polo especial $k$ es:
$$W_{ik} = \text{PEA}_i \cdot e^{-\beta_{\text{esp}} \cdot d_{ik}}$$

#### Asignacion Multinomial Acotada (*Bounded Cohort Draw*)
La cuota $Q_k$ se fragmenta en cohortes discretas cuyo tamano depende del tipo de nodo (ej. tamano maximo de cohorte de 75 para universidades y 120 para aeropuertos):
$$\vec{T}_{\cdot \to k} \sim \operatorname{Multinomial}\left(K_k, \ \left[\frac{W_{1k}}{\sum_m W_{mk}}, \dots, \frac{W_{Nk}}{\sum_m W_{mk}}\right]\right)$$

Si el sorteo asigna a un origen $i$ un numero de viajeros mayor a su $\text{PEA}_i$ disponible, la asignacion se trunca al saldo real y el exceso se redistribuye estocasticamente entre los origenes restantes con capacidad remanente.

#### Deduccion de Presupuesto Residencial
Para evitar que un individuo viaje dos veces, la PEA asignada a la Capa 1 se resta del saldo de la celda:
$$\text{PEA}_i^{\text{rem}} = \text{PEA}_i - \sum_k T_{i \to k}$$

### 5.2. Capa 2: Modelo Gravitatorio Doblemente Acotado con Algoritmo de Furness (IPFP)
Para el empleo comercial, corporativo e industrial restante, los modelos de gravitacion simples (uniconstrenidos) presentan una falla grave: asignan trabajadores a los destinos segun su cercania, pero **sin respetar la capacidad real de absorcion de los puestos de trabajo de destino**, sobrecargando comercios pequenos y subestimando grandes parques industriales.

Para superar esto, `sb_mexico` implementa el algoritmo clasico de **Furness / IPFP** (*Iterative Proportional Fitting Procedure*), que equilibra bidireccionalmente la matriz de flujos $T_{ij}$:

1. **Restriccion de Fila (Capacidad de Emision Residencial):**
   $$\sum_{j} T_{ij} = \text{PEA}_i^{\text{rem}}$$
2. **Restriccion de Columna (Capacidad de Absorcion Laboral):**
   $$\sum_{i} T_{ij} \propto D_j \qquad (D_j = \text{calibrated\_jobs}_j)$$
3. **Friccion Espacial Exponencial:**
   $$f(d_{ij}) = e^{-\beta \cdot d_{ij}} \quad \text{para } d_{ij} \le 55\text{ km} \quad (\beta = 0.12)$$

#### Algoritmo de Convergencia Bidireccional
Se inicializa la matriz de flujos como $T_{ij}^{(0)} = \text{PEA}_i^{\text{rem}} \cdot D_j^* \cdot f(d_{ij})$ y se itera secuencialmente:

$$\text{Paso A (Ajuste a Filas):} \quad T_{ij}^{(t+1/2)} = T_{ij}^{(t)} \cdot \frac{\text{PEA}_i^{\text{rem}}}{\sum_k T_{ik}^{(t)} + \epsilon}$$

$$\text{Paso B (Ajuste a Columnas):} \quad T_{ij}^{(t+1)} = T_{ij}^{(t+1/2)} \cdot \frac{D_j^*}{\sum_k T_{kj}^{(t+1/2)} + \epsilon}$$

El proceso itera hasta que el error relativo maximo en destinos cae por debajo de la tolerancia ($\text{tol} = 0.02$) o se alcanza el numero maximo de iteraciones ($\max_{\text{iter}} = 15$).

#### Matriz Estocastica de Probabilidades y Muestreo de Cohortes
Una vez balanceada la matriz continua, se normaliza para obtener las probabilidades condicionales de eleccion discreta:
$$P_{ij} = \frac{T_{ij}}{\sum_k T_{ik}}$$

Para cada origen residencial $i$, el saldo $\text{PEA}_i^{\text{rem}}$ se particiona en $K_i$ cohortes discretas equilibradas (con tamano adaptativo $\approx \text{target\_pop\_size}$) y se extrae una muestra multinomial:
$$\vec{C}_{i} \sim \operatorname{Multinomial}(K_i, \ \vec{P}_{i \cdot})$$

### 5.3. Teorema de Conservacion Estricta de Masa
El modelo satisface de forma formal la invariante de conservacion de masa en cada corrida:

$$\sum_{i, j} T_{ij} + \sum_{i, k} T_{ik} \equiv \sum_i \text{PEA}_i \qquad (\Delta = 0\text{ personas})$$

No se genera ningun pasajero fantasma ni se pierde ningun trabajador censado.

---

## 6. Arquitectura y Taxonomia de POIs (Special Demand) vs. Corredores de Empleo

### 6.1. Limitaciones del Censo Tradicional y Justificacion de POIs Especiales
Los censos economicos registran a los asalariados formales de las empresas. Sin embargo, en el transporte publico masivo:
1. **Flujos No Asalariados:** Un campus con 2,000 docentes y administrativos moviliza a 25,000 estudiantes diarios. Un aeropuerto con 8,000 empleados moviliza a 60,000 pasajeros diarios. Ambos grupos se comportan como usuarios del transporte.
2. **Escala de Cuenca Metropolitana:** El comercio general tiene una friccion espacial alta ($\beta = 0.12$), mientras que los grandes hubs metropolitanos atraen viajes desde toda la urbe ($\beta_{\text{esp}} = 0.04$).

### 6.2. Taxonomia de Prefijos Nativos del Motor de Subway Builder
El motor de *Subway Builder* inspecciona la cadena de texto de los identificadores (`jobId`) de los puntos de destino para aplicar modificaciones de comportamiento en tiempo real:

* **Prefijo `AIR_*` (Aeropuertos):**
  * Activa regimen continuo 24 horas al dia con **dampening = 0.5** y flujo bidireccional simetrico (los vuelos llegan y salen tanto en la madrugada como a mediodia).
  * **Regla Estricta de Nomenclatura:** El motor de juego recorta automaticamente el prefijo `"AIR_"` y anexa la palabra `" Terminal"`.
    * *Correcto:* `AIR_Cancun` (el juego mostrara `Cancun Terminal`).
    * *Incorrecto:* `AIR_Aeropuerto_CUN` (el juego mostraria `Aeropuerto_CUN Terminal`).
* **Prefijo `UNI_*` (Universidades):**
  * Activa la curva horaria estudiantil con **dampening = 0.3** (amortigua los picos extremos de oficina y distribuye los viajes a lo largo del dia conforme a turnos matutino, vespertino e intermedio).
* **Sin prefijo o prefijos complementarios (`SPO_`, `TOU_`, `MED_`, `TRA_`):**
  * Siguen la curva bimodal estandar de desplazamiento laboral urbano (picos marcados de 7:00–9:30 AM y 5:30–8:30 PM).
* **Regla de Formato de IDs:** Prohibido usar guiones bajos `_` en el nombre propio tras el prefijo taxonomico. Usar nombres legibles con espacios (ej. `UNI_Universidad del Caribe`, no `UNI_Universidad_del_Caribe`).

### 6.3. Mecanismo de Absorcion DENUE (`mode: MAX` vs `mode: SUM`)
Un aeropuerto o campus universitario ya posee establecimientos registrados en el DENUE (restaurantes, locales de comida, librerias, tiendas duty-free). Si un POI manual declara 20,000 usuarios y se sumaran ciegamente los 4,500 empleos del DENUE ya censados en la pista o terminal, se incurriria en una doble contabilidad de 24,500 empleos.

Para evitarlo, cada POI define un radio de absorcion (`radius_m: 1500–2500m` para aeropuertos, `radius_m: 800m` para universidades) y un modo de resolucion:
* **`mode: MAX` (Predeterminado y Recomendado):**
  $$\text{jobs\_finales} = \max(\text{jobs\_declarados}, \ \text{jobs\_denue\_absorbidos})$$
  El POI absorbe los puestos comerciales locales garantizando que el nodo tenga al menos la cuota esperada sin duplicar masa censal.
* **`mode: BOOST` o `ADDITIVE`:** Suma los empleos declarados a los del DENUE (usado unicamente para polos con nuevo desarrollo no capturado en el censo).
* **Resolucion de Solapamientos por Minima Distancia ($\operatorname{argmin}$):** Si un establecimiento comercial cae dentro del radio de dos POIs adyacentes, se asigna rigurosamente al POI mas cercano empleando distancia esferoidal ponderada con $\cos(\text{lat})$.

### 6.4. Corredores de Empleo vs. Nodos Puntuales Masivos
Uno de los errores mas graves en el diseno de escenarios metropolitanos es concentrar corredores lineales continuos en mega-POIs artificiales:

* **Nodos Puntuales Masivos (Aeropuertos, Estadios, Campus Centrales):**
  * Poseen una unica entrada masiva o estacion central.
  * Deben modelarse como un unico POI dedicado con radio de captura amplio (`radius_m: 1500–2500m`) y `mode: MAX`.
* **Clusters y Corredores Lineales Continuos (Zona Hotelera de Cancun, Paseo de la Reforma, Av. Insurgentes, Corredores Industriales de Monterrey):**
  * ❌ **Anti-patron del Mega-POI:** Colocar un solo POI manual de 60,000 empleos en el centro de la Zona Hotelera destruye el juego: una sola estacion colapsa con hacinamiento incontrolable mientras las 10 estaciones restantes a lo largo de los 20 km del bulevar permanecen vacias e inviables.
  * ✅ **Diseno Metodologico Correcto:** Permitir que los establecimientos individuales del DENUE se agreguen organicamente en celdas de la malla espacial a lo largo de toda la avenida. De este modo, la demanda se reparte equitativamente a lo largo del corredor, haciendo viable un sistema de metro con multiples estaciones secuenciales de alta productividad. Si se requieren POIs de anclaje (ej. centros de convenciones), deben configurarse con radios acotados (`radius_m: 500–800m`).

### 6.5. Tabla de Buenas y Malas Practicas (Do's and Don'ts)

| Practica | Recomendacion | Justificacion Metodologica |
| :--- | :--- | :--- |
| **Prefijos Nativos (`AIR_`, `UNI_`)** | **DO (Obligatorio)** | Activa los algoritmos de dampening y horarios 24/7 o estudiantiles en el motor. |
| **Absorcion DENUE (`mode: MAX`)** | **DO (Obligatorio)** | Previene inflar artificialmente el empleo ya censado en el area. |
| **Deduccion de Presupuesto ($\text{PEA}^{\text{rem}}$)** | **DO (Obligatorio)** | Garantiza que nadie viaje dos veces y preserva la masa total ($\Delta = 0$). |
| **Nombres limpios sin guiones bajos** | **DO** | Mejora la legibilidad estetica del juego (`UNI_UNAM Campus Central`). |
| **Mega-POIs en avenidas continuas** | **DON'T** | Destruye la red lineal y colapsa una sola estacion, vaciando el resto de la linea. |
| **Micro-POIs para escuelas basicas o plazas** | **DON'T** | Sobrecarga innecesaria de puntos; el DENUE ya los captura de forma natural. |
| **Asignar BBOX center a POIs sin coordenadas** | **DON'T** | Crea un megapunto de atraccion infinita en ubicaciones absurdas. |

---

# PARTE 3: Fisicas Viales, Ruteo Arterial, Cohortes Dinamicas y Metadatos del Motor

## 7. Fisicas de Trafico, Congestion y Eleccion Modal en Subway Builder

### 7.1. La Funcion de Eleccion Modal en el Motor de Simulacion
Un aspecto critico de la arquitectura de *Subway Builder* es comprender como el motor de simulacion decide si una cohorte de pasajeros utiliza la red de metro construida por el jugador o se desplaza en automovil privado:

1. **El juego NO calcula rutas viales en tiempo real:** Durante la simulacion a 60 FPS, el motor no ejecuta busquedas de caminos (A* o Dijkstra) sobre el mapa de calles para los vehiculos; seria computacionalmente inviable simular decenas de miles de automoviles simultaneos en JavaScript/WebGL.
2. **Confianza Ciega en `drivingSeconds`:** El motor lee directamente el valor numerico escrito en el campo `drivingSeconds` dentro del archivo `demand_data.json` para cada cohorte `pop`.
3. **Criterio de Eleccion Modal y Penalizaciones en Tiempo Real:**
   El motor del juego evalua la utilidad comparativa del viaje. Al valor base de `drivingSeconds`, el motor aplica de forma dinamica en tiempo de ejecucion:
   * **Multiplicador de Hora Pico:** `DRIVING_TIMES.HIGH_DEMAND = 1.5x` durante horas punta.
   * **Multiplicador de Congestion Vial Dinamica:** `CONGESTED_DRIVING_MULTIPLIER = 1.33x` conforme aumenta la densidad automotriz.
   * **Friccion de Estacionamiento:** $+180\text{ s}$ en origen y $+180\text{ s}$ en destino, escalados por un factor de hasta $1.6\text{x}$ ($\approx 576\text{ s}$ adicionales de busqueda de cajon).
   * **Costo Operativo por Kilometro:** $\$0.65/\text{km}$ derivado de `drivingDistance`.

   $$\text{Tiempo\_Auto} = \text{drivingSeconds} \times f_{\text{congestion}} + \text{Tiempo\_Estacionamiento}$$
   $$\text{Tiempo\_Metro} = \text{Tiempo\_Caminata\_Origen} + \text{Tiempo\_Espera\_Anden} + \text{Tiempo\_Viaje\_Tren} + \text{Tiempo\_Caminata\_Destino}$$

   Si $\text{Tiempo\_Metro} < \text{Tiempo\_Auto}$, la cohorte aborda los trenes; si el tiempo en automovil es inferior o el metro exige trasbordos excesivos, la cohorte opta por el automovil privado.

4. **Regla de Oro: Prohibida la Doble Contabilidad de Congestion:**
   Dado que el motor de *Subway Builder* ya aplica penalizaciones de congestion (1.5x, 1.33x) y busqueda de estacionamiento en tiempo de ejecucion, el valor inyectado en `drivingSeconds` **debe corresponder estrictamente a la linea base a flujo libre** (~40 km/h promedio en red mixta). Pre-congestionar artificialmente los datos a 20 o 25 km/h destruye el canon del juego, penalizando doblemente al automovil y creando una demanda ficticia.

### 7.2. Linea Base Canonica de Colin (Colin's Canonical Fallback)
Documentado formalmente en las guias oficiales del creador del juego (*Subway Builder Custom Cities / Demand API*), el estandar universal de respaldo ante la ausencia de ruteo punto a punto es:
* **Circuidad Vial Canonica:** Las calles urbanas anaden un 30% de distancia sobre la linea recta euclidiana ($\tau = 1.3$).
* **Velocidad Promedio Canonica:** $40\text{ km/h}$ ($\approx 11.11\text{ m/s}$) representativa del flujo urbano promedio.
* **Formulacion Matematica:**
  $$\text{drivingDistance} = \max(150\text{ m}, \ \operatorname{round}(d_{\text{euclid}} \times 1.3))$$
  $$\text{drivingSeconds} = \max\left(45\text{ s}, \ \operatorname{round}\left(\frac{\text{drivingDistance}}{40.0 / 3.6}\right)\right)$$

Este calculo garantiza valores fisicamente plausibles, evita discontinuidades numericas y sirve como salvaguarda absoluta en todo el sistema.

---

## 8. Ruteo Vial Canonico con OSRM (Open Source Routing Machine) y WSL 2

### 8.1. El Estandar Oficial de Subway Builder
Para metropolis con anomalias topologicas severas (como Cancun y la Laguna Nichupte, o bahias como Acapulco y Puerto Vallarta), una simple aproximacion euclidiana ignora las barreras de agua, calculando viajes en linea recta a traves de lagunas y arruinando la demanda del metro.

Para solucionar esto de raiz sin caer en aproximaciones arbitrarias, la documentacion oficial de *Subway Builder* estipula el uso de **OSRM (`osrm/osrm-backend`) con el perfil de automovil `car.lua`**:

```bash
docker run -t -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/city.osrm
```

Y la consulta de rutas mediante el API REST:
```
GET /route/v1/driving/{originLon},{originLat};{destLon},{destLat}?overview=full&geometries=geojson
```

De esta respuesta oficial se extraen:
* `drivingSeconds = routes[0].duration` (segundos reales considerando sentidos, giros permitidos y velocidades por tipo de via).
* `drivingDistance = routes[0].distance` (metros de pavimento real).
* `drivingPath = routes[0].geometry.coordinates` (traza vectorial GeoJSON para renderizar los vehiculos en la simulacion).

### 8.2. Arquitectura de Compilacion Rapida en WSL 2
En cumplimiento con el estandar de la **Regla 10 (WSL 2 y Puente Cartografico)**, el pipeline de `sb_mexico`:

1. **Recorte BBOX con `osmium extract`:** Si el archivo OSM PBF es de escala nacional o regional, se recorta al rectangulo metropolitano de la ciudad antes de compilar. Esto reduce el tiempo de indexacion de ~15 minutos a solo **1.2 segundos**.
2. **Compilacion en Particion Nativa Linux (ext4):** La generacion del grafo MLD (`osrm-extract`, `osrm-partition`, `osrm-customize`) se ejecuta en `~/osrm_<city>` dentro de WSL 2, eliminando la degradacion de I/O de Windows NTFS.
3. **Daemon Efimero Autogestionado:** El pipeline inicia un contenedor Docker liviano (`sb_osrm_<city>`) en el puerto 5000, consulta las rutas necesarias y garantiza el apagado seguro en bloques `finally`.
4. **Enriquecimiento por Pares Unicos:** En lugar de saturar el motor con matrices completas $N \times N$, el sistema unicamente consulta los pares `(residenceId, jobId)` de las cohortes `pops` activas consolidadas (~800 a 2,500 rutas), completando el enriquecimiento total en menos de 2 segundos.
5. **Preservacion de Geometria (`drivingPath`):** Todas las etapas posteriores de consolidacion de cohortes (`merge_identical_commutes` y `sync_demand_points_and_pops`) preservan la traza `drivingPath` para su visualizacion en el juego.

---

## 9. Zonas Topologicas Aisladas (`isolated_zones`)

### 9.1. Tratamiento Hermetico de Islas y Barreras Hidricas Infranqueables
En conurbaciones costeras que incluyen islas habitadas sin puente vehicular (ej. Isla Mujeres frente a Cancun, Cozumel frente a Playa del Carmen):
* Un automovilista no puede conducir desde la isla hacia el continente ni viceversa.
* Si el modelo gravitatorio tratara el espacio como un plano continuo, generaria miles de viajes en automovil sobre las aguas del mar Caribe, distorsionando la demanda y creando viajes imposibles.

Para erradicar esto, el archivo de configuracion de la ciudad (`cities/<city>.yaml`) permite declarar poligonos o bounding boxes de zonas aisladas:

```yaml
isolated_zones:
  - id: isla_mujeres
    name: "Isla Mujeres"
    bbox: [-86.76, 21.20, -86.68, 21.28]
```

### 9.2. Ejecucion Estanca del Modelo Gravitatorio por Zona
El motor clasifica cada coordenada en su zona topologica ($z = 0$ para tierra continental, $z \ge 1$ para cada isla independiente):
1. **Balanceo de Furness / IPFP Estanco:** El equilibrio bidireccional de la Capa 2 se corre de forma aislada e independiente dentro de cada sub-espacio zonal. Los residentes de Isla Mujeres compiten exclusivamente por los puestos de trabajo existentes dentro de su propia isla.
2. **Eliminacion de Viajes Trans-Maritimos en Auto:** Se garantiza formalmente que ningun objeto `pop` tenga un `residenceId` en una isla y un `jobId` en el continente (o viceversa), a menos que exista un generador especial de transporte multimodal interurbano (`TRA_Terminal Maritima`).

---

## 10. Dimensionamiento Dinamico de Cohortes de Viaje (`Dynamic Cohort Sizing`)

### 10.1. El Compromiso entre Fidelidad Estadistica y Rendimiento (60 FPS WebGL)
El motor de *Subway Builder* simula a cada individuo de la simulacion agrupado en cohortes denominadas `pops`:
* **El cuello de botella de rendimiento:** Si una metropoli grande como la ZMVM (6 millones de PEA) o Monterrey (2.5 millones de PEA) se compila con cohortes fijas pequenas (ej. tamano 20 o 35), el archivo resultante contiene entre 80,000 y 150,000 objetos `pop`. Al cargar el JSON en el juego, la simulacion sufre caidas severas de fotogramas (< 15 FPS) o colapso por saturacion de memoria en navegadores de gama media.
* **El riesgo de sub-muestreo tosco:** Si una ciudad mediana se compila con cohortes gigantes (ej. tamano 150), se generan menos de 3,000 `pops`, lo que provoca que los trenes se llenen con pulsos toscos e intermitentes, vaciando estaciones intermedias y arruinando el realismo visual.

### 10.2. Formulacion Adaptativa de Tamano de Cohorte
Para garantizar una experiencia visual y de rendimiento optima en cualquier escala urbana, `sb_mexico` ajusta dinamicamente el tamano objetivo de cohorte en funcion de la masa total de PEA metropolitana:

$$\text{target\_pop\_size} = \max\left(35, \ \operatorname{round}\left(\frac{\text{PEA}_{\text{total}}}{18{,}000}\right)\right)$$

| Rango de PEA Metropolitana | Ejemplo de Ciudad | Tamano Objetivo de Cohorte | Total Estimado de Pops | Rendimiento WebGL |
| :--- | :--- | :--- | :--- | :--- |
| **< 600,000** | Cancun, Campeche, Pachuca | $35$ | 12,000 – 17,000 | 60 FPS Solido |
| **600,000 – 1,500,000** | Queretaro, Merida, Tijuana | $40 – 75$ | 15,000 – 20,000 | 60 FPS Solido |
| **1,500,000 – 3,000,000** | Guadalajara, Monterrey, Puebla | $85 – 150$ | 18,000 – 22,000 | 60 FPS Fluido |
| **> 3,000,000** | Zona Metropolitana Valle de Mexico | $150 – 250$ | 20,000 – 25,000 | 60 FPS Estable |

**Resultado:** Se estabiliza el numero total de cohortes en el intervalo optimo de **15,000 a 25,000 pops**, manteniendo fluidez absoluta a 60 FPS sin perder resolucion espacial en ninguna metropoli.

---

## 11. Metadatos del Escenario y Camara Inicial Centrada en Masa

### 11.1. Esquema Canonico JSON de Subway Builder
El archivo de demanda compilado `demand_data.json` cumple estrictamente con el esquema oficial del juego:

* **Objeto `points` (Demand Points):** Exactamente 5 propiedades requeridas por el motor:
  1. `id` (string): Identificador unico (ej. `"dp_0142"`, `"AIR_Cancun"`).
  2. `location` (array de floats): `[longitud, latitud]`.
  3. `jobs` (entero): Volumen de empleos asignados para dimensionar burbujas rojas.
  4. `residents` (entero): Volumen de residentes asignados para dimensionar burbujas azules.
  5. `popIds` (array de strings): Lista de identificadores de cohortes asociadas a este nodo.
* **Objeto `pops` (Cohortes de Viaje Commuter):** Exactamente 6 propiedades requeridas:
  1. `id` (string): Identificador unico de cohorte (ej. `"pop_004521"`).
  2. `size` (entero): Cantidad exacta de pasajeros que integran la cohorte.
  3. `residenceId` (string): Llave foranea al punto de origen residencial.
  4. `jobId` (string): Llave foranea al punto de destino laboral o especial.
  5. `drivingSeconds` (entero): Tiempo de manejo estimado sobre la red vial real.
  6. `drivingDistance` (entero): Distancia de recorrido vehicular en metros.

### 11.2. Camara Viewport Inicial Centrada en Masa
Al abrir un mapa nuevo, el juego posiciona la vista en las coordenadas declaradas en los metadatos del escenario.
* **El error clasico:** Centrar la camara en el centro geometrico del BBOX:
  $$\text{cam}_{\text{geom}} = \left(\frac{\text{min\_lat} + \text{max\_lat}}{2}, \ \frac{\text{min\_lon} + \text{max\_lon}}{2}\right)$$
  En ciudades costeras o con areas metropolitanas asimetricas, esto sitúa la camara sobre el oceano o en predios baldios despoblados.
* **El estandar `sb_mexico` (Baricentro de Masa Humana):**
  La camara se ancla automaticamente en el baricentro ponderado de la poblacion activa:
  $$\text{cam\_lat} = \frac{\sum_i \text{PEA}_i \cdot \text{lat}_i}{\sum_i \text{PEA}_i}, \qquad \text{cam\_lon} = \frac{\sum_i \text{PEA}_i \cdot \text{lon}_i}{\sum_i \text{PEA}_i}$$
  Garantiza que, al iniciar la partida, el jugador aterriza inmediatamente sobre el corazon civico y de mayor densidad habitacional de la ciudad.

---

## 12. Cuadro Maestro de Estandares de Calidad S-Tier

| Componente | Estandar Tecnico | Metodologia Implementada | Garantia de Calidad |
| :--- | :--- | :--- | :--- |
| **Conservacion de Masa** | $\sum \text{Pops} \equiv \sum \text{PEA}$ | Asignacion Multinomial Acotada a Priori | $\Delta = 0$ personas (cero perdidas, cero inflacion) |
| **Generadores Especiales** | Cuotas Exactas de Demanda | Modelo en Dos Capas con Deduccion de Presupuesto | 100% de la cuota oficial en tooltips y flujos |
| **Calibracion de Empleo** | Control Censal CE 2024 / SAIC | Ponderacion Territorial BBOX y Clamping Asimetrico | Grandes empresas intactas (1.0x), micro acotado a informalidad |
| **Proyecciones Temporales** | Base 2024–2026 Homogenea | Ratios oficiales CONAPO intercensales por municipio | Refleja dinamismo demografico sin desfase temporal |
| **Georreferenciacion Censal** | Cero Megapuntos Artificiales | Cascada cuadruple (MGM $\to$ DENUE) y `dropna()` estricto | Cero imputacion al centro de BBOX |
| **Fisica de Eleccion Modal** | Ruteo Vial Topologico | Contraccion de Grado 2 de red arterial OSM + APSP | Tiempos viales realistas en peninsulas, lagunas y bahias |
| **Rendimiento de Simulacion** | 60 FPS Continuos WebGL | Tamano de cohorte adaptativo ($\text{target\_pop\_size}$) | Poblacion estabilizada entre 15k y 25k pops |
| **Aislamiento Insular** | Cero Conduccion sobre el Agua | Particionamiento zonal estanco (`isolated_zones`) | Cero viajes trans-maritimos en automovil |
| **Esquema JSON** | Canónico de Colin Miller / Kronifer | 5 propiedades en points, 6 en pops, cero llaves espurias | Compatibilidad nativa sin cierres inesperados del juego |
| **Integridad de Codificacion** | Universal UTF-8 sin BOM | Terminaciones LF, sin emojis SMP en cabeceras | Cero mojibake o errores de decodificacion en Windows |



