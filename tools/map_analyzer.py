#!/usr/bin/env python3
"""Standalone, read-only analyzer for Subway Builder demand JSON and map ZIPs."""

import argparse
import gzip
import json
import math
import secrets
import time
from pathlib import Path, PurePosixPath
import tempfile
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


TOOLS = Path(__file__).resolve().parent
MAX_UPLOAD = 512 * 1024 * 1024
MAX_CONFIG = 4 * 1024 * 1024
RESPONSE_PART = 16 * 1024
IMPORT_RESULTS = {}
IMPORT_LOCK = threading.Lock()


def store_result(body):
    with IMPORT_LOCK:
        now = time.monotonic()
        for token, (created, _) in list(IMPORT_RESULTS.items()):
            if now - created > 600:
                del IMPORT_RESULTS[token]
        if len(IMPORT_RESULTS) >= 2 or sum(len(v[1]) for v in IMPORT_RESULTS.values()) + len(body) > MAX_UPLOAD:
            raise ValueError("Other imports are still being received. Try again shortly.")
        token = secrets.token_urlsafe(24)
        IMPORT_RESULTS[token] = (now, body)
        return dict(token=token, bytes=len(body), partBytes=RESPONSE_PART)


def read_json(stream):
    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.load(stream, parse_constant=invalid)


def count(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < 0 or value > 2**53 - 1 or not math.isfinite(value) or int(value) != value):
        raise ValueError(f"{label} must be a nonnegative whole number.")
    return int(value)


def normalize(demand, config=None, name="Imported map"):
    """Keep analysis fields only; validate references without changing counts."""
    if not isinstance(demand, dict) or not isinstance(demand.get("points"), list):
        raise ValueError("Expected a demand_data.json object with a points array.")
    if not demand["points"]:
        raise ValueError("This map has no demand points to analyze.")
    points, ids = [], set()
    for index, point in enumerate(demand["points"]):
        if not isinstance(point, dict):
            raise ValueError(f"Point {index} must be an object.")
        identity = point.get("id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError(f"Point {index} has a missing or duplicate string ID.")
        location = point.get("location")
        if (not isinstance(location, list) or len(location) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in location)
                or not -180 <= location[0] <= 180 or not -85 <= location[1] <= 85):
            raise ValueError(f"Point {identity}: expected [longitude, latitude] within ±180°, ±85°.")
        points.append(dict(id=identity, location=location,
                           residents=count(point.get("residents"), f"{identity}.residents"),
                           jobs=count(point.get("jobs"), f"{identity}.jobs")))
        ids.add(identity)
    trips_available = "pops" in demand
    raw_pops = demand.get("pops", [])
    if not isinstance(raw_pops, list):
        raise ValueError("pops must be an array when supplied.")
    pops = []
    outgoing, incoming = dict.fromkeys(ids, 0), dict.fromkeys(ids, 0)
    for index, pop in enumerate(raw_pops):
        if not isinstance(pop, dict):
            raise ValueError(f"Trip group {index} must be an object.")
        origin, destination = pop.get("residenceId"), pop.get("jobId")
        if not isinstance(origin, str) or not isinstance(destination, str) or origin not in ids or destination not in ids:
            raise ValueError(f"Trip group {index} references a missing point.")
        size = count(pop.get("size"), f"Trip group {index}.size")
        if not size:
            raise ValueError(f"Trip group {index}.size must be positive.")
        pops.append(dict(residenceId=origin, jobId=destination, size=size))
        outgoing[origin] += size
        incoming[destination] += size
    warnings = []
    if trips_available:
        mismatch = sum(p["residents"] != outgoing[p["id"]] or p["jobs"] != incoming[p["id"]] for p in points)
        if mismatch:
            warnings.append(f"{mismatch:,} points have display counts that differ from trip totals. Heatmaps use the supplied point counts; flows use trip group sizes.")
    else:
        warnings.append("No pops array: heatmaps are available, but trip flows are unavailable.")
    for field in ("residents", "jobs"):
        count(sum(p[field] for p in points), f"Total {field}")
    count(sum(p["size"] for p in pops), "Total commuters")
    title = config.get("name") if isinstance(config, dict) else None
    return dict(name=title if isinstance(title, str) and title else name,
                points=points, pops=pops, tripsAvailable=trips_available, warnings=warnings)


