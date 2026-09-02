# Libro Blanco de Metodología y Fundamentos Matemáticos (v6.0) 📐🚇

## 1. Fundamentación Teórica
A diferencia del estándar estadounidense basado en encuestas de origen-destino (LODES/LEHD), México no cuenta con matrices O-D universales a nivel manzana. sb_mexico implementa un modelo de interacción espacial gravitatoria de **Muestreo Multinomial Puro en Dos Capas**, calibrado con microdatos del INEGI.

---

## 2. El Modelo de Demanda en Dos Capas

### Capa 1: Generadores Especiales con Cuota Exacta (Hubs Metropolitanos)
Para POIs de escala metropolitana (Aeropuertos AIR_, Universidades UNI_, Estadios):
1. **Cuota Objetivo:** $ fijada empíricamente (fórmula AFAC para aeropuertos, matrícula SEP para universidades).
2. **Distribución Espacial:** 
   W_{ik} = 	ext{PEA}_i \cdot e^{-eta_{	ext{esp}} \cdot d_{ik}} \quad (eta_{	ext{esp}} = 0.04)
3. **Asignación Multinomial:**
   T_{i 	o k} \sim 	ext{Multinomial}\left(Q_k, rac{W_{ik}}{\sum_m W_{mk}}ight)
4. **Descuento de Presupuesto:** $	ext{PEA}_i^{	ext{rem}} = 	ext{PEA}_i - \sum_k T_{i 	o k}$.

### Capa 2: Modelo Gravitatorio Multinomial para Empleo Regular (DENUE)
Para la PEA remanente y los destinos comerciales/industriales del DENUE:
A_{ij} = E_j^{0.85} \cdot e^{-eta \cdot d_{ij}} \quad (eta = 0.12, \ d_{ij} \le 55	ext{ km})
P_{ij} = rac{A_{ij}}{\sum_k A_{ik}}
T_{i 	o j} \sim 	ext{Multinomial}\left(	ext{PEA}_i^{	ext{rem}}, P_{i \cdot}ight)

**Aserción de Masa:** $\sum_{i,j} T_{ij} + \sum_{i,k} T_{ik} \equiv \sum_i 	ext{PEA}_i$.

---

## 3. Calibración Asimétrica de Empleo Municipal

El DENUE subestima el empleo informal de micro-negocios pero mide con precisión a las grandes empresas. El motor aplica un factor de expansión acotado:

	ext{Factor Micro} = 	ext{clamp}\left(rac{H001A - 	ext{Empleo Grande}}{	ext{Empleo Micro Base}}, \ 1.0, \ rac{1}{1 - TIL_1}ight)

* Grandes empresas ($>50$ trabajadores): Multiplicador fijo .000$ (sin inflar).
* Micro y pequeñas empresas: Multiplicador ajustado para satisfacer el control $ del Censo Económico 2024.

---

## 4. Física de Tráfico y Congestión Urbana

Los tiempos de manejo (drivingSeconds) en el juego determinan la elección modal:

	au(d) = 1.25 + 0.15 \cdot e^{-d / 10.0} \quad (	ext{Tortuosidad})
V(d) = 18.0 + (65.0 - 18.0) \cdot \left(1 - e^{-d / 8.0}ight) \quad (	ext{Velocidad en km/h})
	ext{drivingDistance} = \max(800, \ 	ext{int}(d 	imes 1000 	imes 	au))
	ext{drivingSeconds} = \max\left(180, \ 	ext{int}\left(rac{	ext{drivingDistance}}{V(d) / 3.6}ight)ight)

---

## 5. Resumen de Estándares de Calidad S-Tier

| Componente | Implementación | Garantía |
| :--- | :--- | :--- |
| **Conservación de Masa** | Asignación Multinomial Estricta | $\Delta = 0$ personas |
| **POIs Especiales** | Cuota Objetivo Pura (Capa 1) | 100% de la cuota real en el tooltip |
| **Esquema JSON** | Canónico de Subway Builder | 5 llaves en points, 6 en pops |
| **Cámara Viewport** | Baricentro de Masa $\sum x_i(R_i + 1.5J_i)$ | Centrado sobre el núcleo metropolitano |
