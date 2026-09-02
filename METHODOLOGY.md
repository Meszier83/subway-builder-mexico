# Libro Blanco de Metodología y Fundamentos Matemáticos (v6.1) 📐🚇

## 1. Fundamentación Teórica
A diferencia del estándar estadounidense basado en encuestas de origen-destino (LODES/LEHD), México no cuenta con matrices O-D universales a nivel manzana. sb_mexico implementa un modelo de interacción espacial gravitatoria de **Muestreo Multinomial Puro en Dos Capas**, calibrado con microdatos del INEGI.

---

## 2. El Modelo de Demanda en Dos Capas

### Capa 1: Generadores Especiales con Cuota Exacta (Hubs Metropolitanos)
Para POIs de escala metropolitana (Aeropuertos AIR_, Universidades UNI_, Estadios):
1. **Cuota Objetivo:** $ fijada empíricamente (fórmula AFAC para aeropuertos, matrícula SEP para universidades).
2. **Distribución Espacial:** 
   W_{ik} = \text{PEA}_i \cdot e^{-\beta_{\text{esp}} \cdot d_{ik}} \quad (\beta_{\text{esp}} = 0.04)
3. **Asignación Multinomial:**
   T_{i \to k} \sim \text{Multinomial}\left(Q_k, \frac{W_{ik}}{\sum_m W_{mk}}\right)
4. **Descuento de Presupuesto:** $\text{PEA}_i^{\text{rem}} = \text{PEA}_i - \sum_k T_{i \to k}$.

### Capa 2: Modelo Gravitatorio Multinomial para Empleo Regular (DENUE)
Para la PEA remanente y los destinos comerciales/industriales del DENUE:
A_{ij} = E_j^{0.85} \cdot e^{-\beta \cdot d_{ij}} \quad (\beta = 0.12, \ d_{ij} \le 55\text{ km})
P_{ij} = \frac{A_{ij}}{\sum_k A_{ik}}
T_{i \to j} \sim \text{Multinomial}\left(\text{PEA}_i^{\text{rem}}, P_{i \cdot}\right)

**Aserción de Masa:** $\sum_{i,j} T_{ij} + \sum_{i,k} T_{ik} \equiv \sum_i \text{PEA}_i$.

---

## 3. Calibración Asimétrica de Empleo Municipal

El DENUE subestima el empleo informal de micro-negocios pero mide con precisión a las grandes empresas. El motor aplica un factor de expansión acotado:

\text{Factor Micro} = \text{clamp}\left(\frac{H001A - \text{Empleo Grande}}{\text{Empleo Micro Base}}, \ 1.0, \ \frac{1}{1 - TIL_1}\right)

* Grandes empresas (>50 trabajadores): Multiplicador fijo 1.000 (sin inflar).
* Micro y pequeñas empresas: Multiplicador ajustado para satisfacer el control $ del Censo Económico 2024.

---

## 4. Física de Tráfico y Congestión Urbana

Los tiempos de manejo (drivingSeconds) en el juego determinan la elección modal:

\tau(d) = 1.25 + 0.15 \cdot e^{-d / 10.0} \quad (\text{Tortuosidad})
V(d) = 18.0 + (65.0 - 18.0) \cdot (1 - e^{-d / 8.0}) \quad (\text{Velocidad en km/h})
\text{drivingDistance} = \max(800, \ \text{int}(d \times 1000 \times \tau))
\text{drivingSeconds} = \max\left(180, \ \text{int}\left(\frac{\text{drivingDistance}}{V(d) / 3.6}\right)\right)

---

## 5. Resumen de Estándares de Calidad S-Tier

| Componente | Implementación | Garantía |
| :--- | :--- | :--- |
| **Conservación de Masa** | Asignación Multinomial Estricta | $\Delta = 0$ personas |
| **POIs Especiales** | Cuota Objetivo Pura (Capa 1) | 100% de la cuota real en el tooltip |
| **Esquema JSON** | Canónico de Subway Builder | 5 llaves en points, 6 en pops |
| **Cámara Viewport** | Baricentro de Masa | Centrado sobre el núcleo metropolitano |

---

## 6. Arquitectura de POIs (Special Demand) vs Clusters de Empleo 🏢✈️

### 6.1. ¿Por qué existen los POIs / Generadores Especiales?
Los censos económicos tradicionales registran el empleo formal corporativo pero presentan dos limitaciones:
1. **Flujos No Asalariados:** Un Aeropuerto (`AIR_`) o Universidad (`UNI_`) moviliza a decenas de miles de pasajeros y estudiantes diarios que no son empleados de nómina pero se comportan como usuarios del transporte masivo.
2. **Escala de Cuenca Metropolitana:** El comercio general tiene una fricción espacial alta ($\beta = 0.12$), mientras que los grandes hubs metropolitanos atraen viajes desde toda la ciudad ($\beta_{\text{esp}} = 0.03 - 0.05$).

### 6.2. Semántica del Motor de Subway Builder
* **Demand Points (`points`):** Son entidades visuales. Sus valores de `jobs` y `residents` únicamente definen el tamaño de la burbuja y los paneles informativos. **No crean pasajeros por sí mismos.**
* **Pops (`pops`):** Son las cohortes de viaje reales (`{residenceId, jobId, size, drivingSeconds}`). Son los únicos entes que suben a los trenes y generan tráfico.

### 6.3. Prefijos Nativos del Juego y Curvas de Horarios (`Dampening`)
El motor de *Subway Builder* inspecciona los prefijos del `jobId`:
* **`AIR_*` (Aeropuertos):** Activa el modo 24/7 con **dampening = 0.5** y flujo bidireccional simétrico (los vuelos operan día y noche).
* **`UNI_*` (Universidades):** Activa el horario estudiantil con **dampening = 0.3** (viajes distribuidos a lo largo del día entre turnos de clase).
* **Sin prefijo (General):** Sigue la curva bimodal de hora pico matutina (7–10 AM) y vespertina (4–7 PM).

### 6.4. Tratamiento de Clusters Comerciales vs Nodos Puntuales
* **Nodos Puntuales Masivos (Aeropuertos, Estadios, Campus Centrales):** Usan un POI individual con radio amplio (`radius_m: 1500–2500m`) y modo `MAX`.
* **Clusters y Corredores Lineales (Zona Hotelera, Paseo de la Reforma, Corredores Industriales):**
  * ❌ **Anti-patrón:** Crear un solo mega-POI concentra decenas de miles de empleos en una sola coordenada, colapsando una estación y dejando el resto del corredor vacío.
  * ✅ **Práctica Correcta:** Dejar que el DENUE distribuya el empleo orgánicamente a lo largo del corredor en múltiples estaciones continuas, o usar POIs de anclaje con radios acotados (`radius_m: 500–800m`).

### 6.5. Tabla de Do's and Don'ts

| Práctica | Recomendación | Motivo Técnico |
| :--- | :--- | :--- |
| **Prefijos `AIR_` y `UNI_`** | **DO (Obligatorio)** | Activa las físicas horarias nativas del juego. |
| **Absorción DENUE (`mode: MAX`)** | **DO** | Evita duplicar el empleo local ya registrado en el censo. |
| **Descuento de Presupuesto** | **DO** | Garantiza conservación estricta de la PEA ($\Delta = 0$). |
| **Mega-POIs en Corredores** | **DON'T** | Destruye la red lineal y crea cuellos de botella irreales. |
| **Micro-POIs para Plazas/Escuelas** | **DON'T** | Sobrecarga el modelo; el DENUE ya los modela con precisión. |

