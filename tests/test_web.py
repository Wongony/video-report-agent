import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from video_report_agent.pipeline import create_run, write_json
from video_report_agent.web import create_server


def test_local_ui_status_report_and_file_boundary(tmp_path):
    run = create_run(tmp_path, "https://www.bilibili.com/video/BV1aTtb6uE7d/")
    (run / "report.html").write_text("<html><body>report</body></html>")
    (run / "private.txt").write_text("private")
    (run / "assets").mkdir()
    write_json(
        run / "status.json",
        {"run_id": run.name, "state": "RENDERED", "report_url": f"/reports/{run.name}/report.html"},
    )
    comparison = tmp_path / "asr-compare-example"
    comparison.mkdir()
    comparison_status = {"state": "COMPLETE", "stage": "COMPARISON_READY"}
    write_json(comparison / "status.json", comparison_status)
    server = create_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert json.loads((comparison / "status.json").read_text()) == comparison_status
        with urlopen(base + "/api/visual-report/current") as response:
            assert json.load(response) == {"run": None}
        with urlopen(base + f"/api/visual-report/runs/{run.name}") as response:
            status = json.load(response)
            assert status["state"] == "RENDERED"
            assert status["image_url"] == f"/reports/{run.name}/report.png"
        with urlopen(base) as response:
            page = response.read().decode()
            assert 'value="asr-only" selected' in page
            assert 'value="off" selected' in page
            assert "transcriptMode.value" in page
        with urlopen(base + "/api/visual-report/reports") as response:
            assert len(json.load(response)["reports"]) == 1
        with urlopen(base + f"/reports/{run.name}/report.html") as response:
            assert "sandbox" in response.headers["Content-Security-Policy"]
        with urlopen(base + f"/reports/{run.name}/report.png") as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.read().startswith(b"\x89PNG")
        assert (run / "report.png").is_file()
        for path in [
            f"/reports/{run.name}/private.txt",
            f"/reports/{run.name}/assets/../private.txt",
        ]:
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 404
        request = Request(
            base + "/api/visual-report/runs",
            data=json.dumps({"url": "https://invalid.test"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_mode_validation_before_workspace_creation(tmp_path):
    with pytest.raises(ValueError):
        create_run(
            tmp_path, "https://www.bilibili.com/video/BV1aTtb6uE7d/", transcript_mode="invalid"
        )
    assert not list(tmp_path.iterdir())


def test_bvid_input_uses_same_video_identity_as_url(tmp_path):
    bvid_run = create_run(tmp_path, "  BV1aTtb6uE7d\n")
    url_run = create_run(tmp_path, "https://www.bilibili.com/video/BV1aTtb6uE7d/")
    assert json.loads((bvid_run / "input.json").read_text()) == json.loads(
        (url_run / "input.json").read_text()
    )


@pytest.mark.parametrize("value", ["BV123", "BV1aTtb6uE7d/extra", "BV1aTtb6uE7d?p=2"])
def test_invalid_bvid_does_not_create_run(tmp_path, value):
    from video_report_agent.ingest import UrlIngestError

    with pytest.raises(UrlIngestError):
        create_run(tmp_path, value)
    assert not list(tmp_path.iterdir())


def test_model_selection_is_saved_and_invalid_thinking_rejected(tmp_path, monkeypatch):
    from video_report_agent import model_config

    monkeypatch.setattr(model_config, "catalog", lambda: {"models": [{
        "provider": "test", "model": "test-model", "configured": True,
        "thinking_levels": ["off", "high"],
    }]})
    monkeypatch.setattr("video_report_agent.web.generate", lambda run: None)
    server = create_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api/visual-report/runs"
    payload = {"url": "BV1aTtb6uE7d", "provider": "test", "model": "test-model",
               "thinking": "high"}
    try:
        with urlopen(Request(endpoint, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})) as response:
            run_id = json.load(response)["run_id"]
        metadata = json.loads((tmp_path / run_id / "input.json").read_text())
        assert metadata["model_selection"] == {
            "provider": "test", "model": "test-model", "thinking": "high",
        }
        payload["thinking"] = "max"
        with pytest.raises(HTTPError) as error:
            urlopen(Request(endpoint, data=json.dumps(payload).encode()))
        assert error.value.code == 400
        assert len(list(tmp_path.glob("*/input.json"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_all_history_survives_media_retention(tmp_path, monkeypatch):
    from video_report_agent.retention import cleanup_media

    monkeypatch.setenv("MEDIA_KEEP_LAST", "20")
    for i in range(25):
        run = tmp_path / f"history-{i:02}"
        (run / "download").mkdir(parents=True)
        (run / "download/source.m4a").write_bytes(b"audio")
        (run / "report.html").write_text("report")
        write_json(run / "status.json", {
            "run_id": run.name, "state": "RENDERED",
            "report_url": f"/reports/{run.name}/report.html",
        })
    cleanup_media(tmp_path)
    assert len(list(tmp_path.glob("*/download/source.m4a"))) == 20
    server = create_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for endpoint, key in [("runs", "runs"), ("reports", "reports")]:
            with urlopen(base + "/api/visual-report/" + endpoint) as response:
                assert len(json.load(response)[key]) == 25
        with urlopen(base + "/reports/history-00/report.html") as response:
            assert response.read() == b"report"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
