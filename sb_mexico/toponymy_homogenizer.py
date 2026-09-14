"""
sb_mexico.toponymy_homogenizer
==============================
Motor universal y robusto de análisis taxonómico, homogeneización masiva,
formato de títulos en español (smart title casing), búsqueda y reemplazo regex,
y deduplicación espacial para toponimia urbana mexicana en Subway Builder.
"""

import re
import math
from typing import Dict, List, Tuple, Optional, Any, Set


# Partículas gramaticales del español que deben permanecer en minúscula salvo al inicio
SPANISH_PREPOSITIONS = {
    "de", "del", "al", "en", "y", "e", "a", "o", "u", "por", "con", "sin", "sobre"
}
SPANISH_ARTICLES = {"la", "las", "el", "los"}

# Patrón para números romanos comunes en toponimia (I hasta XL)
ROMAN_NUMERALS_SET = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX",
    "XXXI", "XXXII", "XXXIII", "XXXIV", "XXXV", "XXXVI", "XXXVII", "XXXVIII", "XXXIX", "XL"
}


def smart_title_case(text: str) -> str:
    """
    Convierte una cadena a Capitalización de Título inteligente para el español:
    - Conserva en mayúsculas los números romanos (ej. 'Fase IV', 'Sección II', 'Siglo XXI').
    - Mantiene en minúsculas las preposiciones intermedias ('del Sol', 'de la Selva').
    - Capitaliza artículos independientes ('Las Américas', 'Los Pinos') pero los minúsculiza
      tras preposiciones ('de las Flores', 'en los Sauces').
    - Respeta sufijos alfanuméricos y guiones (ej. '92-A', 'Mz 14', 'Lote 3').
    - Maneja acentos y caracteres especiales del español (Á, É, Í, Ó, Ú, Ñ, Ü).
    """
    if not text or not str(text).strip():
        return ""

    raw = str(text).strip()
    # Si la cadena completa es un número puro o alfanumérico corto (ej. '94', '92A', '92-A')
    if re.match(r"^\d+([-\s]?[A-Za-z])?$", raw):
        return raw.upper()

    tokens = re.split(r"(\s+)", raw)
    processed = []
    prev_lower_word = ""
    word_index = 0

    for token in tokens:
        # Preservar espacios en blanco intactos
        if re.match(r"^\s+$", token):
            processed.append(token)
            continue

        clean_word = token.strip()
        upper_word = clean_word.upper()
        lower_word = clean_word.lower()

        # Abreviaturas reconocidas
        if re.match(r"^U\.?H\.?$", upper_word):
            processed.append("U.H.")
        elif re.match(r"^FRACC\.?$", upper_word):
            processed.append("Fracc.")
        elif re.match(r"^S\.?M\.?$", upper_word):
            processed.append("SM")
        # 1. ¿Es un número romano?
        elif upper_word in ROMAN_NUMERALS_SET and upper_word not in {"A", "D", "I" if word_index > 0 and lower_word in SPANISH_PREPOSITIONS else ""}:
            processed.append(upper_word)
        # 2. ¿Es una preposición intermedia del español?
        elif word_index > 0 and lower_word in SPANISH_PREPOSITIONS:
            processed.append(lower_word)
        # 3. ¿Es un artículo que sigue a una preposición (ej. 'de la', 'de los')?
        elif word_index > 0 and lower_word in SPANISH_ARTICLES and prev_lower_word in {"de", "en", "a", "por", "con", "sin", "sobre"}:
            processed.append(lower_word)
        # 4. ¿Es un código alfanumérico compuesto (ej. '92-A', '4B', 'Mz-12')?
        elif re.match(r"^\d+[-/][A-Za-z0-9]+$", clean_word):
            processed.append(clean_word.upper())
        elif re.match(r"^(?:MZ|LT|SEC|SECC|EDIF|ETAPA)[-.]?\d+[A-Z]?$", upper_word):
            processed.append(upper_word)
        # 5. Caso general de palabra
        else:
            if "-" in clean_word:
                sub_parts = [
                    p.capitalize() if p.lower() not in SPANISH_PREPOSITIONS else p.lower()
                    for p in clean_word.split("-")
                ]
                processed.append("-".join(sub_parts))
            else:
                processed.append(clean_word[0].upper() + clean_word[1:].lower() if len(clean_word) > 1 else clean_word.upper())

        prev_lower_word = lower_word
        word_index += 1

    result = "".join(processed)
    # Limpiar posibles puntos dobles accidentales
    result = re.sub(r"\.{2,}", ".", result)

    return result


