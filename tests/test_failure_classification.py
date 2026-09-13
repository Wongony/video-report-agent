import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from video_report_agent.analytics import Analytics
from video_report_agent.failures import asr_failure
from video_report_agent.ingest import UrlIngestError, download_bilibili_video, validate_bilibili_url
from video_report_agent.paraformer import CloudAsrError, ParaformerBackend
from video_report_agent.pipeline import write_json


def test_silent_duration_filter_exit(tmp_path, monkeypatch):
    monkeypatch.setattr("video_report_agent.ingest.shutil.which", lambda _: "/bin/yt-dlp")
    with pytest.raises(UrlIngestError) as error:
        download_bilibili_video(
            validate_bilibili_url("BV1aTtb6uE7d"),
            tmp_path,
            runner=lambda *a, **kw: SimpleNamespace(returncode=101, stdout="", stderr=""),
        )
    assert error.value.category == "VIDEO_DURATION_INVALID"
    assert "3 小时" in str(error.value)


@pytest.mark.parametrize(
    "http,code,expected",
    [
        (400, "Arrearage", "ASR_BILLING_UNAVAILABLE"),
        (429, "Throttling", "ASR_RATE_LIMITED"),
        (403, "AccessDenied", "ASR_ACCESS_DENIED"),
        (400, "InvalidParameter", "ASR_SERVICE_ERROR"),
    ],
)
def test_asr_http_diagnostics_are_safe(tmp_path, monkeypatch, http, code, expected):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-secret")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    def handle(request):
        return httpx.Response(
            http, json={"code": code, "message": "sk-secret https://signed.test/?secret=value"}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CloudAsrError) as error:
            ParaformerBackend(client=client)._run(client, audio, "zh")
    assert error.value.http_status == http
    assert error.value.provider_code == code
    assert asr_failure(error.value)[0] == expected
    assert "secret" not in json.dumps(error.value.to_dict())


def test_audited_history_and_trends(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "audit", Path(__file__).parents[1] / "scripts/reclassify_failure_audit_20260913.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    for prefix, (video, kind) in audit.AUDITED.items():
        run = tmp_path / prefix
        run.mkdir()
        write_json(run / "input.json", {"video_id": "bilibili-" + video})
        write_json(
            run / "status.json",
            {
                "run_id": prefix,
                "state": "FAILED",
                "error_category": "EXTERNAL_API_FAILURE",
                "error": "yt-dlp failed"
                if kind == "duration"
                else "Paraformer submit: HTTPStatusError",
                "started_at": 1789228800,
            },
        )
    assert all(result == "classified" for _, result in audit.reclassify(tmp_path))
    assert all(result == "already classified" for _, result in audit.reclassify(tmp_path))
    data = Analytics(tmp_path).snapshot()
    assert data["failure_groups"] == {"input": 6, "external": 5}
    assert data["summary"]["failed_tasks"] == 11
    assert data["summary"]["success_rate"] == 0
    assert data["trends"]["daily"][0]["input"] == 6
    assert data["trends"]["weekly"][0]["external"] == 5
    assert all(r["provider_code"] is None for r in data["runs"])


def test_trends_use_shanghai_dates_and_monday_weeks(tmp_path):
    from datetime import datetime

    stats = Analytics(tmp_path)
    for i, stamp in enumerate(["2026-09-13T15:59:59+00:00", "2026-09-13T16:00:00+00:00"]):
        run = tmp_path / str(i)
        run.mkdir()
        write_json(
            run / "status.json",
            {
                "run_id": str(i),
                "state": "RENDERED",
                "started_at": datetime.fromisoformat(stamp).timestamp(),
            },
        )
    data = stats.snapshot()
    assert [r["date"] for r in data["trends"]["daily"]] == ["2026-09-13", "2026-09-14"]
    assert [r["date"] for r in data["trends"]["weekly"]] == ["2026-09-07", "2026-09-14"]
    assert stats.snapshot("2026-09-14", "2026-09-14")["summary"]["tasks"] == 1


def test_pipeline_persists_public_asr_error(tmp_path, monkeypatch):
    from video_report_agent.pipeline import create_run, generate
    run = create_run(tmp_path, 'BV1aTtb6uE7d')
    def fail(*args, **kwargs):
        raise CloudAsrError('submit', 'HTTP request rejected', http_status=400,
                            provider_code='Arrearage')
    monkeypatch.setattr('video_report_agent.pipeline.reuse_download', fail)
    status = generate(run)
    assert status['error_code'] == 'ASR_BILLING_UNAVAILABLE'
    assert status['http_status'] == 400
    assert status['provider_code'] == 'Arrearage'
    assert '欠费' in status['error']
    assert json.loads((run / 'asr-error.json').read_text())['provider_code'] == 'Arrearage'
