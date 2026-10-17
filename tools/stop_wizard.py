#!/usr/bin/env python3
"""
tools/stop_wizard.py
====================
Terminador integral de procesos de Subway Builder México.
Detiene y termina de manera garantizada:
  1. Servidores HTTP del Wizard (tools/wizard.py)
  2. Procesos de POI Studio (tools/poi_studio.py)
  3. Procesos del compilador de demanda y cartografía (build.py)
  4. Cualquier proceso que mantenga bloqueados los puertos 8080, 5050 o 5000 (OSRM)
  5. Contenedores Docker efímeros de OSRM en WSL 2 o nativos (sb_osrm_*)
  6. Subprocesos huérfanos de cartografía en WSL (osmium, tippecanoe, planetiler, osrm-routed)

Uso:
  python tools/stop_wizard.py
  python tools/stop_wizard.py --all
  python tools/stop_wizard.py --shutdown-wsl
  python tools/stop_wizard.py --force
"""

import os
import sys
import time
import socket
import argparse
import subprocess
import urllib.request
import urllib.error
from typing import List, Set, Dict, Any, Tuple

# Configurar stdout/stderr en modo seguro para caracteres universales
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

DEFAULT_PORTS = [8080, 5050, 5000]

# Nombres o patrones de procesos relacionados con el pipeline
TARGET_PATTERNS = [
    "tools/wizard.py",
    "tools\\wizard.py",
    "wizard.py",
    "tools/poi_studio.py",
    "tools\\poi_studio.py",
    "poi_studio.py",
    "build.py",
    "cartography_runner",
    "sb_mexico",
]

# Procesos seguros que NUNCA deben cerrarse
EXCLUDED_PROCESS_NAMES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "svchost.exe", "explorer.exe",
    "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe",
    "code.exe", "antigravity.exe", "devenv.exe", "powershell.exe", "cmd.exe",
    "wslservice.exe"
}


def log(msg: str, symbol: str = "[->]"):
    print(f" {symbol} {msg}")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Verifica si un puerto TCP local está en uso."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def attempt_graceful_http_shutdown(port: int = 8080) -> bool:
    """Intenta enviar una señal de apagado ordenado al endpoint /api/shutdown."""
    url = f"http://127.0.0.1:{port}/api/shutdown"
    try:
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status in (200, 202, 204):
                log(f"Señal HTTP /api/shutdown recibida por el Wizard en puerto {port}.", "[OK]")
                time.sleep(0.6)
                return True
    except Exception:
        pass
    return False


def get_pids_on_ports_windows(ports: List[int]) -> Dict[int, Set[int]]:
    """Encuentra PIDs asociados a puertos escuchando en Windows usando netstat."""
    port_pids: Dict[int, Set[int]] = {p: set() for p in ports}
    try:
        out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[0].upper() == "TCP":
                local_addr = parts[1]
                state = parts[3].upper()
                pid_str = parts[4]
                if state == "LISTENING":
                    for p in ports:
                        if local_addr.endswith(f":{p}") and pid_str.isdigit():
                            pid = int(pid_str)
                            if pid > 4:
                                port_pids[p].add(pid)
    except Exception as e:
        log(f"Aviso leyendo netstat: {e}", "[!]")
    return port_pids


def get_processes_windows() -> List[Dict[str, Any]]:
    """Lista procesos en Windows con PID, Nombre y Línea de comandos."""
    procs = []
    # Usar PowerShell Get-CimInstance para extraer CommandLine de forma confiable
    ps_cmd = (
        'Get-CimInstance Win32_Process | '
        'Select-Object ProcessId, Name, CommandLine | '
        'ConvertTo-Json -Compress'
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            text=True, stderr=subprocess.DEVNULL, timeout=10
        )
        if out.strip():
            import json
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                procs.append({
                    "pid": int(item.get("ProcessId") or 0),
                    "name": str(item.get("Name") or "").lower(),
                    "cmd": str(item.get("CommandLine") or "")
                })
    except Exception:
        # Respaldo simple con tasklist si PowerShell falla
        try:
            tl = subprocess.check_output("tasklist /fo csv /nh", shell=True, text=True, stderr=subprocess.DEVNULL)
            for row in tl.splitlines():
                parts = [c.strip('"') for c in row.split('","')]
                if len(parts) >= 2 and parts[1].isdigit():
                    procs.append({
                        "pid": int(parts[1]),
                        "name": parts[0].lower(),
                        "cmd": ""
                    })
        except Exception:
            pass
    return procs


