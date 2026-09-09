"""
tools.patch_depot_wsl
=====================
Aplica los parches canonicos a depot.maps en el entorno WSL Ubuntu:
1. Etiquetado dual explicito:
   - type: 'college', kind: 'college' para instituciones educativas.
   - type: 'commercial', kind: final_kind para comercios.
2. Regla canonica 'Campus Wins' (disyuntividad geometrica estricta):
   - Generacion de college_mask.
   - Sustraccion de college_mask sobre poligonos comerciales superpuestos.
3. Desbloqueo de CPU para calculo de oceanos (100% nucleos asignados).
4. Optimizacion de zoom en mascara de agua (z14 en vez de z15 para decodificacion de teselas).
"""

import os
import sys


def patch_campus_wins(content: str) -> tuple[str, bool]:
    """Aplica el parche Campus Wins de disyuntividad comercial/educativa."""
    if "college_mask" in content and "Campus Wins" in content:
        print("[OK] depot.maps ya cuenta con el parche canonico 'Campus Wins'.")
        return content, False

    modified = False

    # 1. Parche de etiquetado
    old_tagging = """                if final_kind != "college":
                    props = {'kind': final_kind, 'sort_rank': final_rank}
                else:
                    props = {'type': final_kind, 'sort_rank': final_rank}"""

    new_tagging = """                if final_kind == "college":
                    props = {'type': 'college', 'kind': 'college', 'sort_rank': final_rank}
                elif dest == "commercial":
                    props = {'type': 'commercial', 'kind': final_kind, 'sort_rank': final_rank}
                else:
                    props = {'kind': final_kind, 'sort_rank': final_rank}"""

    if old_tagging in content:
        content = content.replace(old_tagging, new_tagging, 1)
        modified = True

    # 2. Parche de mascara college
    old_mask = """        # Build commercial mask
        commercial_mask = None
        if "commercial" in new_layers_data:
            commercial_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("kind") == "commercial":
                    a_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not a_geom.is_empty:
                        if not a_geom.is_valid:
                            a_geom = a_geom.buffer(0)
                        commercial_geoms.append(a_geom)
            if commercial_geoms:
                commercial_mask = unary_union(commercial_geoms)"""

    new_mask = """        # Build commercial mask
        commercial_mask = None
        if "commercial" in new_layers_data:
            commercial_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("kind") in ["commercial", "retail"] or feat["properties"].get("type") == "commercial":
                    a_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not a_geom.is_empty:
                        if not a_geom.is_valid:
                            a_geom = a_geom.buffer(0)
                        commercial_geoms.append(a_geom)
            if commercial_geoms:
                commercial_mask = unary_union(commercial_geoms)

        # Build college mask for "Campus Wins" disjointness
        college_mask = None
        if "commercial" in new_layers_data:
            college_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("type") == "college" or feat["properties"].get("kind") == "college":
                    c_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not c_geom.is_empty:
                        if not c_geom.is_valid:
                            c_geom = c_geom.buffer(0)
                        college_geoms.append(c_geom)
            if college_geoms:
                college_mask = unary_union(college_geoms)"""

    if old_mask in content:
        content = content.replace(old_mask, new_mask, 1)
        modified = True

    # 3. Parche de sustraccion comercial (Campus Wins)
    old_subtraction = """        # Subtract water and aerodrome from commercial
        if "commercial" in new_layers_data:
            kept = []
            for feat in new_layers_data["commercial"]:
                kind = feat["properties"].get("kind")
                
                if kind not in ["commercial"]:
                    kept.append(feat)
                    continue
                
                geom = shape(feat["geometry"])
                geom = geom.intersection(tile_bounds)
                if geom.is_empty:
                    continue
                if not geom.is_valid:
                    geom = geom.buffer(0)
                
                # Subtract water from commercial
                if merged_result is not None and not merged_result.is_empty:
                    if geom.intersects(merged_result):
                        geom = geom.difference(merged_result)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Subtract aerodromes from commercial
                if kind == "commercial" and aerodrome_mask is not None and not aerodrome_mask.is_empty:
                    if geom.intersects(aerodrome_mask):
                        geom = geom.difference(aerodrome_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Final geometry verification & cleaning
                if geom.is_empty or geom.area < 1.0:
                    continue  

                if geom.geom_type not in ("Polygon", "MultiPolygon"):
                    parts = [g for g in geom.geoms 
                             if g.geom_type in ("Polygon", "MultiPolygon")]
                    if not parts:
                        continue
                    geom = unary_union(parts) if len(parts) > 1 else parts[0]
                feat["geometry"] = mapping(geom)
                kept.append(feat)
            new_layers_data["commercial"] = kept"""

    new_subtraction = """        # Subtract water, aerodrome and college from commercial ("Campus Wins")
        if "commercial" in new_layers_data:
            kept = []
            for feat in new_layers_data["commercial"]:
                is_college = feat["properties"].get("type") == "college" or feat["properties"].get("kind") == "college"
                
                geom = shape(feat["geometry"])
                geom = geom.intersection(tile_bounds)
                if geom.is_empty:
                    continue
                if not geom.is_valid:
                    geom = geom.buffer(0)
                
                # Subtract water from commercial and college
                if merged_result is not None and not merged_result.is_empty:
                    if geom.intersects(merged_result):
                        geom = geom.difference(merged_result)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                if is_college:
                    # College keeps its geometry intact (Campus Wins)
                    if geom.is_empty or geom.area < 1.0:
                        continue
                    if geom.geom_type not in ("Polygon", "MultiPolygon"):
                        parts = [g for g in geom.geoms 
                                 if g.geom_type in ("Polygon", "MultiPolygon")]
                        if not parts:
                            continue
                        geom = unary_union(parts) if len(parts) > 1 else parts[0]
                    feat["geometry"] = mapping(geom)
                    kept.append(feat)
                    continue

                # Commercial features: subtract aerodromes and college
                if aerodrome_mask is not None and not aerodrome_mask.is_empty:
                    if geom.intersects(aerodrome_mask):
                        geom = geom.difference(aerodrome_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Subtract college from commercial ("Campus Wins")
                if college_mask is not None and not college_mask.is_empty:
                    if geom.intersects(college_mask):
                        geom = geom.difference(college_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Final geometry verification & cleaning
                if geom.is_empty or geom.area < 1.0:
                    continue  

                if geom.geom_type not in ("Polygon", "MultiPolygon"):
                    parts = [g for g in geom.geoms 
                             if g.geom_type in ("Polygon", "MultiPolygon")]
                    if not parts:
                        continue
                    geom = unary_union(parts) if len(parts) > 1 else parts[0]
                feat["geometry"] = mapping(geom)
                kept.append(feat)
            new_layers_data["commercial"] = kept"""

    if old_subtraction in content:
        content = content.replace(old_subtraction, new_subtraction, 1)
        modified = True

    if modified:
        print("[OK] Parche 'Campus Wins' incorporado.")
    return content, modified


