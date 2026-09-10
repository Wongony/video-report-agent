import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from test_queue import wait_for

from video_report_agent.pipeline import create_run, write_json
from video_report_agent.web import create_server


def request(client, base, path, body=None):
    payload = json.dumps(body).encode() if body is not None else None
    try:
        response = client.open(
            Request(base + path, data=payload, headers={"Content-Type": "application/json"})
        )
    except HTTPError as response:
        return response.code, response.read()
    with response:
        return response.status, response.read()


def test_public_owner_isolation_admission_models_and_restart(tmp_path, monkeypatch):
    release = threading.Event()

    def generate(run):
        release.wait(5)
        (run / "assets").mkdir()
        (run / "assets/test.txt").write_text("private asset")
        (run / "report.html").write_text("<html><body>private report</body></html>")
        write_json(
            run / "status.json",
            {
                "run_id": run.name,
                "state": "RENDERED",
                "report_url": f"/reports/{run.name}/report.html",
            },
        )

    monkeypatch.setattr("video_report_agent.web.generate", generate)
    monkeypatch.setattr("video_report_agent.web.catalog", lambda: pytest.fail("public catalog"))
    monkeypatch.setattr(
        "video_report_agent.web.save_model", lambda data: pytest.fail("public save")
    )
    monkeypatch.setattr(
        "video_report_agent.web.check_connection", lambda data: pytest.fail("public connection")
    )
    legacy = create_run(tmp_path, "BV1aTtb6uE7d")
    write_json(legacy / "status.json", {"run_id": legacy.name, "state": "RENDERED"})
    jars = [CookieJar(), CookieJar()]
    a, b = [build_opener(HTTPCookieProcessor(jar)) for jar in jars]
    server = create_server(tmp_path, 0, mode="public", max_queue_length=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert b'data-mode="public"' in request(a, base, "/")[1]
        request(b, base, "/")
        for path, body in [("/api/models", None), ("/api/models", {}), ("/api/models/check", {"model": "injected"})]:
            assert request(a, base, path, body)[0] == 403
        assert (
            request(
                a, base, "/api/visual-report/runs", {"url": "BV1aTtb6uE7d", "provider": "injected"}
            )[0]
            == 403
        )
        endpoint = "/api/visual-report/runs"
        code, first = request(a, base, endpoint, {"url": "BV1aTtb6uE7d", "user_id": "forged"})
        assert code == 202
        first = json.loads(first)["run_id"]
        assert json.loads(request(a, base, "/api/visual-report/current")[1])["run"]["run_id"] == first
        code, duplicate = request(a, base, endpoint, {
            "url": "https://www.bilibili.com/video/BV1aTtb6uE7d/?p=1&share_source=copy"
        })
        assert code == 429
        assert json.loads(duplicate)["error_category"] == "VIDEO_ALREADY_ACTIVE"
        assert len(list(tmp_path.glob("*/queue.json"))) == 1
        code, second = request(a, base, endpoint, {"url": "https://www.bilibili.com/video/BV1aTtb6uE7d/?p=2"})
        assert code == 202
        second = json.loads(second)
        assert second["queue_position"] == 1 and second["queued_ahead"] == 0
        assert second["running_count"] == 1
        code, duplicate = request(a, base, endpoint, {"url": "https://www.bilibili.com/video/BV1aTtb6uE7d/?p=2"})
        assert code == 429
        assert json.loads(duplicate)["error_category"] == "VIDEO_ALREADY_ACTIVE"
        assert request(a, base, endpoint, {"url": "BV1aTtb6uE7d"})[0] == 429
        assert request(b, base, endpoint, {"url": "BV1aTtb6uE7d"})[0] == 429
        assert json.loads(request(b, base, endpoint)[1]) == {"runs": []}
        assert json.loads(request(b, base, "/api/visual-report/current")[1]) == {"run": None}
        assert request(b, base, endpoint + "/" + first)[0] == 404
        assert request(a, base, endpoint + "/" + legacy.name)[0] == 404
        metadata = json.loads((tmp_path / first / "input.json").read_text())
        assert metadata["model_selection"] == {
            "provider": "deepseek",
            "model": "deepseek-v4-flash-vision-exp",
            "thinking": "low",
        }
        owner = json.loads((tmp_path / first / "queue.json").read_text())["owner_id"]
        assert owner != "forged"
        release.set()
        wait_for(
            lambda: json.loads((tmp_path / first / "queue.json").read_text())["state"] == "RENDERED"
        )
        wait_for(
            lambda: json.loads((tmp_path / second["run_id"] / "queue.json").read_text())["state"] == "RENDERED"
        )
        assert json.loads(request(a, base, "/api/visual-report/current")[1]) == {"run": None}
        for path in [f"/reports/{first}/report.html", f"/reports/{first}/assets/test.txt"]:
            assert request(a, base, path)[0] == 200
            assert request(b, base, path)[0] == 404
        assert json.loads(request(b, base, "/api/visual-report/reports")[1]) == {"reports": []}
        assert request(a, base, endpoint, {"url": "BV1aTtb6uE7d"})[0] == 202
        # A copied owner value with an invalid signature cannot access the task.
        forged = build_opener()
        try:
            forged.open(
                Request(
                    base + endpoint + "/" + first, headers={"Cookie": f"vr_owner={owner}.invalid"}
                )
            )
            pytest.fail("forged cookie accepted")
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
    server = create_server(tmp_path, 0, mode="public")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert request(a, base, f"/reports/{first}/report.html")[0] == 200
        assert request(b, base, f"/reports/{first}/report.html")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_local_model_endpoints_remain_available(tmp_path, monkeypatch):
    monkeypatch.setattr("video_report_agent.web.catalog", lambda: {"models": []})
    monkeypatch.setattr("video_report_agent.web.save_model", lambda data: {"saved": True})
    monkeypatch.setattr("video_report_agent.web.check_connection", lambda data: {"ok": True})
    server = create_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        assert b'data-mode="local"' in request(client, base, "/")[1]
        assert request(client, base, "/api/models")[0] == 200
        assert request(client, base, "/api/models", {})[0] == 201
        assert request(client, base, "/api/models/check", {})[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("connected", [True, False])
def test_public_connection_checks_only_fixed_model(tmp_path, monkeypatch, connected):
    selections = []

    def check(selection):
        selections.append(selection)
        return {"connected": connected, "error": "private diagnostic"}

    monkeypatch.setattr("video_report_agent.web.check_connection", check)
    server = create_server(tmp_path, 0, mode="public")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        status, body = request(client, base, "/api/models/check", {})
        assert status == 200
        assert json.loads(body) == {"connected": connected}
        assert selections == [{
            "provider": "deepseek", "model": "deepseek-v4-flash-vision-exp", "thinking": "low",
        }]
        status, _ = request(client, base, "/api/models/check", {"model": "injected"})
        assert status == 403
        assert len(selections) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_public_rejects_ocr_fusion_and_subtitles(tmp_path, monkeypatch):
    monkeypatch.setattr("video_report_agent.web.generate", lambda _: pytest.fail("generation"))
    server = create_server(tmp_path, 0, mode="public")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for extra in [
            {"transcript_mode": "fused"}, {"ocr_mode": "auto"}, {"ocr_mode": "roi"},
            {"ocr_roi": "0,0,1,1"}, {"subtitle_content": "text"},
            {"subtitle_file": "subtitle.srt"},
        ]:
            assert request(client, base, "/api/visual-report/runs", {
                "url": "BV1aTtb6uE7d", **extra,
            })[0] == 403
        assert not list(tmp_path.glob("*/input.json"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_public_rejects_recovered_fusion_task(tmp_path, monkeypatch):
    run = create_run(tmp_path, "BV1aTtb6uE7d", transcript_mode="fused")
    write_json(run / "queue.json", {
        "owner_id": "test-owner", "queue_seq": 1, "state": "QUEUED",
    })
    monkeypatch.setattr("video_report_agent.web.generate", lambda _: pytest.fail("generation"))
    server = create_server(tmp_path, 0, mode="public")
    try:
        wait_for(lambda: json.loads((run / "queue.json").read_text())["state"] == "FAILED")
        assert "Public" in json.loads((run / "status.json").read_text())["error"]
    finally:
        server.server_close()