def kill_pid_windows(pid: int, force: bool = True) -> bool:
    """Termina un proceso y su árbol completo en Windows con taskkill."""
    if pid <= 4:
        return False
    flags = "/F /T" if force else "/T"
    cmd = f"taskkill {flags} /PID {pid}"
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def get_processes_posix() -> List[Dict[str, Any]]:
    """Lista procesos en entornos POSIX (Linux/macOS)."""
    procs = []
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,comm,args"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit():
                procs.append({
                    "pid": int(parts[0]),
                    "name": parts[1].lower(),
                    "cmd": parts[2] if len(parts) > 2 else ""
                })
    except Exception:
        pass
    return procs


def kill_pid_posix(pid: int, force: bool = True) -> bool:
    """Termina un proceso en POSIX con SIGTERM o SIGKILL."""
    import signal
    if pid <= 1:
        return False
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except OSError:
        return False


def cleanup_wsl_and_docker(shutdown_wsl: bool = False) -> Tuple[int, int]:
    """Detiene y elimina contenedores OSRM y mata procesos huérfanos dentro de WSL 2."""
    containers_removed = 0
    wsl_procs_killed = 0

    # 1. Comprobar si WSL está disponible
    wsl_available = False
    try:
        wsl_check = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=5)
        wsl_available = (wsl_check.returncode == 0)
    except Exception:
        wsl_available = False

    if wsl_available:
        # Limpiar contenedores de Docker en WSL
        docker_clean_script = (
            "docker rm -f $(docker ps -aq --filter name=sb_osrm_) 2>/dev/null || true; "
            "docker rm -f $(docker ps -aq --filter ancestor=osrm/osrm-backend) 2>/dev/null || true"
        )
        try:
            res = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "-e", "bash", "-c", docker_clean_script],
                capture_output=True, text=True, timeout=8
            )
            out = res.stdout.strip()
            if out:
                containers_removed += len(out.splitlines())
        except Exception:
            pass

        # Matar procesos huérfanos de compilación en WSL
        kill_wsl_script = (
            "pkill -9 -f 'osrm-routed' 2>/dev/null || true; "
            "pkill -9 -f 'osmium' 2>/dev/null || true; "
            "pkill -9 -f 'tippecanoe' 2>/dev/null || true; "
            "pkill -9 -f 'planetiler' 2>/dev/null || true; "
            "pkill -9 -f 'cartography_runner' 2>/dev/null || true; "
            "pkill -9 -f 'sb_mexico' 2>/dev/null || true"
        )
        try:
            subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "-e", "bash", "-c", kill_wsl_script],
                capture_output=True, timeout=8
            )
            wsl_procs_killed += 1
        except Exception:
            pass

        # Apagar WSL completamente si se solicitó explícitamente
        if shutdown_wsl:
            log("Apagando máquina virtual WSL 2 (--shutdown-wsl)...", "[WSL]")
            try:
                subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=10)
                log("WSL 2 se ha detenido por completo.", "[OK]")
            except Exception as e:
                log(f"Aviso deteniendo WSL: {e}", "[!]")

    # 2. Comprobar Docker nativo en Windows por si acaso
    try:
        res = subprocess.run(
            ["docker", "rm", "-f", "sb_osrm_cancun"],
            capture_output=True, timeout=3
        )
    except Exception:
        pass

    return containers_removed, wsl_procs_killed


