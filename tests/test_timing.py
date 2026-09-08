import json

from video_report_agent import pipeline
from video_report_agent.web import create_server


def test_failed_run_preserves_start_and_records_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.time, "time", lambda: 1000)
    run = pipeline.create_run(tmp_path, "BV1aTtb6uE7d")
    monkeypatch.setattr(pipeline.time, "time", lambda: 1073)

    def fail(*args, **kwargs):
        raise OSError("test download failure")

    monkeypatch.setattr(pipeline, "reuse_download", fail)
    status = pipeline.generate(run)
    assert status["state"] == "FAILED"
    assert status["started_at"] == 1000
    assert status["finished_at"] == 1073


def test_restart_freezes_interrupted_timer(tmp_path):
    run = pipeline.create_run(tmp_path, "BV1aTtb6uE7d")
    server = create_server(tmp_path, 0)
    server.server_close()
    status = json.loads((run / "status.json").read_text())
    assert status["state"] == "FAILED"
    assert status["finished_at"] >= status["started_at"]
