"""Local/public Web UI with an owner-scoped persistent task queue."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .analytics import Analytics, number
from .execution import generate
from .ingest import UrlIngestError, validate_bilibili_url
from .model_config import catalog, check_connection, save_model, validate_selection
from .pi import DEFAULT_MODEL, DEFAULT_PROVIDER, DEFAULT_THINKING
from .pipeline import create_run, write_json
from .queue import AdmissionError, RunQueue
from .report_image import REPORT_CSP, render_report_image
from .retention import cleanup_media
from .session import OwnerSessions
from .transcript_foundation import TranscriptFoundationError

PAGE = Path(__file__).parent / "static" / "index.html"


def create_server(
    root: Path,
    port: int = 8765,
    *,
    mode="local",
    host="127.0.0.1",
    max_concurrency=1,
    max_active_per_owner=2,
    max_queue_length=20,
    public_origin=None,
) -> ThreadingHTTPServer:
    if mode not in {"local", "public"}:
        raise ValueError("mode must be local or public")
    public_model = {
        "provider": os.getenv("PI_PROVIDER", DEFAULT_PROVIDER),
        "model": os.getenv("PI_MODEL", DEFAULT_MODEL),
        "thinking": DEFAULT_THINKING,
    }
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    rate = os.getenv("ASR_CNY_PER_SECOND", "").strip()
    asr_rate = float(rate) if rate else None
    if asr_rate is not None and not number(asr_rate):
        raise ValueError("ASR_CNY_PER_SECOND must be a non-negative finite number")
    if public_origin is not None:
        origin = urlsplit(public_origin)
        if origin.scheme not in {"http", "https"} or not origin.netloc or origin.path:
            raise ValueError("public_origin must be an HTTP(S) origin without a path")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def execute(run):
        # Apply the policy to recovered queued tasks as well as new HTTP submissions.
        if mode == "public":
            metadata = json.loads((run / "input.json").read_text())
            if (
                metadata.get("transcript_mode", "asr-only") != "asr-only"
                or metadata.get("ocr_mode", "off") != "off"
                or metadata.get("subtitle_file") is not None
            ):
                raise ValueError("Public 仅支持 ASR，已关闭 OCR 和融合。")
        return generate(run)

    queue = RunQueue(
        root,
        execute,
        max_concurrency=max_concurrency,
        max_active_per_owner=max_active_per_owner,
        max_queue_length=max_queue_length,
    )
    try:
        sessions = OwnerSessions(
            root, secure=bool(public_origin and public_origin.startswith("https://"))
        )
        analytics = Analytics(root, asr_rate=asr_rate)
    except Exception:
        queue.close()
        raise

    class Handler(BaseHTTPRequestHandler):
        def identify(self):
            self.owner, self.owner_cookie = sessions.identify(self.headers.get("Cookie"))

        def statuses(self, *, shared_reports=False):
            statuses = queue.statuses(self.owner, shared_reports=shared_reports)
            # Public history also includes completed runs created before owner tracking.
            if mode == "local" or shared_reports:
                for path in root.glob("*/status.json"):
                    if (path.parent / "queue.json").exists():
                        continue
                    try:
                        item = json.loads(path.read_text())
                        if item.get("run_id") and (mode == "local" or item.get("state") == "RENDERED"):
                            statuses.append(item)
                    except (OSError, ValueError):
                        continue
            for item in statuses:
                if item.get("state") == "RENDERED" and item.get("report_url"):
                    item["image_url"] = f"/reports/{item['run_id']}/report.png"
                item.pop("download_reused_from", None)
                item.pop("transcript_reused_from", None)
            return statuses

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if self.command == "POST" and self.path == "/api/visual-report/runs":
                analytics.record("submit", self.owner, http_status=status)
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            if self.owner_cookie:
                self.send_header("Set-Cookie", self.owner_cookie)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html") and self.path.startswith("/reports/"):
                self.send_header(
                    "Content-Security-Policy",
                    REPORT_CSP,
                )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == "/healthz":
                self.owner_cookie = None
                return self.send(200, {"status": "ok"})
            self.identify()
            if path == "/":
                analytics.record("visit", self.owner)
                page = PAGE.read_text().replace('data-mode="local"', f'data-mode="{mode}"')
                if mode == "public":
                    page = page.replace("Video Visual Report", "Video Visual Report · Public Queue").replace(
                        "本地会依次", "服务端会依次"
                    )
                return self.send(200, page.encode(), "text/html; charset=utf-8")
            if path in {"/admin", "/api/admin/stats"}:
                if not admin_token:
                    return self.send(404, {"error": "Admin statistics are disabled"})
                if path == "/admin":
                    return self.send(
                        200, PAGE.with_name("admin.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(
                    supplied.encode(), f"Bearer {admin_token}".encode()
                ):
                    return self.send(401, {"error": "管理员密钥不正确"})
                query = parse_qs(urlsplit(self.path).query)
                try:
                    result = analytics.snapshot(
                        query.get("start", [None])[0], query.get("end", [None])[0]
                    )
                except ValueError:
                    return self.send(400, {"error": "请使用有效日期，开始日期不能晚于结束日期"})
                return self.send(200, result)
            if path == "/api/models":
                if mode == "public":
                    return self.send(403, {"error": "Model configuration is disabled"})
                try:
                    return self.send(200, catalog())
                except ValueError as exc:
                    return self.send(503, {"error": str(exc)})
            statuses = self.statuses(shared_reports=(
                mode == "public"
                and (path == "/api/visual-report/reports" or path.startswith("/reports/"))
            ))
            if path == "/api/visual-report/current":
                active = [s for s in statuses if s["state"] in {"QUEUED", "RUNNING"}]
                return self.send(200, {"run": (active or [None])[0]})
            if path == "/api/visual-report/runs":
                return self.send(200, {"runs": statuses})
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
                allowed = (
                    len(parts) >= 2
                    and parts[0] in {s["run_id"] for s in statuses if s["state"] == "RENDERED"}
                    and (parts[1] in {"report.html", "report.png", "assets"})
                )
                if allowed:
                    run_root = root / parts[0]
                    allowed = (
                        target in {run_root / "report.html", run_root / "report.png"}
                        or target.is_relative_to(run_root / "assets")
                    )
                if allowed and target == root / parts[0] / "report.png" and not target.exists():
                    try:
                        render_report_image(root / parts[0])
                    except Exception:
                        return self.send(
                            503, "长图生成失败，请刷新重试。HTML 报告仍可打开。".encode(),
                            "text/plain; charset=utf-8",
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
            self.identify()
            if self.path not in {"/api/visual-report/runs", "/api/models", "/api/models/check"}:
                return self.send(404, {"error": "Not found"})
            if mode == "public" and self.path == "/api/models":
                return self.send(403, {"error": "Model configuration is disabled"})
            expected_origin = public_origin or f"http://{self.headers.get('Host')}"
            if self.headers.get("Origin") not in (None, expected_origin):
                return self.send(403, {"error": "Cross-origin request rejected"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object")
                if self.path == "/api/models/check":
                    if mode == "public":
                        if data:
                            return self.send(403, {"error": "Public model configuration is fixed"})
                        result = check_connection(public_model.copy())
                        return self.send(200, {"connected": result["connected"]})
                    return self.send(200, check_connection(data))
                if self.path == "/api/models":
                    return self.send(201, save_model(data))
                if mode == "public" and any(
                    key in data
                    for key in ("provider", "model", "thinking", "api_key", "model_selection")
                ):
                    return self.send(
                        403, {"error": "Public tasks use the server model configuration"}
                    )
                selection = validate_selection(data) if mode == "local" else public_model.copy()
                if mode == "public" and (
                    data.get("transcript_mode", "asr-only") != "asr-only"
                    or data.get("ocr_mode", "off") != "off"
                    or data.get("ocr_roi") is not None
                    or data.get("subtitle_content") is not None
                    or data.get("subtitle_file") is not None
                ):
                    return self.send(403, {"error": "Public 仅支持 ASR，已关闭 OCR 和融合。"})
                if data.get("subtitle_content") is not None:
                    suffix = Path(data.get("subtitle_name") or "").suffix.lower()
                    if suffix not in {".srt", ".vtt", ".ass"} or not isinstance(
                        data["subtitle_content"], str
                    ):
                        raise ValueError("Subtitle must be SRT, VTT or ASS text")

                def create():
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
                    return run

                run = queue.submit(
                    self.owner, create, video_id=validate_bilibili_url(data["url"]).video_id
                )
            except AdmissionError as exc:
                messages = {
                    "VIDEO_ALREADY_ACTIVE": "这个视频正在生成或排队中，请等待任务结束后再重新生成。",
                    "USER_ACTIVE_LIMIT": "你的待处理任务已达上限，请等待任务完成。",
                    "QUEUE_FULL": "等待队列已满，请稍后再提交。",
                    "SERVER_STOPPING": "服务正在关闭，请稍后重试。",
                }
                return self.send(429, {"error_category": str(exc), "error": messages[str(exc)]})
            except (
                ValueError,
                KeyError,
                TypeError,
                UrlIngestError,
                TranscriptFoundationError,
            ) as exc:
                return self.send(400, {"error_category": "URL_INVALID", "error": str(exc)})
            self.send(202, next(s for s in self.statuses() if s["run_id"] == run.name))

    try:
        server = ThreadingHTTPServer((host, port), Handler)
        cleanup_media(root)
        queue.start()
    except Exception:
        queue.close()
        raise
    original_close = server.server_close
    original_shutdown = server.shutdown

    def shutdown():
        queue.stop()
        original_shutdown()

    def close():
        queue.stop()
        original_close()
        queue.close()

    server.shutdown = shutdown
    server.server_close = close
    return server
