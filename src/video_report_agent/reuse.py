"""Reuse completed local input artifacts across independent report runs."""

import json
import os
import shutil
from pathlib import Path

from .ingest import DownloadResult, UrlIngestError, _metadata
from .transcript_foundation import FUSION_VERSION, NORMALIZER_VERSION


def previous_runs(run: Path, video_id: str):
    for candidate in sorted(run.parent.iterdir()):
        if candidate == run or not candidate.is_dir():
            continue
        try:
            metadata = json.loads((candidate / "input.json").read_text())
            if metadata.get("video_id") == video_id:
                yield candidate, metadata
        except (OSError, ValueError, AttributeError):
            continue


def reuse_download(source, run: Path, *, request_subtitles: bool, audio_only: bool = False):
    for candidate, metadata in previous_runs(run, source.video_id):
        # input metadata is written only after the downloader verifies its output.
        if not metadata.get("asr_model"):
            continue
        if request_subtitles and metadata.get("transcript_mode", "asr-only") != "fused":
            continue
        directory = candidate / "download"
        media = directory / "source.mp4"
        if audio_only:
            media = next(
                (path for path in sorted(directory.glob("source.*"))
                 if path.suffix in {
                     ".m4a", ".webm", ".opus", ".mp3", ".ogg", ".aac", ".flac", ".wav",
                 }
                 and path.is_file() and path.stat().st_size),
                media,
            )
        elif metadata.get("download_audio_only"):
            continue
        info = directory / "source.info.json"
        if not media.is_file() or not media.stat().st_size or not info.is_file():
            continue
        try:
            title, uploader, attribution = _metadata(info, source)
        except UrlIngestError:
            continue
        target = run / "download"
        target.mkdir(exist_ok=True)
        for path in directory.glob("source.*"):
            if path == media:
                os.link(path, target / path.name)
            elif path.is_file():
                shutil.copy2(path, target / path.name)
        return DownloadResult(
            source,
            target / media.name,
            target / info.name,
            title,
            uploader,
            attribution,
            (),
        ), candidate.name
    return None, None


def reuse_transcript(run: Path, metadata: dict):
    for candidate, previous in previous_runs(run, metadata["video_id"]):
        identity = (
            "asr_backend",
            "asr_provider",
            "asr_model",
            "asr_language",
            "asr_parameters",
            "asr_base_url",
        )
        if not previous.get("asr_backend") or any(
            previous.get(key) != metadata.get(key) for key in identity
        ):
            continue
        if metadata.get("transcript_mode") == "fused" and metadata.get("ocr_mode") != "off":
            if any(previous.get(k) != metadata.get(k) for k in ("ocr_backend", "ocr_model")):
                continue
        defaults = {"transcript_mode": "asr-only", "ocr_mode": "off", "ocr_roi": None}
        if previous.get("asr_model") != metadata["asr_model"] or any(
            previous.get(key, default) != metadata.get(key, default)
            for key, default in defaults.items()
        ):
            continue
        old_sub, new_sub = previous.get("subtitle_file"), metadata.get("subtitle_file")
        if bool(old_sub) != bool(new_sub):
            continue
        try:
            if old_sub and (candidate / old_sub).read_bytes() != (run / new_sub).read_bytes():
                continue
            manifest = json.loads((candidate / "transcript-manifest.json").read_text())
            if (
                manifest.get("status") != "READY"
                or manifest.get("video_id") != metadata["video_id"]
                or manifest.get("transcript_mode") != metadata.get("transcript_mode", "asr-only")
                or manifest.get("normalizer_version") != NORMALIZER_VERSION
                or manifest.get("fusion_version") != FUSION_VERSION
            ):
                continue
            names = list(manifest["artifacts"].values()) + ["asr.json"]
            if any(Path(name).name != name or not (candidate / name).is_file() for name in names):
                continue
            units = [
                json.loads(line)
                for line in (candidate / "canonical-transcript.jsonl").read_text().splitlines()
            ]
            if not units or len(units) != manifest["canonical_unit_count"]:
                continue
            lines = [
                f"[{u['unit_id']} | {u['start_ms'] / 1000:.3f}–"
                f"{u['end_ms'] / 1000:.3f}s] {u['canonical_text']}"
                for u in units
            ]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for name in names:
            shutil.copy2(candidate / name, run / name)
        (run / "transcript.md").write_text(
            "\n".join([f"# {metadata['title']}", metadata["attribution"], "", *lines])
        )
        return candidate.name
    return None