def load_map(stream, name="Imported map"):
    """Read selected ZIP members in memory; never extract or modify a map."""
    if not zipfile.is_zipfile(stream):
        stream.seek(0)
        return normalize(read_json(stream), name=name)
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        candidates = [m for m in archive.infolist()
                      if PurePosixPath(m.filename).name == "demand_data.json" and not m.is_dir()]
        if len(candidates) != 1:
            raise ValueError("The ZIP must contain exactly one demand_data.json. Import separate maps individually.")
        member = candidates[0]
        if member.file_size > MAX_UPLOAD:
            raise ValueError("The uncompressed demand JSON exceeds the 512 MiB import limit.")
        parent = PurePosixPath(member.filename).parent
        configs = [m for m in archive.infolist() if m.filename == str(parent / "config.json")]
        config = None
        if len(configs) > 1:
            raise ValueError("The ZIP contains duplicate config.json members.")
        if configs:
            if configs[0].file_size > MAX_CONFIG:
                raise ValueError("config.json exceeds the 4 MiB limit.")
            with archive.open(configs[0]) as entry:
                config = read_json(entry)
        with archive.open(member) as entry:
            return normalize(read_json(entry), config, name)


class AnalyzerHandler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        # Browsers advertise gzip for loopback requests too. Keep identity responses
        # for clients that do not support it; never alter the source assets.
        accepts_gzip = any(
            parts[0].strip().lower() == "gzip"
            and not any(p.strip().lower().replace(" ", "") in ("q=0", "q=0.0", "q=0.00", "q=0.000") for p in parts[1:])
            for parts in (item.split(";") for item in self.headers.get("Accept-Encoding", "").split(","))
        )
        compressed = accepts_gzip and len(body) >= 1024
        if compressed:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/import-result/"):
            if self.headers.get("X-Map-Analyzer") != "1":
                self.reply(403, b'{"error":"Use the local analyzer."}')
                return
            parts = path.split("/")
            try:
                token, index = parts[3], int(parts[4])
                if len(parts) != 5 or index < 0:
                    raise ValueError()
                with IMPORT_LOCK:
                    created, body = IMPORT_RESULTS[token]
                    if time.monotonic() - created > 600:
                        del IMPORT_RESULTS[token]
                        raise KeyError(token)
                    start = index * RESPONSE_PART
                    if start >= len(body):
                        raise ValueError()
                    part = body[start:start + RESPONSE_PART]
                self.reply(200, part, "application/octet-stream")
            except (ValueError, KeyError, IndexError):
                self.reply(404, b'{"error":"Import result expired or part missing. Reopen the map."}')
            return
        fixed = {"/": TOOLS / "templates/map_analyzer.html",
                 "/core.js": TOOLS / "static/map-analyzer/core.js",
                 "/import.js": TOOLS / "static/map-analyzer/import.js",
                 "/app.js": TOOLS / "static/map-analyzer/app.js",
                 "/style.css": TOOLS / "static/map-analyzer/style.css",
                 "/leaflet.js": TOOLS / "static/wizard/vendor/leaflet.js",
                 "/leaflet.css": TOOLS / "static/wizard/vendor/leaflet.css"}
        target = fixed.get(path)
        if target is None:
            self.reply(404, b'{"error":"Not found"}')
            return
        mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}[target.suffix]
        self.reply(200, target.read_bytes(), mime + "; charset=utf-8")

    def do_POST(self):
        if self.path not in ("/api/import", "/api/import?response=parts"):
            self.reply(404, b'{"error":"Not found"}')
            return
        # Accept imports only from this local UI, not other websites.
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Origin") != expected or self.headers.get("X-Map-Analyzer") != "1":
            self.reply(403, b'{"error":"Import maps from the local analyzer window."}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD:
                raise ValueError("Choose a nonempty map ZIP or demand JSON up to 512 MiB.")
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("The upload ended before the entire file arrived.")
                    stream.write(chunk)
                    remaining -= len(chunk)
                stream.seek(0)
                result = load_map(stream)
            body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if self.path.endswith("?response=parts"):
                body = json.dumps(store_result(body)).encode("utf-8")
            self.reply(200, body)
        except (ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
            self.reply(400, json.dumps(dict(error=str(error))).encode("utf-8"))

    def do_DELETE(self):
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Origin") != expected or self.headers.get("X-Map-Analyzer") != "1":
            self.reply(403, b'{}')
            return
        prefix = "/api/import-result/"
        if not self.path.startswith(prefix):
            self.reply(404, b'{}')
            return
        with IMPORT_LOCK:
            IMPORT_RESULTS.pop(self.path[len(prefix):], None)
        self.reply(200, b'{}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AnalyzerHandler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Map Analyzer: {url}\nRead-only imports. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
