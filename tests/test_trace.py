import json

import pytest

from video_report_agent import pipeline
from video_report_agent.trace import RunTrace


def test_span_failure_and_redaction(tmp_path):
    with pytest.raises(ValueError):
        with RunTrace(tmp_path).span("asr", backend="test", api_key="secret"):
            raise ValueError("request https://example.com/private?token=x sk-secret failed")
    lines = (tmp_path / "run.trace.jsonl").read_text().splitlines()
    start, end = map(json.loads, lines)
    assert start["span_id"] == end["span_id"]
    assert end["status"] == "error"
    assert end["elapsed_ms"] >= 0
    assert "secret" not in "".join(lines)
    assert "example.com" not in "".join(lines)


def test_pipeline_download_failure_closes_span(tmp_path, monkeypatch):
    run = pipeline.create_run(tmp_path, "BV1aTtb6uE7d")

    def fail(*args, **kwargs):
        raise OSError("download failed")

    monkeypatch.setattr(pipeline, "reuse_download", fail)
    assert pipeline.generate(run)["state"] == "FAILED"
    events = [json.loads(line) for line in (run / "run.trace.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["stage_start", "stage_end"]
    assert events[-1]["stage"] == "download"
    assert events[-1]["status"] == "error"