class ToponymyAnalyzer:
    """Analizador y clasificador taxonómico de asentamientos y toponimia mexicana."""

    SUPERMANZANA_RE = re.compile(
        r"^(?:SUPER\s*MANZANA|SUPERMANZANA|S\.?\s*M\.?|SM)\s*[-#]?\s*(\d+[A-Za-z]?(?:[-\s][A-Za-z0-9]+)?)\b",
        re.IGNORECASE
    )
    REGION_RE = re.compile(
        r"^(?:REGI[ÓO]N|REGION|R\.?|REG\.?)\s*[-#]?\s*(\d+[A-Za-z]?(?:[-\s][A-Za-z0-9]+)?)\b",
        re.IGNORECASE
    )
    COLONIA_RE = re.compile(
        r"^(?:COLONIA|COL\.?)\s+(.+)",
        re.IGNORECASE
    )
    FRACC_RE = re.compile(
        r"^(?:FRACCIONAMIENTO|FRACC\.?)\s+(.+)",
        re.IGNORECASE
    )
    RESIDENCIAL_RE = re.compile(
        r"^(?:RESIDENCIAL|PRIVADA|PRIV\.?)\s+(.+)",
        re.IGNORECASE
    )
    UH_RE = re.compile(
        r"^(?:UNIDAD\s+HABITACIONAL|U\.?\s*H\.?)\s+(.+)",
        re.IGNORECASE
    )
    BARRIO_RE = re.compile(
        r"^(?:BARRIO|PUEBLO|EJIDO)\s+(?:DE\s+)?(.+)",
        re.IGNORECASE
    )
    BARE_NUMERIC_RE = re.compile(
        r"^(\d+[A-Za-z]?(?:[-\s][A-Za-z0-9]+)?)$"
    )

    @classmethod
    def classify_name(cls, name: str) -> Dict[str, Any]:
        """
        Clasifica un nombre urbano en su categoría taxonómica.
        Retorna dict con: category, raw_prefix, core_name, number, suffix.
        """
        raw = str(name).strip()
        if not raw:
            return {
                "category": "EMPTY",
                "raw_prefix": "",
                "core_name": "",
                "number": None,
                "suffix": None
            }

        # 1. Supermanzanas
        m_sm = cls.SUPERMANZANA_RE.match(raw)
        if m_sm:
            num = m_sm.group(1).strip()
            return {
                "category": "SUPERMANZANA",
                "raw_prefix": raw[:m_sm.start(1)].strip(),
                "core_name": num,
                "number": num,
                "suffix": None
            }

        # 2. Regiones
        m_reg = cls.REGION_RE.match(raw)
        if m_reg:
            num = m_reg.group(1).strip()
            return {
                "category": "REGION",
                "raw_prefix": raw[:m_reg.start(1)].strip(),
                "core_name": num,
                "number": num,
                "suffix": None
            }

        # 3. Números puros o alfanuméricos cortos (ej. '94', '100', '228-A')
        m_num = cls.BARE_NUMERIC_RE.match(raw)
        if m_num:
            num = m_num.group(1).strip()
            return {
                "category": "BARE_NUMERIC",
                "raw_prefix": "",
                "core_name": num,
                "number": num,
                "suffix": None
            }

        # 4. Colonias
        m_col = cls.COLONIA_RE.match(raw)
        if m_col:
            core = m_col.group(1).strip()
            return {
                "category": "COLONIA",
                "raw_prefix": raw[:m_col.start(1)].strip(),
                "core_name": core,
                "number": None,
                "suffix": None
            }

        # 5. Fraccionamientos
        m_fracc = cls.FRACC_RE.match(raw)
        if m_fracc:
            core = m_fracc.group(1).strip()
            return {
                "category": "FRACCIONAMIENTO",
                "raw_prefix": raw[:m_fracc.start(1)].strip(),
                "core_name": core,
                "number": None,
                "suffix": None
            }

        # 6. Residenciales y Privadas
        m_res = cls.RESIDENCIAL_RE.match(raw)
        if m_res:
            core = m_res.group(1).strip()
            return {
                "category": "RESIDENCIAL",
                "raw_prefix": raw[:m_res.start(1)].strip(),
                "core_name": core,
                "number": None,
                "suffix": None
            }

        # 7. Unidades Habitacionales
        m_uh = cls.UH_RE.match(raw)
        if m_uh:
            core = m_uh.group(1).strip()
            return {
                "category": "UNIDAD_HABITACIONAL",
                "raw_prefix": raw[:m_uh.start(1)].strip(),
                "core_name": core,
                "number": None,
                "suffix": None
            }

        # 8. Barrios, Pueblos, Ejidos
        m_barrio = cls.BARRIO_RE.match(raw)
        if m_barrio:
            core = m_barrio.group(1).strip()
            return {
                "category": "BARRIO_PUEBLO",
                "raw_prefix": raw[:m_barrio.start(1)].strip(),
                "core_name": core,
                "number": None,
                "suffix": None
            }

        # 9. Genérico
        return {
            "category": "GENERIC",
            "raw_prefix": "",
            "core_name": raw,
            "number": None,
            "suffix": None
        }

    @classmethod
    def analyze_collection(cls, places: List[Dict]) -> Dict[str, Any]:
        """
        Analiza una colección completa de lugares y genera un diagnóstico taxonómico.
        """
        counts = {
            "SUPERMANZANA": 0,
            "REGION": 0,
            "BARE_NUMERIC": 0,
            "COLONIA": 0,
            "FRACCIONAMIENTO": 0,
            "RESIDENCIAL": 0,
            "UNIDAD_HABITACIONAL": 0,
            "BARRIO_PUEBLO": 0,
            "GENERIC": 0,
            "EMPTY": 0
        }
        prefix_samples: Dict[str, List[str]] = {}

        for p in places:
            name = p.get("name", "")
            classification = cls.classify_name(name)
            cat = classification["category"]
            counts[cat] = counts.get(cat, 0) + 1

            pref = classification["raw_prefix"] or cat
            if pref not in prefix_samples:
                prefix_samples[pref] = []
            if len(prefix_samples[pref]) < 3:
                prefix_samples[pref].append(name)

        return {
            "total": len(places),
            "categories": counts,
            "prefix_samples": prefix_samples,
            "has_bare_numbers": counts["BARE_NUMERIC"] > 0,
            "has_redundant_colonias": counts["COLONIA"] > 0,
            "has_supermanzanas": (counts["SUPERMANZANA"] + counts["REGION"] + counts["BARE_NUMERIC"]) > 0
        }


