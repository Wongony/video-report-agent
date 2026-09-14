
from video_report_agent import pipeline


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
