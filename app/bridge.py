"""Authenticated browser host bridge, bound exclusively to IPv4 loopback."""
import base64
import hmac
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from core import hostname

PORT = 17863


class BrowserState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reports = {}
        self.icon_dir = None      # set by the app; None disables site icons
        self.icon_version = 0     # bumped whenever a new site icon is saved

    def put_icon(self, report):
        """Store a site icon sent by the extension: a PNG of at most 30 KB, one file per host."""
        host, data = report.get("host"), report.get("png")
        if not isinstance(host, str) or not host or len(host) > 253 or hostname(host) != host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
            raise ValueError("invalid hostname")
        if not isinstance(data, str) or len(data) > 40000:
            raise ValueError("invalid icon")
        png = base64.b64decode(data, validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 30000:
            raise ValueError("not a png")
        if self.icon_dir is None:
            return
        folder = Path(self.icon_dir); folder.mkdir(parents=True, exist_ok=True)
        if len(list(folder.glob("*.png"))) >= 1000:
            return
        target = folder/f"{host}.png"; temp = target.with_suffix(".tmp")
        temp.write_bytes(png); temp.replace(target)
        with self.lock:
            self.icon_version += 1

    def put(self, report):
        browser = report.get("browser")
        if browser not in ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe"):
            raise ValueError("unsupported browser")
        host = report.get("host", "")
        title = report.get("title", "")
        if not isinstance(host, str) or len(host) > 253 or not isinstance(title, str) or len(title) > 2048:
            raise ValueError("invalid report")
        if host and (hostname(host) != host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host)):
            raise ValueError("invalid hostname")
        with self.lock:
            self.reports[browser] = {"host": host, "title": title, "focused": report.get("focused") is True, "at": time.monotonic()}

    def current(self, browser, title):
        with self.lock:
            report = self.reports.get(browser)
            if report and report["focused"] and time.monotonic()-report["at"] <= 45 and report["title"] == title:
                return report["host"]
        return ""

    def connected(self):
        with self.lock:
            return any(time.monotonic()-x["at"] < 45 for x in self.reports.values())


def start_bridge(state, token, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}" or self.path not in ("/api/browser", "/api/icon"):
                return self.respond(404, {})
            if not hmac.compare_digest(self.headers.get("X-Focus-Token", ""), token):
                return self.respond(403, {})
            if self.headers.get("Origin") and not self.headers["Origin"].startswith("chrome-extension://"):
                return self.respond(403, {})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (45000 if self.path == "/api/icon" else 8192):
                    return self.respond(413, {})
                self.connection.settimeout(2)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError()
                (state.put_icon if self.path == "/api/icon" else state.put)(payload)
            except (ValueError, TypeError, OSError):
                return self.respond(400, {})
            self.respond(200, {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
