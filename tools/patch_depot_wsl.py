"""
tools.patch_depot_wsl
=====================
Aplica el parche canónico a depot.maps en el entorno WSL Ubuntu:
1. Etiquetado dual explícito:
   - type: 'college', kind: 'college' para instituciones educativas.
   - type: 'commercial', kind: final_kind para comercios.
2. Regla canónica 'Campus Wins' (disyuntividad geométrica estricta):
   - Generación de college_mask.
   - Sustracción de college_mask sobre polígonos comerciales superpuestos.
"""

import os
import sys


def patch_depot_maps(file_path: str = None) -> bool:
    if file_path is None:
        try:
            import depot.maps
            file_path = depot.maps.__file__
        except ImportError:
            print("[WARN] depot.maps no está instalado en este entorno.")
            return False

    if not os.path.exists(file_path):
        print(f"[WARN] Archivo no encontrado: {file_path}")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verificar si ya está parcheado
    if "college_mask" in content and "Campus Wins" in content:
        print("[OK] depot.maps ya cuenta con el parche canónico 'Campus Wins'.")
        return True

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

    if old_tagging not in content:
        print("[WARN] No se encontró el bloque de etiquetado exacto en depot/maps.py.")
        return False

    content = content.replace(old_tagging, new_tagging, 1)

    # 2. Parche de máscara college
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

    if old_mask not in content:
        print("[WARN] No se encontró el bloque de commercial_mask en depot/maps.py.")
        return False

    content = content.replace(old_mask, new_mask, 1)

    # 3. Parche de sustracción comercial (Campus Wins)
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

    if old_subtraction not in content:
        print("[WARN] No se encontró el bloque de sustracción en depot/maps.py.")
        return False

    content = content.replace(old_subtraction, new_subtraction, 1)

    # Crear backup antes de escribir
    backup_path = file_path + ".bak"
    if not os.path.exists(backup_path):
        with open(backup_path, "w", encoding="utf-8") as bf:
            with open(file_path, "r", encoding="utf-8") as orig:
                bf.write(orig.read())

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] depot.maps parcheado exitosamente con 'Campus Wins' en {file_path}")
    return True


if __name__ == "__main__":
    patch_depot_maps()
