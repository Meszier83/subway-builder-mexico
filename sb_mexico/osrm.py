"""
sb_mexico.osrm
==============
Módulo de integración canónica con OSRM (Open Source Routing Machine).
Implementa el estándar oficial de Subway Builder para:
1. Compilar redes viales con el perfil car.lua en WSL 2.
2. Levantar el microservicio osrm-routed (MLD) en Docker.
3. Consultar rutas y geometrías reales (drivingSeconds, drivingDistance, drivingPath).
4. Proporcionar el fallback canónico oficial (1.3x a 40 km/h) ante cualquier contingencia.
"""

import os
import sys
import time
import json
import shutil
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any

from sb_mexico.cartography import to_wsl_path


CANONICAL_SPEED_KMH = 40.0
CANONICAL_CIRCUITY = 1.3


def calculate_canonical_driving_fallback(dist_euclid_m: float) -> Tuple[int, int]:
    """
    Cálculo canónico de respaldo documentado por Colin (creador de Subway Builder):
    distancia euclidiana x 1.3 de circuidad vial a 40 km/h de velocidad promedio a flujo libre.
    """
    d_m = max(150.0, float(dist_euclid_m))
    road_m = int(round(d_m * CANONICAL_CIRCUITY))
    speed_ms = CANONICAL_SPEED_KMH / 3.6
    driving_seconds = max(45, int(round(road_m / speed_ms)))
    return road_m, driving_seconds


def _run_wsl_command(cmd: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Ejecuta un comando en WSL con auto-recuperación ante errores E_UNEXPECTED de Windows."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 and ("E_UNEXPECTED" in (res.stderr or "") or res.returncode == 4294967295):
            subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=5)
            time.sleep(1.5)
            subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "-u", "root", "-e", "/usr/sbin/service", "docker", "start"],
                capture_output=True,
                timeout=15
            )
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res
    except Exception as e:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def is_docker_available() -> Tuple[bool, str]:
    """
    Comprueba si Docker está disponible y operativo, priorizando WSL 2.
    Si el daemon de Docker en WSL 2 está apagado, intenta iniciarlo automáticamente como root.
    Retorna (disponible, entorno: 'wsl' | 'native' | 'none').
    """
    # 1. Probar en WSL 2 (Ubuntu)
    if os.name == "nt":
        res = _run_wsl_command(["wsl.exe", "-d", "Ubuntu", "-e", "docker", "info"], timeout=15)
        if res.returncode == 0:
            return True, "wsl"

        # Si docker está instalado pero el servicio está detenido, arrancarlo con /usr/sbin/service
        subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "-u", "root", "-e", "/usr/sbin/service", "docker", "start"],
            capture_output=True,
            timeout=15
        )
        res2 = _run_wsl_command(["wsl.exe", "-d", "Ubuntu", "-e", "docker", "info"], timeout=15)
        if res2.returncode == 0:
            return True, "wsl"

    # 2. Probar Docker nativo en el host
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            res = subprocess.run(
                [docker_bin, "info"],
                capture_output=True,
                text=True,
                timeout=8
            )
            if res.returncode == 0:
                return True, "native"
        except Exception:
            pass

    return False, "none"


def get_wsl_home_dir() -> str:
    """Obtiene la ruta home del usuario en WSL 2."""
    try:
        res = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "-e", "bash", "-c", "echo $HOME"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "/home/keppl"


