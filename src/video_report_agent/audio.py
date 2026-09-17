"""Deterministic FFmpeg/FFprobe handling for local ASR."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path


class AudioExtractionError(RuntimeError):
    """Raised when an audio artifact cannot be created and verified."""


def midpoint_pause_ms(path: Path, duration_ms: int) -> int | None:
    """Find a >=250 ms quiet pause within 30 seconds of the midpoint; never hard-cut."""
    start_ms = duration_ms // 2 - 30_000
    completed = subprocess.run(
        [
            _require_binary("ffmpeg"), "-hide_banner", "-nostdin",
            "-ss", str(start_ms / 1000), "-t", "60", "-i", str(path),
            "-af", "silencedetect=noise=-40dB:d=0.25", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise AudioExtractionError("FFmpeg silence detection failed")
    pauses = []
    beginning = None
    for kind, value in re.findall(r"silence_(start|end): ([\d.]+)", completed.stderr):
        if kind == "start":
            beginning = float(value)
        elif beginning is not None:
            end = float(value)
            if end - beginning >= 0.25:
                pauses.append(start_ms + round((beginning + end) * 500))
            beginning = None
    return min(pauses, key=lambda point: abs(point - duration_ms / 2)) if pauses else None


def split_pcm_audio(path: Path, directory: Path, split_ms: int) -> tuple[Path, Path]:
    """Copy consecutive PCM frames without re-encoding, gaps or overlap."""
    parts = (directory / "part-0.wav", directory / "part-1.wav")
    with wave.open(str(path)) as source:
        split_frame = round(split_ms * source.getframerate() / 1000)
        for target, frames in zip(parts, (split_frame, source.getnframes() - split_frame)):
            with wave.open(str(target), "wb") as output:
                output.setparams(source.getparams())
                while frames:
                    count = min(frames, source.getframerate() * 30)
                    output.writeframes(source.readframes(count))
                    frames -= count
    return parts


@dataclass(frozen=True)
class AudioProbe:
    path: Path
    channels: int
    sample_rate_hz: int
    duration_ms: int


@dataclass(frozen=True)
class AudioExtraction:
    path: Path
    command: tuple[str, ...]
    probe: AudioProbe


def _require_binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        raise AudioExtractionError(f"{name} is not available on PATH")
    return binary


def _duration_to_ms(raw_duration: object) -> int:
    try:
        duration_ms = round(float(raw_duration) * 1000)
    except (TypeError, ValueError) as exc:
        raise AudioExtractionError("FFprobe did not return a numeric duration") from exc
    if duration_ms <= 0:
        raise AudioExtractionError("FFprobe returned an empty duration")
    return duration_ms


def _run_ffprobe(path: Path, show_entries: str) -> dict[str, object]:
    ffprobe = _require_binary("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        show_entries,
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AudioExtractionError(f"ffprobe failed with exit code {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AudioExtractionError("ffprobe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AudioExtractionError("ffprobe returned an invalid payload")
    return payload


def probe_media_duration_ms(path: Path) -> int:
    """Return the container duration for a non-empty media input."""

    if not path.is_file():
        raise AudioExtractionError(f"input media does not exist: {path}")
    payload = _run_ffprobe(path, "format=duration")
    format_data = payload.get("format")
    if not isinstance(format_data, dict):
        raise AudioExtractionError("ffprobe did not return container metadata")
    return _duration_to_ms(format_data.get("duration"))


def probe_audio(path: Path) -> AudioProbe:
    """Verify mono 16 kHz PCM output with FFprobe."""

    if not path.is_file() or path.stat().st_size == 0:
        raise AudioExtractionError("audio output is missing or empty")

    payload = _run_ffprobe(path, "stream=channels,sample_rate:format=duration")
    streams = payload.get("streams")
    format_data = payload.get("format")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise AudioExtractionError("ffprobe did not return an audio stream")
    if not isinstance(format_data, dict):
        raise AudioExtractionError("ffprobe did not return audio duration")

    stream = streams[0]
    try:
        channels = int(stream.get("channels", 0))
        sample_rate_hz = int(stream.get("sample_rate", 0))
    except (TypeError, ValueError) as exc:
        raise AudioExtractionError("ffprobe returned invalid audio properties") from exc
    if channels != 1 or sample_rate_hz != 16000:
        raise AudioExtractionError(
            f"audio profile must be mono 16 kHz, got {channels} channel(s) at {sample_rate_hz} Hz"
        )
    return AudioProbe(
        path=path,
        channels=channels,
        sample_rate_hz=sample_rate_hz,
        duration_ms=_duration_to_ms(format_data.get("duration")),
    )


def extract_audio(
    media_path: Path,
    output_path: Path,
    *,
    limit_seconds: int | None = None,
) -> AudioExtraction:
    """Extract a verified mono 16 kHz PCM WAV without invoking a shell."""

    if not media_path.is_file():
        raise AudioExtractionError(f"input media does not exist: {media_path}")
    if limit_seconds is not None and limit_seconds <= 0:
        raise AudioExtractionError("limit_seconds must be positive when set")

    ffmpeg = _require_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
    partial_path.unlink(missing_ok=True)

    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-i",
        str(media_path),
    ]
    if limit_seconds is not None:
        command.extend(["-t", str(limit_seconds)])
    command.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(partial_path),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        partial_path.unlink(missing_ok=True)
        raise AudioExtractionError(
            f"ffmpeg audio extraction failed with exit code {completed.returncode}"
        )

    try:
        probe = probe_audio(partial_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise

    partial_path.replace(output_path)
    return AudioExtraction(path=output_path, command=tuple(command), probe=probe)
