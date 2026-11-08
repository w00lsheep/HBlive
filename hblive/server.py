"""Dashboard-Server: liefert das Dashboard aus und aktualisiert die Daten im Hintergrund.

    python -m hblive.server            # http://localhost:8000
    python -m hblive.server --port 8080 --interval 600

Endpunkte:
    /                 Dashboard (web/)
    /data.json        aktuelle Spieldaten (mit ETag, damit Polling billig bleibt)
    /calendar.ics     alle Spiele als abonnierbarer Kalender
    /api/status       Zustand des Hintergrund-Scrapers
    POST /api/refresh sofort neu laden (max. 1x pro Minute)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ical import build_ics
from .scraper import next_refresh_seconds, scrape, write_json

log = logging.getLogger("hblive.server")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_FILE = ROOT / "data" / "data.json"
MIN_MANUAL_REFRESH = 60


class DataStore:
    """Hält die letzten Daten im Speicher und scrapt periodisch neu."""

    def __init__(self, data_file: Path, idle_interval: int, hot_interval: int):
        self.data_file = data_file
        self.idle_interval = idle_interval
        self.hot_interval = hot_interval
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.data: dict | None = None
        self.body = b""
        self.etag = ""
        self.last_success: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.next_run: datetime | None = None
        if data_file.exists():
            try:
                self._set(json.loads(data_file.read_text(encoding="utf-8")))
                log.info("Vorhandene Daten aus %s geladen", data_file)
            except (OSError, ValueError) as exc:
                log.warning("Konnte %s nicht lesen: %s", data_file, exc)

    def _set(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with self.lock:
            self.data = data
            self.body = body
            self.etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'

    def snapshot(self) -> tuple[bytes, str]:
        with self.lock:
            return self.body, self.etag

    def refresh(self) -> None:
        self.last_attempt = datetime.now(timezone.utc)
        try:
            data = scrape()
            if not data["teams"]:
                raise RuntimeError("keine Mannschaften gefunden")
            self.last_success = datetime.now(timezone.utc)
            self.last_error = None
            interval = next_refresh_seconds(data, idle=self.idle_interval, hot=self.hot_interval)
            self.next_run = self.last_success + timedelta(seconds=interval)
            data["refresh"] = {"interval": interval, "next_at": self.next_run.isoformat(timespec="seconds")}
            self._set(data)
            write_json(data, self.data_file)
            log.info("Daten aktualisiert, nächster Abruf in %ds", interval)
        except Exception as exc:  # noqa: BLE001 – Server soll weiterlaufen
            self.last_error = str(exc)
            self.next_run = datetime.now(timezone.utc) + timedelta(seconds=self.hot_interval)
            log.exception("Aktualisierung fehlgeschlagen")

    def run_forever(self) -> None:
        while True:
            self.refresh()
            wait = max(5.0, (self.next_run - datetime.now(timezone.utc)).total_seconds()) if self.next_run else 60
            self.wake.wait(wait)
            self.wake.clear()

    def request_refresh(self) -> bool:
        if self.last_attempt and (datetime.now(timezone.utc) - self.last_attempt).total_seconds() < MIN_MANUAL_REFRESH:
            return False
        self.wake.set()
        return True

    def status(self) -> dict:
        iso = lambda d: d.isoformat(timespec="seconds") if d else None  # noqa: E731
        return {
            "last_success": iso(self.last_success),
            "last_attempt": iso(self.last_attempt),
            "next_run": iso(self.next_run),
            "last_error": self.last_error,
            "has_data": self.data is not None,
        }


def make_handler(store: DataStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HBlive/1.0"

        def log_message(self, fmt, *args):  # leiser als Standard
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj: dict, status: int = 200) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/data.json":
                body, etag = store.snapshot()
                if not body:
                    return self._json({"error": "Noch keine Daten – erster Abruf läuft"}, 503)
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                return self._send(200, body, "application/json; charset=utf-8", {"ETag": etag, "Cache-Control": "no-cache"})
            if path == "/calendar.ics":
                if store.data is None:
                    return self._json({"error": "Noch keine Daten"}, 503)
                body = build_ics(store.data).encode("utf-8")
                return self._send(200, body, "text/calendar; charset=utf-8", {"Content-Disposition": 'inline; filename="hondelage-herren.ics"'})
            if path == "/api/status":
                return self._json(store.status())
            self._static(path)

        def do_POST(self):
            if self.path.split("?", 1)[0] == "/api/refresh":
                ok = store.request_refresh()
                return self._json({"accepted": ok}, 202 if ok else 429)
            self._json({"error": "not found"}, 404)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            if WEB_DIR not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="HBlive Dashboard-Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=int, default=900, help="Abrufintervall in Sekunden ohne laufendes Spiel (Standard 900)")
    parser.add_argument("--hot-interval", type=int, default=120, help="Abrufintervall rund um eigene Spiele (Standard 120)")
    parser.add_argument("--data", type=Path, default=DATA_FILE)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    store = DataStore(args.data, idle_interval=args.interval, hot_interval=args.hot_interval)
    threading.Thread(target=store.run_forever, name="scraper", daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    log.info("Dashboard läuft auf http://%s:%d", "localhost" if args.host == "0.0.0.0" else args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