def prepare_osrm_network_wsl(
    city_code: str,
    osm_pbf_path: str,
    bbox: List[float],
    force_rebuild: bool = False
) -> Tuple[bool, str]:
    """
    Recorta el archivo PBF al BBOX de la ciudad (con osmium) y genera los archivos
    compilados de OSRM (.osrm, .osrm.partition, .osrm.cells) en la partición ext4 de WSL.
    Retorna (exitoso, directorio_de_compilacion_wsl).
    """
    city_slug = city_code.lower()
    home_dir = get_wsl_home_dir()
    wsl_build_dir = f"{home_dir}/osrm_{city_slug}"

    wsl_pbf = to_wsl_path(os.path.abspath(osm_pbf_path))

    # Script bash para ejecutar dentro de WSL 2
    margin = 0.05
    min_lon = max(-180.0, bbox[0] - margin)
    min_lat = max(-90.0, bbox[1] - margin)
    max_lon = min(180.0, bbox[2] + margin)
    max_lat = min(90.0, bbox[3] + margin)
    bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}"

    bash_script = f"""set -e
mkdir -p "{wsl_build_dir}"
cd "{wsl_build_dir}"

TARGET_PBF="{wsl_build_dir}/{city_slug}.osm.pbf"
OSRM_BASE="{wsl_build_dir}/{city_slug}.osrm"

# Si ya está compilado y no se fuerza reconstrucción, omitir
if [ "{str(force_rebuild).lower()}" = "false" ] && [ -f "${{OSRM_BASE}}.cells" ] && [ -f "${{OSRM_BASE}}.partition" ]; then
    echo "OSRM_CACHE_VALID"
    exit 0
fi

# Recorte BBOX eficiente con osmium si es mayor a 20MB
FILE_SIZE_MB=$(du -m "{wsl_pbf}" | cut -f1)
if [ "$FILE_SIZE_MB" -gt 20 ] && which osmium >/dev/null 2>&1; then
    echo "-> [OSRM] Recortando PBF ({bbox_str}) con osmium..."
    osmium extract -b {bbox_str} --overwrite -o "$TARGET_PBF" "{wsl_pbf}"
else
    echo "-> [OSRM] Copiando PBF a partición ext4..."
    cp -f "{wsl_pbf}" "$TARGET_PBF"
fi

echo "-> [OSRM] Ejecutando osrm-extract (perfil car.lua)..."
docker run --rm -v "{wsl_build_dir}:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua "/data/{city_slug}.osm.pbf"

echo "-> [OSRM] Ejecutando osrm-partition (MLD)..."
docker run --rm -v "{wsl_build_dir}:/data" osrm/osrm-backend osrm-partition "/data/{city_slug}.osrm"

echo "-> [OSRM] Ejecutando osrm-customize..."
docker run --rm -v "{wsl_build_dir}:/data" osrm/osrm-backend osrm-customize "/data/{city_slug}.osrm"

echo "OSRM_BUILD_SUCCESS"
"""

    try:
        proc = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "-e", "bash", "-c", bash_script],
            capture_output=True,
            text=True,
            timeout=180
        )
        output = proc.stdout + proc.stderr
        if "OSRM_CACHE_VALID" in output:
            print(f"-> Red vial OSRM existente y al día ({city_code}). Reutilizando caché compilado.")
            return True, wsl_build_dir
        elif proc.returncode == 0 and "OSRM_BUILD_SUCCESS" in output:
            print(f"-> Red vial OSRM compilada exitosamente ({city_code}).")
            return True, wsl_build_dir
        else:
            print(f"  [WARN] Fallo compilando OSRM en WSL: {output.strip()[-300:]}")
            return False, wsl_build_dir
    except Exception as e:
        print(f"  [WARN] Excepción compilando OSRM: {e}")
        return False, wsl_build_dir


_ACTIVE_OSRM_PROC: Optional[subprocess.Popen] = None


def start_osrm_daemon_wsl(city_code: str, port: int = 5000) -> bool:
    """
    Inicia el contenedor Docker osrm-routed para la ciudad dada en el puerto especificado.
    Usa subprocess.Popen con wsl.exe abierto para mantener activa la máquina virtual WSL 2
    y prevenir que Windows cierre el daemon por inactividad durante las consultas.
    Espera hasta que el servicio responda a peticiones HTTP.
    """
    global _ACTIVE_OSRM_PROC
    stop_osrm_daemon_wsl(city_code)

    city_slug = city_code.lower()
    container_name = f"sb_osrm_{city_slug}"
    home_dir = get_wsl_home_dir()
    wsl_build_dir = f"{home_dir}/osrm_{city_slug}"

    cmd = [
        "wsl.exe", "-d", "Ubuntu", "-e",
        "docker", "run", "--rm",
        "--name", container_name,
        "-p", f"{port}:5000",
        "-v", f"{wsl_build_dir}:/data",
        "osrm/osrm-backend",
        "osrm-routed", "--algorithm", "mld", f"/data/{city_slug}.osrm"
    ]

    try:
        _ACTIVE_OSRM_PROC = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Esperar a que el servidor HTTP esté listo (máximo 8 segundos)
        test_url = f"http://127.0.0.1:{port}/route/v1/driving/0,0;0,0"
        for _ in range(16):
            time.sleep(0.5)
            if _ACTIVE_OSRM_PROC.poll() is not None:
                print(f"  [WARN] El proceso OSRM en WSL terminó prematuramente (código {_ACTIVE_OSRM_PROC.poll()}).")
                stop_osrm_daemon_wsl(city_code)
                return False
            try:
                with urllib.request.urlopen(test_url, timeout=1.0) as resp:
                    if resp.status in (200, 400):
                        return True
            except urllib.error.HTTPError:
                return True
            except Exception:
                continue

        print(f"  [WARN] El servidor OSRM en el puerto {port} no respondió a tiempo.")
        stop_osrm_daemon_wsl(city_code)
        return False
    except Exception as e:
        print(f"  [WARN] Error iniciando daemon OSRM: {e}")
        stop_osrm_daemon_wsl(city_code)
        return False


def stop_osrm_daemon_wsl(city_code: str) -> None:
    """Detiene y elimina el contenedor Docker efímero de OSRM y cierra el proceso supervisor WSL."""
    global _ACTIVE_OSRM_PROC
    city_slug = city_code.lower()
    container_name = f"sb_osrm_{city_slug}"
    try:
        subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "-e", "docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=10
        )
    except Exception:
        pass

    if _ACTIVE_OSRM_PROC is not None:
        try:
            _ACTIVE_OSRM_PROC.terminate()
            _ACTIVE_OSRM_PROC.wait(timeout=3)
        except Exception:
            try:
                _ACTIVE_OSRM_PROC.kill()
            except Exception:
                pass
        _ACTIVE_OSRM_PROC = None


