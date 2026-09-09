"""One sequential URL → ASR → Pi → report pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
import uuid
from pathlib import Path

from .asr import AsrError, transcribe_audio
from .audio import AudioExtractionError, extract_audio
from .ingest import UrlIngestError, download_bilibili_video, validate_bilibili_url
from .media_config import resolve_media_config
from .paraformer import CloudAsrError
from .pi import PiError, PiRunner
from .retention import cleanup_media
from .reuse import reuse_download, reuse_transcript
from .transcript import build_transcript
from .transcript_foundation import parse_roi


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def create_run(
    root: Path,
    url: str,
    *,
    transcript_mode="asr-only",
    ocr_mode="off",
    ocr_roi=None,
    subtitle_file=None,
    model_selection=None,
    asr_backend=None,
    asr_model=None,
    ocr_backend=None,
) -> Path:
    if transcript_mode not in {"asr-only", "fused"}:
        raise ValueError("transcript_mode must be asr-only or fused")
    if ocr_mode == "on":
        ocr_mode = "roi"
    if ocr_mode not in {"off", "auto", "roi"}:
        raise ValueError("ocr_mode must be off, auto or roi")
    if transcript_mode == "fused" and ocr_mode == "roi":
        parse_roi(ocr_roi)
    media_config = resolve_media_config(asr_backend, asr_model, ocr_backend)
    source = validate_bilibili_url(url)
    run = root.resolve() / uuid.uuid4().hex
    run.mkdir(parents=True)
    write_json(
        run / "input.json",
        {
            **({"model_selection": model_selection} if model_selection else {}),
            **media_config,
            "url": source.canonical_url,
            "video_id": source.video_id,
            "transcript_mode": transcript_mode,
            "ocr_mode": ocr_mode,
            "ocr_roi": ocr_roi,
            "subtitle_file": "subtitle" + Path(subtitle_file).suffix if subtitle_file else None,
        },
    )
    write_json(
        run / "status.json",
        {
            "run_id": run.name,
            "state": "QUEUED",
            "stage": "QUEUED",
            "started_at": time.time(),
        },
    )
    if subtitle_file:
        import shutil

        shutil.copy2(subtitle_file, run / ("subtitle" + Path(subtitle_file).suffix))
    return run


def generate(run: Path) -> dict:
    metadata = json.loads((run / "input.json").read_text())
    status = json.loads((run / "status.json").read_text())
    status.update(video_id=metadata["video_id"])

    def update(stage: str, **fields):
        if stage in {"RENDERED", "FAILED"}:
            fields["finished_at"] = time.time()
        status.update(state=stage, stage=stage, **fields)
        write_json(run / "status.json", status)

    try:
        update("DOWNLOADING")
        source = validate_bilibili_url(metadata["url"])
        request_subtitles = metadata.get("transcript_mode") == "fused"
        audio_only = metadata.get("transcript_mode", "asr-only") == "asr-only"
        downloaded, download_source = reuse_download(
            source,
            run,
            request_subtitles=request_subtitles,
            audio_only=audio_only,
        )
        if downloaded is None:
            downloaded = download_bilibili_video(
                source,
                run,
                request_subtitles=request_subtitles,
                audio_only=audio_only,
            )
        status["download_reused_from"] = download_source
        metadata.update(
            download_audio_only=audio_only,
            title=downloaded.title,
            uploader=downloaded.uploader,
            attribution=downloaded.attribution,
        )
        write_json(run / "input.json", metadata)
        transcript_source = reuse_transcript(run, metadata)
        status["transcript_reused_from"] = transcript_source
        if transcript_source is None:
            update("TRANSCRIBING", title=downloaded.title)
            audio = extract_audio(downloaded.media_path, run / "audio.wav")
            asr = transcribe_audio(
                audio.path,
                model=metadata["asr_model"],
                backend=metadata["asr_backend"],
                base_url=metadata["asr_base_url"],
                language=metadata["asr_language"],
                parameters=metadata["asr_parameters"],
                task_path=run / "asr-task.json",
            )
            write_json(run / "asr.json", asr.to_dict())
            options = argparse.Namespace(
                transcript_mode=metadata.get("transcript_mode", "asr-only"),
                ocr_mode=metadata.get("ocr_mode", "off"),
                ocr_roi=metadata.get("ocr_roi"),
                ocr_backend=metadata["ocr_backend"],
                subtitle_file=(
                    run / metadata["subtitle_file"] if metadata.get("subtitle_file") else None
                ),
            )
            build, _, _ = build_transcript(
                options,
                source_path=downloaded.media_path,
                artifact_root=run,
                asr_path=run / "asr.json",
                source_duration_ms=audio.probe.duration_ms,
                manifest=metadata,
            )
            transcript = [f"# {downloaded.title}", downloaded.attribution, ""]
            transcript.extend(
                f"[{u.unit_id} | {u.start_ms / 1000:.3f}–{u.end_ms / 1000:.3f}s] {u.canonical_text}"
                for u in build.canonical_units
            )
            (run / "transcript.md").write_text("\n".join(transcript))
        update("GENERATING")
        asyncio.run(PiRunner(**metadata.get("model_selection", {})).run(run))
        update("RENDERED", report_url=f"/reports/{run.name}/report.html")
    except Exception as exc:
        if isinstance(exc, CloudAsrError):
            write_json(run / "asr-error.json", exc.to_dict())
            (run / "failure.log").write_text(str(exc))
        else:
            (run / "failure.log").write_text(traceback.format_exc())
        if isinstance(exc, CloudAsrError):
            category = "EXTERNAL_API_FAILURE"
        elif isinstance(exc, PiError):
            category = exc.category
        elif isinstance(exc, UrlIngestError):
            category = (
                "ENVIRONMENT_FAILURE" if "unavailable" in str(exc) else "EXTERNAL_API_FAILURE"
            )
        elif isinstance(exc, (AsrError, AudioExtractionError, OSError)):
            category = "ENVIRONMENT_FAILURE"
        else:
            category = "IMPLEMENTATION_FAILURE"
        update("FAILED", error_category=category, error=str(exc))
    cleanup_media(run.parent)
    return status
