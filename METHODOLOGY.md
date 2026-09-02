# Libro Blanco de Metodología y Fundamentos Matemáticos (v6.0) 📐🚇

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