def enrich_pops_with_osrm(
    pops: List[Dict],
    demand_points: List[Dict],
    osrm_url: str = "http://127.0.0.1:5000"
) -> Tuple[int, int]:
    """
    Enriquece cada cohorte (pop) con drivingSeconds, drivingDistance y drivingPath
    consultando el servicio OSRM local por cada par único (residenceId, jobId).
    Si una ruta específica no es conectable en la red vial o el daemon se interrumpe,
    aplica el fallback canónico oficial de Colin (1.3x a 40 km/h).
    Retorna (rutas_enriquecidas_osrm, rutas_fallback).
    """
    if not pops or not demand_points:
        return 0, 0

    import requests
    from urllib3.util import Retry
    from requests.adapters import HTTPAdapter

    dp_locs = {p["id"]: p["location"] for p in demand_points if "id" in p and "location" in p}

    # Identificar pares únicos de origen y destino
    unique_pairs = set()
    for p in pops:
        res_id = p.get("residenceId")
        job_id = p.get("jobId")
        if res_id and job_id:
            unique_pairs.add((res_id, job_id))

    routes_cache: Dict[Tuple[str, str], Tuple[int, int, Optional[List]]] = {}
    osrm_success = 0
    fallback_count = 0

    session = requests.Session()
    # Permitir hasta 2 reintentos transparentes cuando OSRM renueva el keep-alive (Keep-Alive: max=512)
    retries = Retry(total=2, backoff_factor=0.05, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries, pool_connections=1, pool_maxsize=10))

    total_pairs = len(unique_pairs)
    processed = 0
    consecutive_connection_errors = 0
    service_aborted = False

    for res_id, job_id in unique_pairs:
        processed += 1
        if processed == 1:
            print(f"   • Conexión OSRM activa. Consultando {total_pairs:,} pares viales...", flush=True)
        elif processed % 500 == 0 or processed == total_pairs:
            print(f"   • OSRM progreso: {processed:,} / {total_pairs:,} ({processed / total_pairs:.0%})...", flush=True)

        orig_loc = dp_locs.get(res_id)
        dest_loc = dp_locs.get(job_id)

        if not orig_loc or not dest_loc:
            continue

        # Distancia euclidiana base para fallback
        import math
        cos_lat = math.cos(math.radians((orig_loc[1] + dest_loc[1]) / 2.0))
        dx_m = (dest_loc[0] - orig_loc[0]) * 111_320.0 * cos_lat
        dy_m = (dest_loc[1] - orig_loc[1]) * 110_574.0
        euclid_m = math.hypot(dx_m, dy_m)

        # Si origen y destino son el mismo punto (viaje local intra-celda)
        if res_id == job_id or euclid_m < 20.0:
            fb_dist, fb_sec = calculate_canonical_driving_fallback(euclid_m)
            routes_cache[(res_id, job_id)] = (fb_dist, fb_sec, None)
            continue

        # Si el microservicio OSRM se abortó por desconexión previa, aplicar fallback directo sin latencia
        if service_aborted:
            fb_dist, fb_sec = calculate_canonical_driving_fallback(euclid_m)
            routes_cache[(res_id, job_id)] = (fb_dist, fb_sec, None)
            fallback_count += 1
            continue

        # Consulta HTTP a OSRM
        url = f"{osrm_url}/route/v1/driving/{orig_loc[0]},{orig_loc[1]};{dest_loc[0]},{dest_loc[1]}?overview=full&geometries=geojson"
        try:
            resp = session.get(url, timeout=(1.0, 3.0))
            consecutive_connection_errors = 0  # El servidor respondió, está activo
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    best = data["routes"][0]
                    duration_sec = int(round(best["duration"]))
                    distance_m = int(round(best["distance"]))
                    path_coords = best.get("geometry", {}).get("coordinates", [])

                    routes_cache[(res_id, job_id)] = (distance_m, duration_sec, path_coords)
                    osrm_success += 1
                    continue
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout):
            consecutive_connection_errors += 1
            if consecutive_connection_errors >= 5:
                print(
                    f"   [WARN] Conexión OSRM interrumpida (5 fallos consecutivos). "
                    f"Aplicando fallback canónico Colin al resto ({total_pairs - processed:,} pares)...",
                    flush=True
                )
                service_aborted = True
        except Exception:
            pass

        # Fallback canónico oficial de Colin si OSRM no conecta el par (ej. Isla Mujeres sin puente)
        fb_dist, fb_sec = calculate_canonical_driving_fallback(euclid_m)
        routes_cache[(res_id, job_id)] = (fb_dist, fb_sec, None)
        fallback_count += 1

    # Inyectar métricas en los objetos pop
    for p in pops:
        key = (p.get("residenceId"), p.get("jobId"))
        if key in routes_cache:
            dist_m, sec, path = routes_cache[key]
            p["drivingDistance"] = dist_m
            p["drivingSeconds"] = sec
            if path:
                p["drivingPath"] = path

    return osrm_success, fallback_count
