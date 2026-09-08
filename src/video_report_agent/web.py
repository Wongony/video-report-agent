"""Existing local UI with a single worker and file-backed run results."""

from __future__ import annotations

import json
import mimetypes
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .ingest import UrlIngestError
from .model_config import catalog, check_connection, save_model, validate_selection
from .pipeline import create_run, generate, write_json
from .retention import cleanup_media
from .transcript_foundation import TranscriptFoundationError

PAGE = Path(__file__).parent / "static" / "index.html"


def create_server(root: Path, port: int = 8765) -> ThreadingHTTPServer:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    worker = ThreadPoolExecutor(max_workers=1)

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html") and self.path.startswith("/reports/"):
                self.send_header(
                    "Content-Security-Policy",
                    "sandbox allow-scripts; default-src 'none'; "
                    "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                    "img-src 'self' data:; font-src data:",
                )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/models":
                try:
                    return self.send(200, catalog())
                except ValueError as exc:
                    return self.send(503, {"error": str(exc)})
            statuses = [
                json.loads(p.read_text())
                for p in sorted(
                    root.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True
                )
            ]
            statuses = [s for s in statuses if s.get("run_id")]
            if path == "/api/visual-report/current":
                return self.send(200, {"run": statuses[0] if statuses else None})
            if path == "/api/visual-report/reports":
                return self.send(
                    200, {"reports": [s for s in statuses if s["state"] == "RENDERED"]}
                )
            prefix = "/api/visual-report/runs/"
            if path.startswith(prefix):
                item = next((s for s in statuses if s["run_id"] == path[len(prefix) :]), None)
                return self.send(200 if item else 404, item or {"error": "Run not found"})
            if path.startswith("/reports/"):
                relative = Path(path[len("/reports/") :])
                target = (root / relative).resolve()
                parts = relative.parts
                allowed = len(parts) >= 2 and (parts[1] == "report.html" or parts[1] == "assets")
                if allowed:
                    run_root = root / parts[0]
                    allowed = target == run_root / "report.html" or target.is_relative_to(
                        run_root / "assets"
                    )
                if allowed and target.is_relative_to(root) and target.is_file():
                    run_status = root / parts[0] / "status.json"
                    if (
                        run_status.is_file()
                        and json.loads(run_status.read_text())["state"] == "RENDERED"
                    ):
                        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                        return self.send(200, target.read_bytes(), mime)
            self.send(404, {"error": "Not found"})

        def do_POST(self):
            if self.path not in {"/api/visual-report/runs", "/api/models", "/api/models/check"}:
                return self.send(404, {"error": "Not found"})
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self.send(403, {"error": "Cross-origin request rejected"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object")
                if self.path == "/api/models/check":
                    return self.send(200, check_connection(data))
                if self.path == "/api/models":
                    return self.send(201, save_model(data))
                selection = validate_selection(data)
                if data.get("subtitle_content") is not None:
                    suffix = Path(data.get("subtitle_name") or "").suffix.lower()
                    if suffix not in {".srt", ".vtt", ".ass"} or not isinstance(
                        data["subtitle_content"], str
                    ):
                        raise ValueError("Subtitle must be SRT, VTT or ASS text")
                run = create_run(
                    root,
                    data["url"],
                    transcript_mode=data.get("transcript_mode", "asr-only"),
                    ocr_mode=data.get("ocr_mode", "off"),
                    ocr_roi=data.get("ocr_roi"),
                    model_selection=selection,
                )
                if data.get("subtitle_content") is not None:
                    subtitle = run / ("subtitle" + suffix)
                    subtitle.write_text(data["subtitle_content"])
                    metadata = json.loads((run / "input.json").read_text())
                    metadata["subtitle_file"] = subtitle.name
                    write_json(run / "input.json", metadata)
            except (
                ValueError,
                KeyError,
                TypeError,
                UrlIngestError,
                TranscriptFoundationError,
            ) as exc:
                return self.send(400, {"error_category": "URL_INVALID", "error": str(exc)})
            worker.submit(generate, run)
            self.send(202, json.loads((run / "status.json").read_text()))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    original_close = server.server_close

    def close():
        original_close()
        worker.shutdown(wait=True)

    server.server_close = close
    # Interrupted processes cannot resume an in-memory job after a restart.
    for path in root.glob("*/status.json"):
        data = json.loads(path.read_text())
        if not data.get("run_id"):
            continue
        if data["state"] not in {"RENDERED", "FAILED"}:
            data.update(
                finished_at=time.time(),
                state="FAILED",
                stage="FAILED",
                error_category="ENVIRONMENT_FAILURE",
                error="Server stopped before this run finished",
            )
            write_json(path, data)
    cleanup_media(root)
    return server
