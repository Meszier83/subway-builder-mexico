# Ingesta Demografica, Fuentes INEGI/CONAPO y Calibracion Municipal

Este documento detalla el tratamiento cientifico y estadistico de los microdatos abiertos de Mexico para generar matrices de demanda urbana a nivel de manzana censal sin distorsiones ni inflacion artificial.

---

## 1. Fuentes de Datos Oficiales y sus Roles

En Mexico no existe un repositorio publico universal de matrices origen-destino a escala micrometrica (como LODES/LEHD en EE. UU.). Subway Builder Mexico resuelve esto integrando 5 fuentes abiertas oficiales:

| Fuente Oficial | Entidad | Nivel de Resolucion | Rol en el Pipeline |
| :--- | :--- | :--- | :--- |
| **Censo CPV 2020** | INEGI | Manzana urbana (`RESAGEBURB`) | Poblacion total (`POBTOT`) y poblacion de 15 anos y mas (`P_15YMAS`). |
| **DENUE** | INEGI | Establecimiento puntual (lat/lon) | Masa de atraccion laboral, estrato de tamano y giro economico. |
| **Censos Economicos 2024 (CE)** | INEGI | Agregado municipal (`H001A`) | Cifras de control de empleo formal e informal municipal. |
| **ENOE** | INEGI | Indicadores estatales trimestrales | Tasa de participacion laboral (PEA) y Tasa de Informalidad ($TIL_1$). |
| **Proyecciones CONAPO 2020–2053** | CONAPO | Anual por municipio (`POB_MIT_MUN`)| Sincronizacion temporal intercensal (2020 -> ano actual). |
| **Marco Geoestadistico (MGM)** | INEGI | Shapefile/GeoJSON vectorial | Geometrias y centroides de Manzanas y AGEBs urbanos. |

---

## 2. Sincronizacion Temporal Intercensal (CONAPO)

Antes de calibrar, registrar archivo, organismo, cobertura, ano de referencia y
ano objetivo de cada fuente; no inferirlos de la fecha actual o un patron de nombre.
Inspeccionar `parse_conapo_projections` y su llamada desde el pipeline: confirmar
que el ano seleccionado se transmite realmente. Si hay varios valores `ANO`,
usar el elegido explicitamente; si falta `ANO`, requerir una confirmacion del ano
en el flujo del usuario y registrar su procedencia. Una vista previa no prueba
que el build uso el mismo ano.

Para DENUE, distinguir cobertura municipal completa del recorte BBOX y conservar
los totales anteriores al recorte (`mun_totals_global`) al calibrar `share_bbox`.
No certificar completitud solo porque el archivo o una etiqueta lo afirman.

El Censo universal por manzana data de 2020, mientras que el DENUE y los Censos Economicos reflejan actividad contemporanea (2024–2026). Para no subestimar la demanda en urbes con alto dinamismo migratorio (ej. Riviera Maya, Queretaro, Tijuana, Monterrey), se aplica una proyeccion demografica municipal derivada de CONAPO:

$$\text{growth\_factor}_m = \frac{\text{POB\_MIT\_MUN}_{m, \text{target}}}{\text{POB\_BASE}_{m, 2020}}$$

### Salvaguarda de Clamping y Transparencia
- El codigo contiene acotacion `[0.90, 1.60]` en algunas rutas derivadas de
  CONAPO. Los factores explicitos y defaults deben inspeccionarse por separado;
  no asumir que toda entrada pasa por ese clamp.
- **Regla 2 (Auditoria en Wizard):** El sistema desglosa en la UI del Wizard (Paso 3) la clave municipal (`cve_mun`), nombre del municipio, ano proyectado, poblacion censal 2020, poblacion proyectada CONAPO y el factor resultante.

### Calculo de la Poblacion Economicamente Activa (PEA)
Para cada manzana $i$ del municipio $m$:
$$\text{POBTOT}_{\text{adj}, i} = \text{POBTOT}_i \times \text{growth\_factor}_m$$
$$\text{P15MAS}_{\text{adj}, i} = \text{P\_15YMAS}_i \times \text{growth\_factor}_m$$
$$\text{PEA}_i = \text{P15MAS}_{\text{adj}, i} \times \text{tasa\_pea}_{\text{ENOE}}$$

