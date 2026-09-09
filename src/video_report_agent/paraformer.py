"""Beijing Paraformer file recognition; one submission, no automatic resubmit."""

import json
import os
import time
from pathlib import Path

import httpx

from .asr import AsrError, AsrRun, normalize_asr_segments
from .audio import probe_audio
from .media_config import DEFAULT_BASE_URL, PARAFORMER_PARAMETERS
from .redaction import redact


class CloudAsrError(AsrError):
    def __init__(self, stage, detail, task_id=None):
        self.stage = stage
        self.task_id = task_id
        self.detail = redact(detail)
        super().__init__(f"Paraformer {stage}: {self.detail}")

    def to_dict(self):
        return {"stage": self.stage, "task_id": self.task_id, "detail": self.detail}


class ParaformerBackend:
    default_model = "paraformer-v2"

    def __init__(
        self,
        model=default_model,
        base_url=None,
        *,
        client=None,
        parameters=None,
        wait_seconds=1800,
        poll_seconds=5,
        task_path=None,
    ):
        if model != self.default_model:
            raise ValueError("paraformer backend requires paraformer-v2")
        self.task_path = task_path
        self.model = model
        self.base_url = base_url or DEFAULT_BASE_URL
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY is required for paraformer")
        self.parameters = dict(PARAFORMER_PARAMETERS if parameters is None else parameters)
        self.client = client
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    def transcribe(self, audio_path: Path, language="zh") -> AsrRun:
        if self.task_path is not None and self.task_path.exists():
            saved = json.loads(self.task_path.read_text())
            if saved.get("task_id"):
                raise CloudAsrError("existing_task", "已有云端任务；未重新提交", saved["task_id"])
        if language != "zh":
            raise AsrError("ASR requires explicit Chinese language")
        if not audio_path.is_file() or not audio_path.stat().st_size:
            raise AsrError("audio input is missing or empty")
        if audio_path.stat().st_size > 1_000_000_000:
            raise AsrError("Paraformer temporary upload exceeds 1 GB")
        if probe_audio(audio_path).duration_ms > 12 * 60 * 60 * 1000:
            raise AsrError("Paraformer input exceeds 12 hours")
        if self.client is not None:
            return self._run(self.client, audio_path, language)
        with httpx.Client(timeout=60) as client:
            return self._run(client, audio_path, language)

    def _run(self, client, path, language):
        started = time.perf_counter()
        stage, task_id = "upload", None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            response = client.get(
                f"{self.base_url}/uploads",
                headers=headers,
                params={"action": "getPolicy", "model": self.model},
            )
            response.raise_for_status()
            policy = response.json()["data"]
            key = f"{policy['upload_dir']}/{path.name}"
            data = {
                "OSSAccessKeyId": policy["oss_access_key_id"],
                "Signature": policy["signature"],
                "policy": policy["policy"],
                "x-oss-object-acl": policy["x_oss_object_acl"],
                "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
                "key": key,
                "success_action_status": "200",
            }
            with path.open("rb") as audio:
                response = client.post(
                    policy["upload_host"],
                    data=data,
                    files={"file": (path.name, audio, "audio/wav")},
                    timeout=300,
                )
            response.raise_for_status()
            upload_ms = round((time.perf_counter() - started) * 1000)
            stage = "submit"
            try:
                response = client.post(
                    f"{self.base_url}/services/audio/asr/transcription",
                    headers={
                        **headers,
                        "X-DashScope-Async": "enable",
                        "X-DashScope-OssResourceResolve": "enable",
                    },
                    json={
                        "model": self.model,
                        "input": {"file_urls": [f"oss://{key}"]},
                        "parameters": self.parameters,
                    },
                )
            except httpx.TransportError:
                raise CloudAsrError(
                    "submit_unknown", "提交状态未知；未自动重新提交", task_id
                ) from None
            try:
                submitted = response.json()
                task_id = submitted.get("output", {}).get("task_id")
            except (ValueError, AttributeError):
                submitted = {}
            if task_id and self.task_path is not None:
                from .pipeline import write_json

                write_json(self.task_path, {"task_id": task_id})
            if response.status_code >= 500 or (response.is_success and not task_id):
                raise CloudAsrError("submit_unknown", "提交状态未知；未自动重新提交", task_id)
            response.raise_for_status()
            if not task_id:
                raise CloudAsrError("submit", "Response has no task_id")
            stage = "poll"
            waiting = time.perf_counter()
            while time.perf_counter() - waiting < self.wait_seconds:
                response = client.get(f"{self.base_url}/tasks/{task_id}", headers=headers)
                response.raise_for_status()
                completed = response.json()
                state = completed["output"]["task_status"]
                if state == "SUCCEEDED":
                    break
                if state not in {"PENDING", "RUNNING"}:
                    raise CloudAsrError(stage, {"task_status": state}, task_id)
                time.sleep(
                    min(
                        self.poll_seconds,
                        max(0, self.wait_seconds - (time.perf_counter() - waiting)),
                    )
                )
            else:
                raise CloudAsrError(
                    stage, "Task wait timed out; task may still be running", task_id
                )
            wait_ms = round((time.perf_counter() - waiting) * 1000)
            stage = "result"
            result = completed["output"]["results"][0]
            if result.get("subtask_status") != "SUCCEEDED":
                raise CloudAsrError(
                    stage, {"subtask_status": result.get("subtask_status")}, task_id
                )
            # Do not send the API key to the result-storage host.
            response = client.get(result["transcription_url"], follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            transcript = next(t for t in payload["transcripts"] if t["channel_id"] == 0)
            rows = [
                {
                    "start": s["begin_time"] / 1000,
                    "end": s["end_time"] / 1000,
                    "text": s["text"],
                    "words": [
                        {
                            "word": w["text"],
                            "start": w["begin_time"] / 1000,
                            "end": w["end_time"] / 1000,
                        }
                        for w in s.get("words", [])
                    ],
                }
                for s in transcript["sentences"]
            ]
            normalized = normalize_asr_segments(rows)
            return AsrRun(
                engine="paraformer",
                backend="paraformer",
                provider="dashscope",
                model=self.model,
                language=language,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                segments=normalized.segments,
                raw_result=redact(
                    {
                        "task_id": task_id,
                        "response": payload,
                        "task": completed,
                        "raw_segment_count": len(rows),
                    }
                ),
                timings={"upload_ms": upload_ms, "task_wait_ms": wait_ms},
                usage={
                    "content_duration_ms": transcript.get("content_duration_in_milliseconds"),
                    "service_usage": redact(completed.get("usage", {})),
                },
                dropped_empty_raw_segment_ordinals=normalized.dropped_empty_raw_segment_ordinals,
                dropped_non_positive_raw_segment_ordinals=(
                    normalized.dropped_non_positive_raw_segment_ordinals
                ),
                dropped_unrepresentable_raw_segment_ordinals=(
                    normalized.dropped_unrepresentable_raw_segment_ordinals
                ),
            )
        except CloudAsrError:
            raise
        except Exception as exc:
            # Exception strings can contain signed URLs, request headers, or bodies.
            raise CloudAsrError(stage, type(exc).__name__, task_id) from None