def patch_ocean_cpu(content: str) -> tuple[str, bool]:
    """Desbloquea el 100% de los nucleos asignados para el calculo paralelo de oceano."""
    if "Subway Builder Mexico: use all allocated cores" in content:
        print("[OK] depot.maps ya cuenta con el parche de desbloqueo de CPU para oceano.")
        return content, False

    old_cpu = """        # Select optimal number of parallel workers
        # Never use more than 1/2 the available cores
        ncores = max(1, min(self.ncores, os.cpu_count() // 2))"""

    new_cpu = """        # Select optimal number of parallel workers
        # Subway Builder Mexico: use all allocated cores (unthrottled)
        ncores = max(1, self.ncores)"""

    if old_cpu not in content:
        print("[WARN] No se encontro el bloque de throttling de CPU en depot/maps.py.")
        return content, False

    content = content.replace(old_cpu, new_cpu, 1)
    print("[OK] Parche de desbloqueo de CPU para oceano incorporado.")
    return content, True


def patch_ocean_water_zoom(content: str) -> tuple[str, bool]:
    """Optimiza el zoom de decodificacion de teselas de agua a z14 en vez de z15."""
    if "Subway Builder Mexico: optimized water_zoom" in content:
        print("[OK] depot.maps ya cuenta con el parche de optimizacion de zoom de agua (z14).")
        return content, False

    old_zoom_block = """        if self.verb:
            print(f"  Decoding water & ocean polygons directly from {self.raw_mbtiles} at zoom {self.maxzoom}")
            
        water_polygons = []
        # Target high resolution Zoom level `self.maxzoom`
        tiles = list(mercantile.tiles(self.bbox[0], self.bbox[1], 
                                      self.bbox[2], self.bbox[3], 
                                      self.maxzoom))
        
        conn = sqlite3.connect(self.raw_mbtiles)
        cursor = conn.cursor()
        
        for t in tiles:
            tms_y = (1 << self.maxzoom) - 1 - t.y # Invert Y for standard TMS lookup scheme
            cursor.execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level={self.maxzoom} AND tile_column=? AND tile_row=?",
                (t.x, tms_y)
            )"""

    new_zoom_block = """        # Subway Builder Mexico: optimized water_zoom (z14 vs z15 reduces tile decode burden by 4x)
        water_zoom = min(14, self.maxzoom)
        if self.verb:
            print(f"  Decoding water & ocean polygons directly from {self.raw_mbtiles} at zoom {water_zoom}")
            
        water_polygons = []
        # Target high resolution Zoom level `water_zoom`
        tiles = list(mercantile.tiles(self.bbox[0], self.bbox[1], 
                                      self.bbox[2], self.bbox[3], 
                                      water_zoom))
        
        conn = sqlite3.connect(self.raw_mbtiles)
        cursor = conn.cursor()
        
        for t in tiles:
            tms_y = (1 << water_zoom) - 1 - t.y # Invert Y for standard TMS lookup scheme
            cursor.execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level={water_zoom} AND tile_column=? AND tile_row=?",
                (t.x, tms_y)
            )"""

    old_buffer_line = """                        if geom_shape.geom_type in ["LineString", "MultiLineString"]:
                            safe_buffer = self._calculate_buffer(self.maxzoom)"""

    new_buffer_line = """                        if geom_shape.geom_type in ["LineString", "MultiLineString"]:
                            safe_buffer = self._calculate_buffer(water_zoom)"""

    if old_zoom_block not in content:
        print("[WARN] No se encontro el bloque de decodificacion de teselas a zoom {self.maxzoom} en depot/maps.py.")
        return content, False

    content = content.replace(old_zoom_block, new_zoom_block, 1)

    if old_buffer_line in content:
        content = content.replace(old_buffer_line, new_buffer_line, 1)

    print("[OK] Parche de optimizacion de zoom de agua (z14) incorporado.")
    return content, True


