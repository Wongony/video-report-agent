import json
from types import SimpleNamespace

import pytest

from video_report_agent import pipeline
from video_report_agent.asr import DEFAULT_ASR_MODEL
from video_report_agent.ingest import validate_bilibili_url
from video_report_agent.reuse import reuse_download, reuse_transcript
from video_report_agent.transcript_foundation import FUSION_VERSION, NORMALIZER_VERSION

URL = "https://www.bilibili.com/video/BV1cZ8x6sEhF/"


@pytest.fixture
def completed(tmp_path):
    run = pipeline.create_run(tmp_path, URL)
    metadata = json.loads((run / "input.json").read_text())
    metadata.update(asr_model=DEFAULT_ASR_MODEL, title="Test", attribution="Bilibili")
    pipeline.write_json(run / "input.json", metadata)
    (run / "download").mkdir()
    (run / "download/source.mp4").write_bytes(b"video")
    pipeline.write_json(
        run / "download/source.info.json", {"title": "Test", "uploader": "UP", "duration": 60}
    )
    unit = {"unit_id": "unit-000001", "start_ms": 0, "end_ms": 1000, "canonical_text": "hello"}
    (run / "canonical-transcript.jsonl").write_text(json.dumps(unit) + "\n")
    (run / "source-text-events.jsonl").write_text("{}\n")
    (run / "asr.json").write_text("{}")
    pipeline.write_json(
        run / "transcript-manifest.json",
        {
            "status": "READY",
            "video_id": metadata["video_id"],
            "transcript_mode": "asr-only",
            "normalizer_version": NORMALIZER_VERSION,
            "fusion_version": FUSION_VERSION,
            "canonical_unit_count": 1,
            "artifacts": {
                "canonical_transcript": "canonical-transcript.jsonl",
                "source_text_events": "source-text-events.jsonl",
                "transcript_manifest": "transcript-manifest.json",
            },
        },
    )
    # A failed report must not invalidate completed transcription.
    pipeline.write_json(run / "status.json", {"state": "FAILED"})
    return run, metadata


def test_pipeline_reuses_inputs_and_generates_new_report(completed, monkeypatch):
    previous, _ = completed
    run = pipeline.create_run(previous.parent, URL + "?p=1&share_source=copy")

    def unexpected(*args, **kwargs):
        pytest.fail("download/FFmpeg/Whisper/build must be skipped")

    for name in (
        "download_bilibili_video",
        "extract_audio",
        "transcribe_audio",
        "build_transcript",
    ):
        monkeypatch.setattr(pipeline, name, unexpected)

    async def generate_report(current):
        assert current == run
        assert "hello" in (current / "transcript.md").read_text()
        (current / "report.html").write_text("fresh report")

    monkeypatch.setattr(pipeline, "PiRunner", lambda: SimpleNamespace(run=generate_report))
    status = pipeline.generate(run)
    assert status["state"] == "RENDERED"
    assert status["title"] == "Test"
    assert json.loads((run / "status.json").read_text())["title"] == "Test"
    assert status["download_reused_from"] == previous.name
    assert status["transcript_reused_from"] == previous.name
    assert (run / "download/source.mp4").stat().st_ino == (
        previous / "download/source.mp4"
    ).stat().st_ino
    assert (run / "canonical-transcript.jsonl").stat().st_ino != (
        previous / "canonical-transcript.jsonl"
    ).stat().st_ino


@pytest.mark.parametrize("change", ["model", "mode", "ocr", "subtitle", "partial", "degraded"])
def test_incompatible_or_incomplete_text_is_not_reused(completed, change):
    previous, metadata = completed
    run = pipeline.create_run(previous.parent, URL)
    metadata = dict(metadata)
    if change == "model":
        metadata["asr_model"] = "other-model"
    elif change == "mode":
        metadata["transcript_mode"] = "fused"
    elif change == "ocr":
        metadata["ocr_mode"] = "auto"
    elif change == "subtitle":
        metadata["subtitle_file"] = "subtitle.srt"
    elif change == "partial":
        (previous / "canonical-transcript.jsonl").write_text("")
    else:
        path = previous / "transcript-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["status"] = "DEGRADED"
        pipeline.write_json(path, manifest)
    assert reuse_transcript(run, metadata) is None
    download, _ = reuse_download(validate_bilibili_url(URL), run, request_subtitles=False)
    assert download is not None


