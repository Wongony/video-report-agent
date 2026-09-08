import pytest

from video_report_agent.asr import AsrError, normalize_asr_segments


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