def patch_depot_maps(file_path: str = None) -> bool:
    if file_path is None:
        try:
            import depot.maps
            file_path = depot.maps.__file__
        except ImportError:
            print("[WARN] depot.maps no esta instalado en este entorno.")
            return False

    if not os.path.exists(file_path):
        print(f"[WARN] Archivo no encontrado: {file_path}")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    any_modified = False

    # 1. Parche Campus Wins
    content, mod_cw = patch_campus_wins(content)
    any_modified = any_modified or mod_cw

    # 2. Parche Desbloqueo CPU Oceano
    content, mod_cpu = patch_ocean_cpu(content)
    any_modified = any_modified or mod_cpu

    # 3. Parche Zoom Mascara Agua
    content, mod_zoom = patch_ocean_water_zoom(content)
    any_modified = any_modified or mod_zoom

    if not any_modified:
        print("[OK] Todos los parches de depot.maps ya estan aplicados.")
        return True

    # Crear backup antes de escribir
    backup_path = file_path + ".bak"
    if not os.path.exists(backup_path):
        with open(backup_path, "w", encoding="utf-8") as bf:
            with open(file_path, "r", encoding="utf-8") as orig:
                bf.write(orig.read())

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] depot.maps actualizado y guardado exitosamente en {file_path}")
    return True


if __name__ == "__main__":
    patch_depot_maps()
