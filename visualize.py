import sys
import os
import json
import argparse

def generate_html_viewer(demand_json_path, config_json_path, output_html="preview.html"):
    if not os.path.exists(demand_json_path):
        raise FileNotFoundError(f"No se encontro {demand_json_path}")

    with open(demand_json_path, "r", encoding="utf-8") as f:
        demand = json.load(f)

    cfg = {}
    if os.path.exists(config_json_path):
        with open(config_json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    city_name = cfg.get("name", "Mapa de Demanda")
    city_code = cfg.get("code", "DEMO")
    view_state = cfg.get("initialViewState", {})
    center_lat = view_state.get("latitude", 21.15)
    center_lon = view_state.get("longitude", -86.85)
    zoom = view_state.get("zoom", 11.5)

    points = demand.get("points", [])
    pops = demand.get("pops", [])
    total_pax = sum(p.get("size", 0) for p in pops)
    total_residents = sum(p.get("residents", 0) for p in points)
    total_jobs = sum(p.get("jobs", 0) for p in points)

    # Convert to JSON strings for embedding in HTML
    points_json = json.dumps(points)
    pops_json = json.dumps(pops[:2000])  # Top 2000 flow lines for fast rendering

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Subway Builder México - Visor de Demanda ({city_code})</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body, html {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        #map {{ height: 100%; width: 100%; background: #111; }}
        .hud-panel {{
            position: absolute; top: 15px; left: 15px; z-index: 1000;
            background: rgba(15, 23, 42, 0.88); backdrop-filter: blur(8px);
            color: #f8fafc; padding: 18px 22px; border-radius: 12px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.1); max-width: 320px;
        }}
        .hud-title {{ font-size: 18px; font-weight: 700; color: #38bdf8; margin-bottom: 4px; }}
        .hud-subtitle {{ font-size: 12px; color: #94a3b8; margin-bottom: 12px; }}
        .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; }}
        .stat-box {{ background: rgba(30, 41, 59, 0.7); padding: 8px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.05); }}
        .stat-num {{ font-size: 15px; font-weight: 700; color: #4ade80; }}
        .legend {{ margin-top: 14px; font-size: 11px; color: #cbd5e1; line-height: 1.6; }}
        .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="hud-panel">
        <div class="hud-title">{city_name} ({city_code})</div>
        <div class="hud-subtitle">Subway Builder México v6.0 - Visor de Interacción</div>
        <div class="stat-grid">
            <div class="stat-box"><div>Población</div><div class="stat-num">{total_residents:,}</div></div>
            <div class="stat-box"><div>Empleos</div><div class="stat-num">{total_jobs:,}</div></div>
            <div class="stat-box"><div>Nodos</div><div class="stat-num">{len(points):,}</div></div>
            <div class="stat-box"><div>Viajeros Activos</div><div class="stat-num">{total_pax:,}</div></div>
        </div>
        <div class="legend">
            <div><span class="dot" style="background:#3b82f6;"></span> Nodos de Origen (Residencial)</div>
            <div><span class="dot" style="background:#ef4444;"></span> Nodos de Destino (Empleo)</div>
            <div><span class="dot" style="background:#a855f7;"></span> POIs Especiales (Hubs / Aeropuertos)</div>
            <div><span class="dot" style="background:#eab308; opacity: 0.5;"></span> Líneas de Deseo (Flujos O-D)</div>
        </div>
    </div>

    <script>
        const map = L.map('map', {{
            center: [{center_lat}, {center_lon}],
            zoom: {zoom}
        }});

        // Dark tile layer
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; OpenStreetMap, &copy; CARTO',
            maxZoom: 19
        }}).addTo(map);

        const points = {points_json};
        const pops = {pops_json};
        const pointMap = new Map();

        // Render Demand Points
        points.forEach(p => {{
            pointMap.set(p.id, p);
            const isSpecial = p.id.startsWith("AIR_") || p.id.startsWith("UNI_") || p.id.startsWith("Plaza_") || p.id.startsWith("Zona_");
            let color = "#3b82f6";
            if (isSpecial) color = "#a855f7";
            else if (p.jobs > p.residents) color = "#ef4444";

            const radius = Math.max(4, Math.min(18, Math.sqrt(p.residents + p.jobs) * 0.4));

            const circle = L.circleMarker([p.location[1], p.location[0]], {{
                radius: radius,
                fillColor: color,
                color: "#ffffff",
                weight: 1,
                opacity: 0.8,
                fillOpacity: 0.7
            }}).addTo(map);

            circle.bindPopup(`
                <b>Nodo:</b> ${{p.id}}<br>
                <b>Habitantes:</b> ${{p.residents.toLocaleString()}}<br>
                <b>Empleos:</b> ${{p.jobs.toLocaleString()}}<br>
                <b>Cohortes O-D:</b> ${{p.popIds.length}}
            `);
        }});

        // Render Sample Flow Lines (Desire Lines)
        pops.slice(0, 800).forEach(pop => {{
            const orig = pointMap.get(pop.residenceId);
            const dest = pointMap.get(pop.jobId);
            if (orig && dest) {{
                L.polyline([
                    [orig.location[1], orig.location[0]],
                    [dest.location[1], dest.location[0]]
                ], {{
                    color: '#eab308',
                    weight: Math.max(1, Math.min(4, pop.size / 30)),
                    opacity: 0.25
                }}).addTo(map);
            }}
        }});
    </script>
</body>
</html>
"""
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Visor interactivo generado con exito en: {output_html}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generador de Visor HTML Interactivo de Demanda")
    parser.add_argument("--demand", default="demand_data.json", help="Ruta a demand_data.json")
    parser.add_argument("--config", default="config.json", help="Ruta a config.json")
    parser.add_argument("--output", default="preview.html", help="Archivo HTML de salida")
    args = parser.parse_args()
    generate_html_viewer(args.demand, args.config, args.output)
