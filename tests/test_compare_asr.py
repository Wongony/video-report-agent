import json
import wave
from types import SimpleNamespace

from video_report_agent import compare_asr as comparison
from video_report_agent.asr import AsrRun, normalize_asr_segments


def test_comparison_uses_same_audio_and_asr_only_once_per_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("ASR_MODEL", "must-not-leak")
    manifest = tmp_path / "clips.json"
    manifest.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "id": "clip",
                        "source_path": "source.mp4",
                        "start_ms": 4000,
                        "duration_ms": 1000,
                    }
                ]
            }
        )
    )
    extraction, calls = [], []

    def prepare(command, **kwargs):
        extraction.append(command)
        with wave.open(command[-1], "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00\x00" * 16000)

    def transcribe(path, **kwargs):
        calls.append((path, kwargs))
        return AsrRun(
            engine=kwargs["backend"],
            model=kwargs["model"],
            language="zh",
            elapsed_ms=500,
            backend=kwargs["backend"],
            raw_result={},
            segments=normalize_asr_segments(
                [{"start": 0.1, "end": 0.9, "text": "原始识别"}]
            ).segments,
        )

    original_build = comparison.build_transcript

    def build(options, **kwargs):
        assert vars(options) == {"transcript_mode": "asr-only", "ocr_mode": "off"}
        return original_build(options, **kwargs)

    monkeypatch.setattr(comparison.subprocess, "run", prepare)
    monkeypatch.setattr(comparison, "probe_audio", lambda p: SimpleNamespace(duration_ms=1000))
    monkeypatch.setattr(comparison, "transcribe_audio", transcribe)
    monkeypatch.setattr(comparison, "build_transcript", build)
    run = comparison.compare_asr(manifest, tmp_path / "runs")
    assert len(extraction) == 1
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert calls[1][1]["model"] == "paraformer-v2"
    for backend in ("mlx", "paraformer"):
        root = run / "clip" / backend
        canonical = json.loads((root / "canonical-transcript.jsonl").read_text().strip())
        assert canonical["provenance"]["asr_backend"] == backend
        assert canonical["start_ms"] == 100
        assert canonical["canonical_text"] == "原始识别"
        mapped = json.loads((root / "source-mapping.json").read_text())
        assert mapped["segments"][0]["source_start_ms"] == 4100
    assert json.loads((run / "status.json").read_text())["state"] == "COMPLETE"
