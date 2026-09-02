import json
import numpy as np
from sb_mexico.gravity import simulate_gravity_demand, sanitize_demand_points
from visualize import generate_html_viewer

np.random.seed(42)

points = []
# 1. Manzanas residenciales en Cancun Urbano (Region 90-200, Villas del Mar, Prado Norte)
for i in range(150):
    lon = np.random.normal(-86.86, 0.035)
    lat = np.random.normal(21.16, 0.030)
    pob = int(np.random.gamma(shape=5, scale=90))
    pea = int(pob * 0.6644)
    jobs = int(np.random.gamma(shape=2, scale=15))
    points.append({
        "id": f"dp_{i+1:04d}",
        "location": [round(lon, 5), round(lat, 5)],
        "residents": pob,
        "jobs": jobs,
        "pea_15ymas": pea,
        "popIds": []
    })

# 2. POIs de Cancún declarados en cancun.yaml
pois = [
    {"id": "AIR_Aeropuerto_CUN", "location": [-86.874, 21.036], "jobs": 15000, "residents": 0, "pea_15ymas": 0, "is_special": True, "popIds": []},
    {"id": "Zona_Hotelera_Punta_Cancun", "location": [-86.747, 21.137], "jobs": 12000, "residents": 1200, "pea_15ymas": 600, "is_special": True, "popIds": []},
    {"id": "Zona_Hotelera_Punta_Nizuc", "location": [-86.784, 21.042], "jobs": 8000, "residents": 500, "pea_15ymas": 250, "is_special": True, "popIds": []},
    {"id": "Plaza_Las_Americas", "location": [-86.824, 21.147], "jobs": 5500, "residents": 200, "pea_15ymas": 100, "is_special": True, "popIds": []},
    {"id": "UNI_Universidad_Caribe", "location": [-86.851, 21.198], "jobs": 4500, "residents": 0, "pea_15ymas": 0, "is_special": True, "popIds": []},
    {"id": "UNI_Tec_Cancun", "location": [-86.848, 21.144], "jobs": 3500, "residents": 0, "pea_15ymas": 0, "is_special": True, "popIds": []}
]

points.extend(pois)

total_pea = sum(p.get("pea_15ymas", 0) for p in points)
pops = simulate_gravity_demand(demand_points=points, beta=0.12, max_pop_size=150, seed=42)

clean_points = sanitize_demand_points(points)

with open("demand_data.json", "w", encoding="utf-8") as f:
    json.dump({"points": clean_points, "pops": pops}, f)

config_data = {
    "name": "Cancun y Riviera Norte",
    "code": "CUN",
    "description": "Zona Metropolitana de Cancun, Isla Mujeres y Corredor Turistico",
    "population": sum(p["size"] for p in pops),
    "initialViewState": {
        "zoom": 11.5,
        "latitude": 21.145,
        "longitude": -86.835,
        "pitch": 0,
        "bearing": 0
    },
    "creator": "Keppler",
    "version": "6.0.0"
}

with open("config.json", "w", encoding="utf-8") as f:
    json.dump(config_data, f, indent=2)

generate_html_viewer("demand_data.json", "config.json", "preview_cancun.html")