def haversine_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calcula la distancia esferoidal en metros entre dos coordenadas geográficas."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


class ToponymyHomogenizer:
    """Motor de transformación y curación masiva de toponimia urbana."""

    @staticmethod
    def canonical_key(name: str) -> str:
        """
        Genera una clave de normalización fonética y de texto para deduplicación.
        Elimina acentos, prefijos redundantes y signos de puntuación.
        """
        s = str(name).strip().upper()
        s = s.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U").replace("Ü", "U")
        s = re.sub(r"^(?:SUPER\s*MANZANA|SUPERMANZANA|SM|S\.?M\.?|REGION|REG\.?|R\.?|COLONIA|COL\.?|FRACCIONAMIENTO|FRACC\.?)\s*", "", s)
        s = re.sub(r"[^A-Z0-9]", "", s)
        return s

    @classmethod
    def unify_supermanzanas(
        cls,
        places: List[Dict],
        target_format: str = "Supermanzana {num}"
    ) -> Tuple[List[Dict], int]:
        """
        Unifica todas las variantes de Supermanzanas, Regiones y Números sueltos
        hacia un formato canónico uniforme (ej. 'Supermanzana {num}', 'SM {num}', 'Región {num}', '{num}').
        """
        modified_count = 0
        output = []

        for p in places:
            item = dict(p)
            name = item.get("name", "")
            classification = ToponymyAnalyzer.classify_name(name)

            if classification["category"] in {"SUPERMANZANA", "REGION", "BARE_NUMERIC"}:
                num = classification["number"]
                if num:
                    new_name = target_format.replace("{num}", num)
                    if new_name != name:
                        item["name"] = new_name
                        item["type"] = "suburb"
                        modified_count += 1
            output.append(item)

        return output, modified_count

    @classmethod
    def strip_redundant_prefixes(
        cls,
        places: List[Dict],
        strip_colonia: bool = True,
        fracc_mode: str = "Fracc.",  # "Fracc.", "strip", o "keep"
        strip_residencial: bool = False
    ) -> Tuple[List[Dict], int]:
        """
        Remueve o estandariza prefijos redundantes como 'Colonia' o 'Fraccionamiento'.
        """
        modified_count = 0
        output = []

        for p in places:
            item = dict(p)
            name = item.get("name", "")
            classification = ToponymyAnalyzer.classify_name(name)
            cat = classification["category"]
            core = classification["core_name"]
            new_name = name

            if cat == "COLONIA" and strip_colonia:
                new_name = smart_title_case(core)
            elif cat == "FRACCIONAMIENTO":
                if fracc_mode == "strip":
                    new_name = smart_title_case(core)
                elif fracc_mode == "Fracc.":
                    core_clean = smart_title_case(core)
                    if core_clean.lower().startswith("fracc."):
                        new_name = core_clean
                    else:
                        new_name = f"Fracc. {core_clean}"
            elif cat == "RESIDENCIAL" and strip_residencial:
                new_name = smart_title_case(core)

            if new_name != name:
                item["name"] = new_name
                modified_count += 1
            output.append(item)

        return output, modified_count

    @classmethod
    def apply_smart_casing(cls, places: List[Dict]) -> Tuple[List[Dict], int]:
        """
        Aplica Smart Spanish Title Casing a todos los nombres de la colección.
        """
        modified_count = 0
        output = []

        for p in places:
            item = dict(p)
            name = item.get("name", "")
            new_name = smart_title_case(name)
            if new_name != name:
                item["name"] = new_name
                modified_count += 1
            output.append(item)

        return output, modified_count

    @classmethod
    def apply_batch_regex(
        cls,
        places: List[Dict],
        pattern: str,
        replacement: str,
        ignore_case: bool = True,
        selected_indices: Optional[List[int]] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Ejecuta una búsqueda y reemplazo masivo con expresiones regulares sobre la lista.
        Retorna (lugares_actualizados, lista_de_diferencias).
        """
        flags = re.IGNORECASE if ignore_case else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Expresión regular inválida: {e}")

        output = []
        diffs = []
        indices_set = set(selected_indices) if selected_indices is not None else None

        for idx, p in enumerate(places):
            item = dict(p)
            name = item.get("name", "")

            if indices_set is None or idx in indices_set:
                new_name = rx.sub(replacement, name).strip()
                if new_name != name:
                    diffs.append({
                        "index": idx,
                        "old": name,
                        "new": new_name,
                        "loc": item.get("loc", [0.0, 0.0])
                    })
                    item["name"] = new_name
            output.append(item)

        return output, diffs

    @classmethod
    def spatial_deduplicate(
        cls,
        places: List[Dict],
        distance_threshold_m: float = 500.0
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Detecta y elimina asentamientos duplicados que se encuentren a una distancia
        menor al umbral y tengan nombres semánticamente equivalentes.

        Priorización de fuentes:
        1. OSM Polígono (source: 'OSM_POLYGON')
        2. OSM Nodo (source: 'OSM_NODE')
        3. DENUE (source: 'INEGI_DENUE')
        """
        if not places:
            return [], []

        source_weights = {
            "OSM_POLYGON": 3,
            "OSM_NODE": 2,
            "INEGI_DENUE": 1
        }

        decorated = []
        for idx, p in enumerate(places):
            loc = p.get("loc", [0.0, 0.0])
            lon = float(loc[0]) if len(loc) >= 2 else 0.0
            lat = float(loc[1]) if len(loc) >= 2 else 0.0
            name = p.get("name", "")
            key = cls.canonical_key(name)
            src = p.get("source", "UNKNOWN")
            weight = source_weights.get(src, 0)
            decorated.append({
                "index": idx,
                "place": dict(p),
                "lon": lon,
                "lat": lat,
                "name": name,
                "key": key,
                "weight": weight
            })

        kept: List[Dict] = []
        removed: List[Dict] = []
        is_dropped = [False] * len(decorated)

        for i in range(len(decorated)):
            if is_dropped[i]:
                continue
            curr = decorated[i]

            for j in range(i + 1, len(decorated)):
                if is_dropped[j]:
                    continue
                cand = decorated[j]

                dist = haversine_distance_m(curr["lon"], curr["lat"], cand["lon"], cand["lat"])
                if dist <= distance_threshold_m:
                    is_match = False
                    if curr["key"] and curr["key"] == cand["key"]:
                        is_match = True
                    elif curr["name"].strip().lower() == cand["name"].strip().lower():
                        is_match = True

                    if is_match:
                        if cand["weight"] > curr["weight"]:
                            is_dropped[i] = True
                            removed.append({
                                "dropped": curr["place"],
                                "kept": cand["place"],
                                "reason": f"Duplicado espacial ({dist:.0f}m) con clave {curr['key']}"
                            })
                            break
                        else:
                            is_dropped[j] = True
                            removed.append({
                                "dropped": cand["place"],
                                "kept": curr["place"],
                                "reason": f"Duplicado espacial ({dist:.0f}m) con clave {cand['key']}"
                            })

            if not is_dropped[i]:
                kept.append(curr["place"])

        return kept, removed

    @classmethod
    def apply_pipeline(
        cls,
        places: List[Dict],
        options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Ejecuta el pipeline configurable de homogeneización sobre una lista de lugares.
        """
        current_places = [dict(p) for p in places]
        total_initial = len(current_places)
        diff_log: List[Dict] = []

        # 1. Búsqueda y reemplazo regex inicial (si existe)
        regex_rule = options.get("regex_rule")
        if regex_rule and isinstance(regex_rule, dict):
            pat = regex_rule.get("pattern")
            rep = regex_rule.get("replacement", "")
            ic = regex_rule.get("ignore_case", True)
            if pat:
                current_places, diffs = cls.apply_batch_regex(current_places, pat, rep, ignore_case=ic)
                diff_log.extend(diffs)

        # 2. Unificación de Supermanzanas
        unify_sm_format = options.get("unify_supermanzanas")
        if unify_sm_format:
            current_places, c_sm = cls.unify_supermanzanas(current_places, target_format=unify_sm_format)

        # 3. Supresión de prefijos redundantes
        if options.get("strip_prefixes"):
            strip_cfg = options["strip_prefixes"]
            current_places, c_pref = cls.strip_redundant_prefixes(
                current_places,
                strip_colonia=strip_cfg.get("strip_colonia", True),
                fracc_mode=strip_cfg.get("fracc_mode", "Fracc."),
                strip_residencial=strip_cfg.get("strip_residencial", False)
            )

        # 4. Smart Title Casing
        if options.get("smart_casing"):
            current_places, c_case = cls.apply_smart_casing(current_places)

        # 5. Deduplicación espacial
        duplicates_removed = []
        if options.get("spatial_deduplicate"):
            dist_threshold = float(options.get("spatial_distance_m", 500.0))
            current_places, duplicates_removed = cls.spatial_deduplicate(
                current_places,
                distance_threshold_m=dist_threshold
            )

        diagnosis = ToponymyAnalyzer.analyze_collection(current_places)

        full_diffs = []
        for idx in range(min(total_initial, len(current_places))):
            orig_name = places[idx].get("name", "")
            new_name = current_places[idx].get("name", "")
            if orig_name != new_name:
                full_diffs.append({
                    "index": idx,
                    "old": orig_name,
                    "new": new_name
                })

        return {
            "places": current_places,
            "diagnosis": diagnosis,
            "diffs": full_diffs,
            "duplicates_removed_count": len(duplicates_removed),
            "duplicates_removed": duplicates_removed
        }


def calculate_place_prestige_score(place: Dict[str, Any], heuristic: str = "density") -> float:
    """
    Calcula una puntuacion heuristica de representatividad toponimica para un asentamiento:
    - Mayor actividad comercial DENUE (comercios).
    - Procedencia cartografica (curado > poligono > nodo > DENUE crudo).
    - Jerarquia toponimica (Supermanzanas / Regiones > Suburb > Quarter > Neighbourhood).
    - En heuristica 'shortest', premia nombres mas cortos y limpios para rotulacion de metro.
    """
    score = 0.0
    # 1. Establecimientos comerciales DENUE
    establishments = float(place.get("establishments", 0) or 0)
    score += establishments * 2.5

    # 2. Procedencia cartografica
    source = str(place.get("source", "")).upper()
    if "YAML_CURATED" in source or "CURATED" in source:
        score += 600.0
    elif "OSM_POLYGON" in source:
        score += 300.0
    elif "OSM_NODE" in source:
        score += 150.0
    elif "INEGI_DENUE" in source or "DENUE" in source:
        score += 50.0

    # 3. Jerarquia y Taxonomia urbana
    name = str(place.get("name", "")).strip()
    cat_info = ToponymyAnalyzer.classify_name(name)
    category = cat_info.get("category", "GENERIC")
    if category in ("SUPERMANZANA", "REGION"):
        score += 350.0
    elif category == "COLONIA":
        score += 200.0
    elif category == "BARRIO_PUEBLO":
        score += 180.0
    elif category == "FRACCIONAMIENTO":
        score += 120.0
    elif category == "RESIDENCIAL":
        score += 70.0
    elif category == "UNIDAD_HABITACIONAL":
        score += 90.0
    elif category == "BARE_NUMERIC":
        score += 220.0

    # 4. Tipo OSM
    ptype = str(place.get("type", "")).lower()
    if ptype == "suburb":
        score += 120.0
    elif ptype == "quarter":
        score += 70.0
    elif ptype == "neighbourhood":
        score += 30.0

    # 5. Longitud del nombre segun heuristica
    if heuristic == "shortest":
        score += max(0.0, 150.0 - (len(name) * 3.0))

    return round(score, 2)


def cluster_places_by_proximity(
    places: List[Dict[str, Any]],
    radius_m: float = 1000.0,
    rank_heuristic: str = "density"
) -> Dict[str, Any]:
    """
    Agrupa asentamientos urbanos en zonas de exclusividad espacial de radio `radius_m`.
    Aplica particionamiento radial Voronoi / Greedy Anchor Clustering:
    - Previene el encadenamiento arbitrario de colonias a lo largo de kilometros.
    - Garantiza que los centros de zona esten separados al menos por `radius_m`.
    - Agrupa competidores cercanos al Anchor mas proximo dentro del radio.
    - Proporciona preseleccion heuristica del mejor candidato y deja la decision final al usuario.
    """
    if not places:
        return {
            "status": "ok",
            "total_places": 0,
            "radius_m": radius_m,
            "heuristic": rank_heuristic,
            "anchors_count": 0,
            "conflict_zones_count": 0,
            "isolated_zones_count": 0,
            "zones": [],
            "summary": {"projected_kept": 0, "projected_pruned": 0}
        }

    # 1. Decorar con score y posicion valida
    valid_places: List[Dict[str, Any]] = []
    for idx, p in enumerate(places):
        loc = p.get("loc")
        if not loc or len(loc) != 2:
            continue
        try:
            lon = float(loc[0])
            lat = float(loc[1])
        except (ValueError, TypeError):
            continue

        score = calculate_place_prestige_score(p, heuristic=rank_heuristic)
        valid_places.append({
            "original_index": idx,
            "place": p,
            "name": str(p.get("name", "")).strip(),
            "loc": [lon, lat],
            "type": p.get("type", "suburb"),
            "source": p.get("source", "UNKNOWN"),
            "establishments": int(p.get("establishments", 0) or 0),
            "score": score
        })

    if not valid_places:
        return {
            "status": "ok",
            "total_places": len(places),
            "radius_m": radius_m,
            "heuristic": rank_heuristic,
            "anchors_count": 0,
            "conflict_zones_count": 0,
            "isolated_zones_count": 0,
            "zones": [],
            "summary": {"projected_kept": 0, "projected_pruned": 0}
        }

    # Ordenar por puntuacion descendente
    valid_places.sort(key=lambda x: x["score"], reverse=True)

    # 2. Greedy Anchor Selection (separados al menos por radius_m)
    anchors: List[Dict[str, Any]] = []
    for cand in valid_places:
        too_close = False
        for a in anchors:
            d = haversine_distance_m(cand["loc"][0], cand["loc"][1], a["loc"][0], a["loc"][1])
            if d < radius_m:
                too_close = True
                break
        if not too_close:
            anchors.append(cand)

    # 3. Asignar candidatos no-anchor al anchor mas cercano dentro de radius_m
    anchor_dict: Dict[int, Dict[str, Any]] = {}
    for a in anchors:
        anchor_dict[a["original_index"]] = {
            "anchor": a,
            "competitors": []
        }

    for cand in valid_places:
        if cand["original_index"] in anchor_dict:
            continue

        best_anchor_idx = None
        best_dist = float("inf")
        for a in anchors:
            d = haversine_distance_m(cand["loc"][0], cand["loc"][1], a["loc"][0], a["loc"][1])
            if d <= radius_m and d < best_dist:
                best_dist = d
                best_anchor_idx = a["original_index"]

        if best_anchor_idx is not None:
            anchor_dict[best_anchor_idx]["competitors"].append({
                "candidate": cand,
                "distance_to_anchor_m": round(best_dist, 1)
            })
        else:
            # Fallback en caso extremo: asignar al anchor global mas cercano
            closest_a = min(anchors, key=lambda a: haversine_distance_m(cand["loc"][0], cand["loc"][1], a["loc"][0], a["loc"][1]))
            d_cl = haversine_distance_m(cand["loc"][0], cand["loc"][1], closest_a["loc"][0], closest_a["loc"][1])
            anchor_dict[closest_a["original_index"]]["competitors"].append({
                "candidate": cand,
                "distance_to_anchor_m": round(d_cl, 1)
            })

    # 4. Formatear zonas
    zones_list: List[Dict[str, Any]] = []
    conflict_count = 0
    isolated_count = 0

    zone_seq = 1
    for a_idx, zdata in anchor_dict.items():
        anchor_cand = zdata["anchor"]
        comps = zdata["competitors"]
        is_conflict = len(comps) > 0

        if is_conflict:
            conflict_count += 1
        else:
            isolated_count += 1

        # Ordenar competidores por distancia o puntuacion
        comps.sort(key=lambda c: c["candidate"]["score"], reverse=True)

        candidates_formatted = [{
            "original_index": anchor_cand["original_index"],
            "name": anchor_cand["name"],
            "loc": anchor_cand["loc"],
            "type": anchor_cand["type"],
            "source": anchor_cand["source"],
            "establishments": anchor_cand["establishments"],
            "score": anchor_cand["score"],
            "distance_m": 0.0,
            "recommended": True
        }]

        for comp in comps:
            c = comp["candidate"]
            candidates_formatted.append({
                "original_index": c["original_index"],
                "name": c["name"],
                "loc": c["loc"],
                "type": c["type"],
                "source": c["source"],
                "establishments": c["establishments"],
                "score": c["score"],
                "distance_m": comp["distance_to_anchor_m"],
                "recommended": False
            })

        zones_list.append({
            "zone_id": f"zone_{zone_seq}",
            "zone_number": zone_seq,
            "title": anchor_cand["name"],
            "center": anchor_cand["loc"],
            "radius_m": radius_m,
            "is_conflict": is_conflict,
            "candidates_count": len(candidates_formatted),
            "candidates": candidates_formatted,
            "recommended_index": anchor_cand["original_index"],
            "selected_index": anchor_cand["original_index"],
            "is_exception": False
        })
        zone_seq += 1

    # Ordenar zonas: primero las que tienen mayor cantidad de competidores en conflicto
    zones_list.sort(key=lambda z: (1 if z["is_conflict"] else 0, z["candidates_count"]), reverse=True)

    return {
        "status": "ok",
        "total_places": len(places),
        "radius_m": radius_m,
        "heuristic": rank_heuristic,
        "anchors_count": len(anchors),
        "conflict_zones_count": conflict_count,
        "isolated_zones_count": isolated_count,
        "zones": zones_list,
        "summary": {
            "projected_kept": len(anchors),
            "projected_pruned": len(places) - len(anchors)
        }
    }


def apply_zone_thinning_selection(
    places: List[Dict[str, Any]],
    zones: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Aplica las selecciones de zonificacion resueltas por el usuario:
    - Para zonas con `is_exception == True`: conserva todos los candidatos.
    - Para zonas normales: conserva unicamente el candidato con `selected_index`.
    Retorna la lista depurada de `places`.
    """
    kept_indices: Set[int] = set()
    for z in zones:
        if z.get("is_exception"):
            for c in z.get("candidates", []):
                idx = c.get("original_index")
                if idx is not None:
                    kept_indices.add(int(idx))
        else:
            sel_idx = z.get("selected_index")
            if sel_idx is not None:
                kept_indices.add(int(sel_idx))
            elif z.get("recommended_index") is not None:
                kept_indices.add(int(z["recommended_index"]))

    kept_places = [places[i] for i in sorted(kept_indices) if 0 <= i < len(places)]
    return {
        "status": "ok",
        "total_initial": len(places),
        "total_kept": len(kept_places),
        "total_pruned": len(places) - len(kept_places),
        "places": kept_places
    }


