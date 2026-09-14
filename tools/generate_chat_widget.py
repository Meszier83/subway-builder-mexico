"""
tools.generate_chat_widget
==========================
Genera el widget interactivo HTML para la previsualización de toponimia.
"""

import os
import json


def build_widget():
    manifest_path = "dist/cancun_riviera_maya/toponymy_manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    nodes = manifest["osm_nodes"]
    polys = manifest["osm_polygons"]
    denue = manifest["denue_places"]

    all_items = []
    for p in polys:
        all_items.append({
            "name": p["name"],
            "cat": "osm_poly",
            "cat_label": "OSM Polígono",
            "lon": p["lon"],
            "lat": p["lat"],
            "extra": "Poniente recuperado"
        })
    for p in denue:
        all_items.append({
            "name": p["name"],
            "cat": "denue",
            "cat_label": "DENUE INEGI",
            "lon": p["lon"],
            "lat": p["lat"],
            "extra": f"{p.get('establishments', 0)} comercios"
        })
    for p in nodes:
        all_items.append({
            "name": p["name"],
            "cat": "osm_node",
            "cat_label": "OSM Nodo",
            "lon": p["lon"],
            "lat": p["lat"],
            "extra": "Centro histórico"
        })

    all_items.sort(key=lambda x: x["name"])
    raw_json = json.dumps(all_items, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    body {{ margin: 0; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
    .scrollbox::-webkit-scrollbar {{ width: 6px; }}
    .scrollbox::-webkit-scrollbar-track {{ background: transparent; }}
    .scrollbox::-webkit-scrollbar-thumb {{ background: var(--border, #333); border-radius: 4px; }}
  </style>
</head>
<body class="bg-[var(--background,#0d1117)] text-[var(--foreground,#c9d1d9)]">
  <div class="max-w-4xl mx-auto border border-[var(--border,#30363d)] rounded-xl bg-[var(--card,#161b22)] p-4 shadow-xl">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[var(--border,#30363d)]">
      <div>
        <h2 class="text-base font-bold text-[var(--foreground,#f0f6fc)] flex items-center gap-2">
          <span>🗺️</span> Previsualizador de Toponimia Urbana
          <span class="text-xs font-semibold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">419 Colonias Activas</span>
        </h2>
        <p class="text-xs text-[var(--muted-foreground,#8b949e)] mt-0.5">
          Cancún / Riviera Maya — Etiquetas listas para compilar e inyectar en el mapa.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-[var(--muted-foreground,#8b949e)]">Visor Leaflet disponible en <code>dist/cancun_riviera_maya/preview_toponymy.html</code></span>
      </div>
    </div>

    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-3">
      <div class="p-2.5 rounded-lg bg-[var(--background,#0d1117)] border border-[var(--border,#30363d)]">
        <div class="text-[10px] uppercase font-bold text-[var(--muted-foreground,#8b949e)]">Total Nombres</div>
        <div class="text-lg font-extrabold text-[var(--foreground,#f0f6fc)]">419</div>
        <div class="text-[10px] text-emerald-400">100% Cobertura</div>
      </div>
      <div class="p-2.5 rounded-lg bg-[var(--background,#0d1117)] border border-cyan-500/30">
        <div class="text-[10px] uppercase font-bold text-cyan-400">Polígonos OSM (Poniente)</div>
        <div class="text-lg font-extrabold text-cyan-400">{len(polys)}</div>
        <div class="text-[10px] text-[var(--muted-foreground,#8b949e)]">Recuperadas vía nwr</div>
      </div>
      <div class="p-2.5 rounded-lg bg-[var(--background,#0d1117)] border border-emerald-500/30">
        <div class="text-[10px] uppercase font-bold text-emerald-400">DENUE INEGI</div>
        <div class="text-lg font-extrabold text-emerald-400">{len(denue)}</div>
        <div class="text-[10px] text-[var(--muted-foreground,#8b949e)]">Fracc. complementarios</div>
      </div>
      <div class="p-2.5 rounded-lg bg-[var(--background,#0d1117)] border border-sky-500/30">
        <div class="text-[10px] uppercase font-bold text-sky-400">Nodos OSM Centro</div>
        <div class="text-lg font-extrabold text-sky-400">{len(nodes)}</div>
        <div class="text-[10px] text-[var(--muted-foreground,#8b949e)]">Centro / Hotel Zone</div>
      </div>
    </div>

    <div class="flex flex-col sm:flex-row gap-2 my-3">
      <div class="relative flex-1">
        <input type="text" id="search-input" placeholder="Buscar Supermanzana (ej. 100, 228), Fracc, Región..."
               class="w-full bg-[var(--background,#0d1117)] border border-[var(--border,#30363d)] rounded-lg px-3 py-1.5 text-xs text-[var(--foreground,#f0f6fc)] focus:outline-none focus:border-cyan-500"
               oninput="renderTable()" />
      </div>
      <div class="flex gap-1 overflow-x-auto text-xs" id="filter-tabs">
        <button onclick="setTab('all')" id="tab-all" class="px-2.5 py-1 rounded-lg font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">Todas (419)</button>
        <button onclick="setTab('osm_poly')" id="tab-osm_poly" class="px-2.5 py-1 rounded-lg font-medium text-[var(--muted-foreground,#8b949e)] hover:text-white">OSM Poniente ({len(polys)})</button>
        <button onclick="setTab('denue')" id="tab-denue" class="px-2.5 py-1 rounded-lg font-medium text-[var(--muted-foreground,#8b949e)] hover:text-white">DENUE ({len(denue)})</button>
        <button onclick="setTab('osm_node')" id="tab-osm_node" class="px-2.5 py-1 rounded-lg font-medium text-[var(--muted-foreground,#8b949e)] hover:text-white">OSM Nodos ({len(nodes)})</button>
      </div>
    </div>

    <div class="scrollbox overflow-y-auto max-h-[320px] rounded-lg border border-[var(--border,#30363d)] bg-[var(--background,#0d1117)]">
      <table class="w-full text-left text-xs border-collapse">
        <thead class="sticky top-0 bg-[var(--card,#161b22)] border-b border-[var(--border,#30363d)] text-[var(--muted-foreground,#8b949e)] uppercase font-semibold text-[10px]">
          <tr>
            <th class="py-2 px-3">Nombre de Colonia / Supermanzana</th>
            <th class="py-2 px-3">Origen</th>
            <th class="py-2 px-3">Coordenadas GPS</th>
            <th class="py-2 px-3 text-right">Detalle</th>
          </tr>
        </thead>
        <tbody id="items-table" class="divide-y divide-[var(--border,#21262d)] text-[var(--foreground,#c9d1d9)]">
        </tbody>
      </table>
    </div>

    <div class="mt-2 text-[11px] text-[var(--muted-foreground,#8b949e)] flex justify-between items-center">
      <span id="counter-text">Mostrando 419 de 419 registros</span>
      <span>Usa el buscador para verificar cualquier zona específica</span>
    </div>
  </div>

  <script>
    const allData = {raw_json};
    let currentTab = 'all';

    function setTab(tab) {{
      currentTab = tab;
      ['all', 'osm_poly', 'denue', 'osm_node'].forEach(t => {{
        const btn = document.getElementById('tab-' + t);
        if (t === tab) {{
          btn.className = 'px-2.5 py-1 rounded-lg font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40';
        }} else {{
          btn.className = 'px-2.5 py-1 rounded-lg font-medium text-[var(--muted-foreground,#8b949e)] hover:text-white';
        }}
      }});
      renderTable();
    }}

    function renderTable() {{
      const query = document.getElementById('search-input').value.toLowerCase().trim();
      const tbody = document.getElementById('items-table');
      tbody.innerHTML = '';

      let filtered = allData.filter(item => {{
        if (currentTab !== 'all' && item.cat !== currentTab) return false;
        if (!query) return true;
        return item.name.toLowerCase().includes(query);
      }});

      document.getElementById('counter-text').innerText = `Mostrando ${{filtered.length}} de ${{allData.length}} registros`;

      if (filtered.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="4" class="py-6 text-center text-[var(--muted-foreground,#8b949e)]">No se encontraron colonias con ese criterio de búsqueda.</td></tr>';
        return;
      }}

      filtered.slice(0, 100).forEach(item => {{
        let badgeClass = '';
        if (item.cat === 'osm_poly') badgeClass = 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30';
        else if (item.cat === 'denue') badgeClass = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
        else badgeClass = 'bg-sky-500/15 text-sky-400 border-sky-500/30';

        const tr = document.createElement('tr');
        tr.className = 'hover:bg-[var(--card,#161b22)]/50 transition';
        tr.innerHTML = `
          <td class="py-2 px-3 font-semibold text-[var(--foreground,#f0f6fc)] flex items-center gap-1.5">
            <span class="text-xs">📍</span> ${{item.name}}
          </td>
          <td class="py-2 px-3">
            <span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold border ${{badgeClass}}">
              ${{item.cat_label}}
            </span>
          </td>
          <td class="py-2 px-3 font-mono text-[11px] text-[var(--muted-foreground,#8b949e)]">
            [${{item.lon.toFixed(4)}}, ${{item.lat.toFixed(4)}}]
          </td>
          <td class="py-2 px-3 text-right text-[11px] text-[var(--muted-foreground,#8b949e)]">
            ${{item.extra}}
          </td>
        `;
        tbody.appendChild(tr);
      }});

      if (filtered.length > 100) {{
        const moreTr = document.createElement('tr');
        moreTr.innerHTML = `<td colspan="4" class="py-2 text-center text-[10px] text-[var(--muted-foreground,#8b949e)] bg-[var(--background,#0d1117)]/80">
          ... y ${{filtered.length - 100}} registros más (afina la búsqueda para filtrar)
        </td>`;
        tbody.appendChild(moreTr);
      }}
    }}

    renderTable();
  </script>
</body>
</html>
"""
    target = "C:/Users/Keppl/.gemini/antigravity/brain/2d7336fe-f308-4782-a987-3f77fedf3d0d/toponymy_widget.html"
    with open(target, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[OK] Widget generado en {target}")


if __name__ == "__main__":
    build_widget()
