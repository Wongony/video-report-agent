import os
import sys

import pytest

from video_report_agent.asr import AsrError, normalize_asr_segments


@pytest.mark.parametrize("fails", [False, True])
def test_local_asr_worker_exits_after_success_or_failure(tmp_path, monkeypatch, fails):
    from video_report_agent.asr import MlxWhisperBackend

    # Run the real spawn/IPC path with a tiny substitute for the expensive model.
    (tmp_path / "mlx_whisper.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def transcribe(audio, **kwargs):\n"
        "    Path(audio + '.pid').write_text(str(os.getpid()))\n"
        "    assert kwargs == dict(path_or_hf_repo='test-model', language='zh', verbose=False)\n"
        "    if Path(audio).name == 'fail.wav':\n"
        "        raise RuntimeError('model failed')\n"
        "    return {'segments': [{'start': 0, 'end': 1, 'text': '测试'}]}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    audio = tmp_path / ("fail.wav" if fails else "ok.wav")
    audio.write_bytes(b"test")
    imported_before = "mlx_whisper" in sys.modules
    backend = MlxWhisperBackend("test-model")
    for _ in range(2):
        if fails:
            with pytest.raises(AsrError, match="transcription failed: RuntimeError"):
                backend.transcribe(audio)
        else:
            result = backend.transcribe(audio)
            assert result.segments[0].text == "测试"
            assert result.raw_result["segments"][0]["end"] == 1
            assert result.model == "test-model"
        pid = int(audio.with_suffix(".wav.pid").read_text())
        assert pid != os.getpid()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert ("mlx_whisper" in sys.modules) == imported_before


def test_empty_raw_asr_segments_are_retained_as_explicit_non_indexed_provenance() -> None:
    normalized = normalize_asr_segments(
        [
            {"start": 0.0, "end": 1.0, "text": "第一段中文内容"},
            {"start": 1.0, "end": 1.5, "text": "  "},
            {"start": 1.5, "end": 3.0, "text": "第二段中文内容"},
        ]
    )

    assert [segment.ordinal for segment in normalized.segments] == [0, 2]
    assert normalized.dropped_empty_raw_segment_ordinals == (1,)


def test_unrepresentable_ranges_are_retained_only_in_raw_provenance() -> None:
    normalized = normalize_asr_segments(
        [
            {"start": 0.0, "end": 1.0, "text": "可表示的内容"},
            {"start": 1.0011, "end": 1.0012, "text": "极短但真实的内容"},
            {"start": 2.0, "end": 2.0, "text": "零时长内容"},
        ]
    )

    assert [segment.ordinal for segment in normalized.segments] == [0]
    assert normalized.dropped_unrepresentable_raw_segment_ordinals == (1,)
    assert normalized.dropped_non_positive_raw_segment_ordinals == (2,)


def test_ms_quantization_preserves_adjacent_raw_order_without_artificial_overlap() -> None:
    normalized = normalize_asr_segments(
        [
            {"start": 0.0001, "end": 1.0001, "text": "第一段"},
            {"start": 1.0002, "end": 2.0002, "text": "第二段"},
        ]
    )

    assert [(segment.start_ms, segment.end_ms) for segment in normalized.segments] == [
        (0, 1000),
        (1000, 2000),
    ]


def test_float_noise_at_an_adjacent_boundary_is_not_a_real_overlap() -> None:
    normalized = normalize_asr_segments(
        [
            {"start": 0.0, "end": 0.1 + 0.2, "text": "第一段"},
            {"start": 0.3, "end": 1.0, "text": "第二段"},
        ]
    )

    assert [(segment.start_ms, segment.end_ms) for segment in normalized.segments] == [
        (0, 300),
        (300, 1000),
    ]


def test_material_overlap_still_fails_closed() -> None:
    with pytest.raises(AsrError, match="overlap"):
        normalize_asr_segments(
            [
                {"start": 0.0, "end": 1.0, "text": "第一段"},
                {"start": 0.9, "end": 2.0, "text": "第二段"},
            ]
        )