def terminate_all_wizard_processes(
    ports: List[int] = None,
    include_all: bool = True,
    shutdown_wsl: bool = False,
    force: bool = False
) -> Dict[str, Any]:
    """
    Función principal que orquesta la detección y terminación de todos los
    procesos del Wizard y sus componentes asociados.
    """
    if ports is None:
        ports = DEFAULT_PORTS

    is_windows = (sys.platform == "win32")
    my_pid = os.getpid()

    log("Iniciando escaneo y terminación de procesos del Wizard...", "[SCAN]")

    # 1. Apagado ordenado vía HTTP si no es modo forzado
    if not force:
        for p in ports:
            if is_port_in_use(p):
                attempt_graceful_http_shutdown(p)

    # 2. Localizar procesos por línea de comandos y nombre
    procs = get_processes_windows() if is_windows else get_processes_posix()
    pids_to_kill: Set[int] = set()
    identified_info: List[Dict[str, Any]] = []

    for proc in procs:
        pid = proc["pid"]
        if pid == my_pid or pid <= 4:
            continue
        name = proc["name"]
        cmd = proc["cmd"]

        if name in EXCLUDED_PROCESS_NAMES:
            continue

        # Coincidencia con patrones de script
        matches_pattern = any(p in cmd for p in TARGET_PATTERNS)
        # O si es python corriendo dentro del directorio del repo
        matches_python_repo = ("python" in name and "subway-builder-mexico" in cmd.lower())

        if matches_pattern or (include_all and matches_python_repo):
            pids_to_kill.add(pid)
            identified_info.append(proc)

    # 3. Localizar PIDs escuchando en los puertos designados
    if is_windows:
        port_pids = get_pids_on_ports_windows(ports)
        for p, pids in port_pids.items():
            for pid in pids:
                if pid != my_pid and pid > 4:
                    # Verificar que no sea un navegador o proceso del sistema
                    proc_match = next((x for x in procs if x["pid"] == pid), None)
                    pname = proc_match["name"] if proc_match else ""
                    if pname not in EXCLUDED_PROCESS_NAMES:
                        pids_to_kill.add(pid)
                        if proc_match:
                            identified_info.append(proc_match)
                        else:
                            identified_info.append({"pid": pid, "name": f"port_{p}_listener", "cmd": f"Listening on {p}"})

    # 4. Terminar los procesos identificados
    killed_count = 0
    for pid in sorted(pids_to_kill):
        p_info = next((x for x in identified_info if x["pid"] == pid), {"name": "proceso", "cmd": ""})
        desc = p_info.get("cmd") or p_info.get("name")
        if len(desc) > 75:
            desc = desc[:72] + "..."
        log(f"Terminando PID {pid} [{desc}]...", "[STOP]")

        success = kill_pid_windows(pid, force=True) if is_windows else kill_pid_posix(pid, force=True)
        if success:
            killed_count += 1

    # 5. Limpieza de contenedores Docker y subprocesos en WSL
    containers_rm, wsl_killed = cleanup_wsl_and_docker(shutdown_wsl=shutdown_wsl)
    if containers_rm > 0:
        log(f"Se eliminaron {containers_rm} contenedor(es) Docker de OSRM.", "[DOCKER]")
    if wsl_killed > 0:
        log("Se purgaron procesos huérfanos en el subsistema WSL 2.", "[WSL]")

    # 6. Pausa breve para que el sistema operativo libere los sockets
    time.sleep(0.5)

    # 7. Verificación final del estado de los puertos
    blocked_ports = []
    freed_ports = []
    for p in ports:
        if is_port_in_use(p):
            blocked_ports.append(p)
        else:
            freed_ports.append(p)

    return {
        "killed_pids": sorted(list(pids_to_kill)),
        "killed_count": killed_count,
        "containers_removed": containers_rm,
        "freed_ports": freed_ports,
        "blocked_ports": blocked_ports,
        "clean": len(blocked_ports) == 0
    }


def main():
    parser = argparse.ArgumentParser(
        description="Detiene y termina todos los procesos relacionados con el Wizard de Subway Builder México."
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        type=int,
        default=DEFAULT_PORTS,
        help="Puertos a verificar y liberar (default: 8080 5050 5000)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=True,
        help="Terminar todos los procesos de wizard, poi_studio, build y OSRM (activo por defecto)"
    )
    parser.add_argument(
        "--shutdown-wsl",
        action="store_true",
        help="Detener también la máquina virtual WSL 2 por completo (wsl --shutdown)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forzar terminación inmediata sin intentar apagado ordenado HTTP"
    )

    args = parser.parse_args()

    print("================================================================")
    print("  SUBWAY BUILDER MÉXICO - TERMINADOR DE PROCESOS")
    print("  Identidad Gráfica: Metro CDMX / Lance Wyman Standard")
    print("================================================================")

    res = terminate_all_wizard_processes(
        ports=args.ports,
        include_all=args.all,
        shutdown_wsl=args.shutdown_wsl,
        force=args.force
    )

    print("----------------------------------------------------------------")
    if res["killed_count"] > 0 or res["containers_removed"] > 0:
        log(f"Procesos terminados: {res['killed_count']} PID(s).", "[OK]")
    else:
        log("No se encontraron procesos activos del Wizard en ejecución.", "[OK]")

    if res["freed_ports"]:
        log(f"Puertos libres y verificados: {', '.join(str(p) for p in res['freed_ports'])}", "[OK]")

    if res["blocked_ports"]:
        log(f"ADVERTENCIA: Puertos aún ocupados: {', '.join(str(p) for p in res['blocked_ports'])}", "[WARN]")
        sys.exit(1)
    else:
        print("================================================================")
        print("  TODOS LOS PROCESOS DEL WIZARD HAN SIDO TERMINADOS EXITOSAMENTE")
        print("================================================================")
        sys.exit(0)


if __name__ == "__main__":
    main()
