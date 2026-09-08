import subprocess
from pathlib import Path

import pytest

from video_report_agent import audio


def test_ffmpeg_failure_does_not_create_final_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-a-real-video")
    output = tmp_path / "audio.wav"

    monkeypatch.setattr(audio.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=9),
    )

    with pytest.raises(audio.AudioExtractionError, match="ffmpeg audio extraction failed"):
        audio.extract_audio(source, output)

    assert not output.exists()
