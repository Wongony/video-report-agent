import json
from types import SimpleNamespace

import httpx
import pytest

from video_report_agent.paraformer import CloudAsrError, ParaformerBackend


def test_task_id_saved_before_poll_and_blocks_resubmit(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    monkeypatch.setattr(
        "video_report_agent.paraformer.probe_audio", lambda path: SimpleNamespace(duration_ms=1000)
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"test")
    checkpoint = tmp_path / "asr-task.json"
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/uploads"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "upload_dir": "test",
                        "oss_access_key_id": "key",
                        "signature": "sig",
                        "policy": "policy",
                        "x_oss_object_acl": "private",
                        "x_oss_forbid_overwrite": "true",
                        "upload_host": "https://upload.test/",
                    }
                },
            )
        if request.url.host == "upload.test":
            return httpx.Response(200)
        if request.url.path.endswith("/transcription"):
            return httpx.Response(200, json={"output": {"task_id": "cloud-123"}})
        assert json.loads(checkpoint.read_text()) == {"task_id": "cloud-123"}
        raise httpx.ConnectError("interrupted", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        backend = ParaformerBackend(client=client, task_path=checkpoint)
        with pytest.raises(CloudAsrError) as error:
            backend.transcribe(audio)
        assert error.value.task_id == "cloud-123"
        before = len(calls)
        audio.unlink()
        with pytest.raises(CloudAsrError, match="已有云端任务"):
            backend.transcribe(audio)
        assert len(calls) == before