Esta masa $\text{PEA}_i$ es el presupuesto invariante estricto de la simulacion ($\Delta = 0$ personas).

---

## 3. Georreferenciacion en Cascada Cuadruple y Cero Imputacion

El archivo tabular `RESAGEBURB` contiene estadisticas demograficas por manzana pero carece de coordenadas en su CSV. El motor aplica una resolucion jerarquica en cascada:

```text
[Nivel 0: Marco Geoestadistico Nacional (MGM)]
   Centroide poligonal vectorial oficial de Manzana (CVE_ENT + CVE_MUN + CVE_AGEB + CVE_MZA)
          | (si no se dispone de capa vectorial de manzana)
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
   El registro censal se purga de la memoria con dropna(). Cero imputacion artificial.
```

### Regla de Oro: CERO Imputacion al Centro de BBOX (Regla 2)
Queda estrictamente prohibido imputar `(mid_lon, mid_lat)` a registros sin coordenadas. En modelos tradicionales, esta mala practica genera "megapuntos" irreales de 100,000+ habitantes en medio de lagunas, selvas o aeropuertos. Todo registro fuera del BBOX o sin geometria se descarta formalmente.

---

## 4. Calibracion Asimetrica de Empleo Municipal

El DENUE clasifica los establecimientos en estratos de personal ocupado (`per_ocu`). Para cada establecimiento se asigna la media geometrica del intervalo:
- `0 a 5 personas`: 2.24
- `6 a 10 personas`: 7.75
- `11 a 30 personas`: 18.17
- `31 a 50 personas`: 39.37
- `51 a 100 personas`: 71.41
- `101 a 250 personas`: 158.90
- `251 y mas personas`: 450.00

### 4.1. Ponderacion Territorial BBOX (`share_bbox`)
Dado que el BBOX de estudio rara vez abarca la totalidad del municipio politico:
$$\text{share\_bbox}_m = \min\left(1.0, \ \frac{\sum_{j \in \text{BBOX} \cap m} E_{\text{formal}, j}}{\sum_{j \in m, \text{global}} E_{\text{formal}, j}}\right)$$
$$H001A_{\text{BBOX}, m} = H001A_{\text{Mun}, m} \times \text{share\_bbox}_m$$
Esto evita concentrar el empleo de las zonas rurales exteriores dentro de la mancha urbana.

### 4.2. Algoritmo de Expansion Asimetrica Acotada
Para corregir el subregistro de micronegocios informales sin alterar las grandes empresas ya verificadas:
1. **Grandes Empresas (> 50 empleados):** Factor neutral: $1.000\times$.
2. **Micro y Pequenas Empresas ($\le 50$ empleados):** Absorben el diferencial para alcanzar $H001A_{\text{BBOX}, m}$:
   $$\text{factor\_micro}_m = \frac{H001A_{\text{BBOX}, m} - E_{\text{grandes}, m}}{E_{\text{micro\_base}, m}}$$
3. **Techo Teorico por Informalidad ($TIL_1$):**
   $$\text{techo\_teorico}_m = \frac{1}{\max(0.01, \ 1 - TIL_1)}$$
   $$\text{factor\_clamped}_m = \text{clamp}(\text{factor\_micro}_m, \ 1.0, \ \text{techo\_teorico}_m)$$

---

## 5. Zonas Metropolitanas Interestatales Multi-Archivo (Regla 7)

Para conurbaciones que abarcan multiples entidades federativas (ej. ZMVM con CDMX, Edomex e Hidalgo; La Laguna con Coahuila y Durango; Puebla-Tlaxcala; Puerto Vallarta-Bahia de Banderas):
- El motor acepta multiples archivos `*RESAGEBURB*.csv` y `*denue*.csv` en la carpeta `data/<ciudad>/`.
- Inspeccionar lectura por archivo/chunks y concatenacion en `load_denue` y
  `load_censo_manzanas`; no asumir una estrategia universal de memoria.
- Aplica un recorte espacial estricto por BBOX metropolitano para eliminar manzanas externas sin duplicar masa ni demanda.