def test_page_and_subtitle_download_requirements(completed):
    previous, _ = completed
    run = pipeline.create_run(previous.parent, URL + "?p=2")
    assert reuse_download(validate_bilibili_url(URL + "?p=2"), run, request_subtitles=False) == (
        None,
        None,
    )
    assert reuse_download(validate_bilibili_url(URL), run, request_subtitles=True) == (None, None)


def test_no_history_runs_normal_pipeline(tmp_path, monkeypatch):
    run = pipeline.create_run(tmp_path, URL)
    calls = []

    def download(*args, **kwargs):
        calls.append("download")
        raise RuntimeError("stop before external work")

    monkeypatch.setattr(pipeline, "download_bilibili_video", download)
    assert pipeline.generate(run)["state"] == "FAILED"
    assert calls == ["download"]


def test_download_only_hit_still_transcribes(completed, monkeypatch):
    previous, _ = completed
    (previous / "canonical-transcript.jsonl").unlink()
    run = pipeline.create_run(previous.parent, URL)
    calls = []

    def extract(media, output):
        assert media == run / "download/source.mp4"
        calls.append("audio")
        return SimpleNamespace(path=output, probe=SimpleNamespace(duration_ms=1000))

    def transcribe(*args, **kwargs):
        calls.append("asr")
        return SimpleNamespace(to_dict=lambda: {})

    def build(*args, **kwargs):
        calls.append("canonical")
        unit = SimpleNamespace(
            unit_id="unit-000001", start_ms=0, end_ms=1000, canonical_text="fresh text"
        )
        return SimpleNamespace(canonical_units=[unit]), {}, {}

    async def report(current):
        calls.append("report")
        assert "fresh text" in (current / "transcript.md").read_text()

    monkeypatch.setattr(pipeline, "extract_audio", extract)
    monkeypatch.setattr(pipeline, "transcribe_audio", transcribe)
    monkeypatch.setattr(pipeline, "build_transcript", build)
    monkeypatch.setattr(pipeline, "PiRunner", lambda: SimpleNamespace(run=report))
    status = pipeline.generate(run)
    assert status["state"] == "RENDERED"
    assert status["download_reused_from"] == previous.name
    assert status["transcript_reused_from"] is None
    assert calls == ["audio", "asr", "canonical", "report"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("asr_backend", "paraformer"),
        ("asr_provider", "dashscope"),
        ("asr_parameters", {"changed": True}),
    ],
)
def test_backend_identity_prevents_reuse(completed, field, value):
    previous, metadata = completed
    run = pipeline.create_run(previous.parent, URL)
    assert reuse_transcript(run, {**metadata, field: value}) is None


def test_legacy_without_backend_not_reused(completed):
    previous, metadata = completed
    old = dict(metadata)
    del old["asr_backend"]
    pipeline.write_json(previous / "input.json", old)
    run = pipeline.create_run(previous.parent, URL)
    assert reuse_transcript(run, metadata) is None


def test_audio_download_reuse_does_not_supply_video_mode(completed):
    previous, metadata = completed
    (previous / "download/source.mp4").rename(previous / "download/source.m4a")
    metadata["download_audio_only"] = True
    pipeline.write_json(previous / "input.json", metadata)
    run = pipeline.create_run(previous.parent, URL)
    source = validate_bilibili_url(URL)
    assert reuse_download(source, run, request_subtitles=False) == (None, None)
    result, origin = reuse_download(source, run, request_subtitles=False, audio_only=True)
    assert origin == previous.name
    assert result.media_path.name == "source.m4a"
