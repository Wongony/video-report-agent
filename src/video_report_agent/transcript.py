from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .transcript_foundation import (
    OcrError,
    RapidOcrBackend,
    TranscriptFoundationError,
    asr_events_from_payload,
    build_canonical_transcript,
    detect_auto_roi,
    discover_subtitle_source,
    extract_subtitle_ocr_events,
    parse_roi,
    parse_subtitle_file,
    read_json_object,
    write_transcript_artifacts,
)


def build_transcript(
    args: argparse.Namespace,
    *,
    source_path: Path,
    artifact_root: Path,
    asr_path: Path,
    source_duration_ms: int,
    manifest: dict[str, Any],
) -> tuple[Any, dict[str, Path], dict[str, dict[str, Any]]]:
    transcript_mode = getattr(args, "transcript_mode", "asr-only")
    ocr_mode = getattr(args, "ocr_mode", "off")
    if transcript_mode not in {"asr-only", "fused"}:
        raise TranscriptFoundationError("transcript_mode must be asr-only or fused")
    if ocr_mode not in {"off", "roi", "auto"}:
        raise TranscriptFoundationError("ocr_mode must be off, roi, or auto")
    asr_payload = read_json_object(asr_path)
    asr_events = asr_events_from_payload(asr_payload, payload_path=asr_path)
    warnings: list[str] = []
    source_status: dict[str, dict[str, Any]] = {
        "asr": {"status": "READY", "count": len(asr_events), "path": asr_path.name},
        "subtitle_track": {"status": "OFF", "count": 0, "path": None},
        "ocr": {"status": "OFF", "count": 0, "path": None},
    }
    subtitle_events: list[Any] = []
    if transcript_mode == "fused":
        explicit_subtitle = getattr(args, "subtitle_file", None)
        explicit_path = Path(explicit_subtitle).expanduser() if explicit_subtitle else None
        try:
            discovery = discover_subtitle_source(
                source_path,
                explicit_path=explicit_path,
                artifact_root=artifact_root,
            )
        except OSError:
            discovery = None
        if discovery is None:
            source_status["subtitle_track"] = {
                "status": "FAILED",
                "count": 0,
                "path": str(explicit_path) if explicit_path else None,
            }
            warnings.append("SUBTITLE_DISCOVERY_FAILED")
        elif discovery.status == "AVAILABLE" and discovery.path is not None:
            source_status["subtitle_track"] = {
                "status": "READY",
                "count": 0,
                "path": str(discovery.path),
                "format": discovery.format,
                "track_index": discovery.track_index,
                "language": discovery.language,
            }
            try:
                subtitle_events = parse_subtitle_file(discovery.path)
                source_status["subtitle_track"]["count"] = len(subtitle_events)
            except Exception:
                source_status["subtitle_track"]["status"] = "FAILED"
                warnings.append("SUBTITLE_PARSE_FAILED")
        else:
            source_status["subtitle_track"] = {
                "status": discovery.status,
                "count": 0,
                "path": None,
            }
            if discovery.warning and discovery.warning != "SUBTITLE_ABSENT":
                warnings.append(discovery.warning)
    ocr_events: list[Any] = []
    if transcript_mode == "fused" and ocr_mode != "off":
        write_ocr_events = True
        if ocr_mode == "roi":
            try:
                requested_roi = parse_roi(getattr(args, "ocr_roi", None))
            except OcrError as exc:
                raise TranscriptFoundationError(str(exc)) from exc
        else:
            requested_roi = None
        try:
            if getattr(args, "ocr_backend", "rapidocr") != "rapidocr":
                raise OcrError("Unsupported OCR backend")
            backend = RapidOcrBackend()
            if ocr_mode == "roi":
                roi = requested_roi
            else:
                auto = detect_auto_roi(source_path, sample_fps=1.0, adapter=backend)
                warnings.extend(auto.warnings)
                if auto.roi is None:
                    source_status["ocr"] = {"status": "UNSTABLE", "count": 0, "path": None}
                    roi = None
                else:
                    roi = auto.roi
            if roi is not None:
                ocr_events = extract_subtitle_ocr_events(
                    source_path,
                    roi,
                    sample_fps=2.0,
                    adapter=backend,
                    duration_ms=source_duration_ms,
                    roi_mode="explicit" if ocr_mode == "roi" else "auto",
                )
                source_status["ocr"] = {
                    "status": "READY" if ocr_events else "ABSENT",
                    "count": len(ocr_events),
                    "path": "subtitle-ocr-events.jsonl",
                    "roi": list(roi),
                    "coverage_status": "FULL",
                    "processed_start_ms": 0,
                    "processed_end_ms": source_duration_ms,
                    "video_duration_ms": source_duration_ms,
                    "coverage_ratio": 1.0,
                }
                if not ocr_events:
                    warnings.append("OCR_NO_EVENTS")
        except Exception:
            source_status["ocr"] = {
                "status": "FAILED",
                "count": 0,
                "path": "subtitle-ocr-events.jsonl",
            }
            warnings.append("OCR_FAILED")
            ocr_events = []
    else:
        write_ocr_events = False
        ocr_events = []

    build = build_canonical_transcript(
        video_id=manifest["video_id"],
        duration_ms=source_duration_ms,
        asr_events=asr_events,
        subtitle_events=subtitle_events,
        ocr_events=ocr_events,
        transcript_mode=transcript_mode,
        source_status=source_status,
        warnings=warnings,
    )
    paths = write_transcript_artifacts(
        build,
        artifact_root,
        write_ocr_events=write_ocr_events,
    )
    return build, paths, source_status
