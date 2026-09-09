import json
import os
import time

from video_report_agent.retention import cleanup_media


def make_run(root, name, state="RENDERED", age=0):
    run = root / name
    (run / "download").mkdir(parents=True)
    (run / "assets").mkdir()
    for name in (
        "download/source.mp4", "download/source.m4a", "audio.wav", "report.html", "transcript.md",
        "download/source.info.json", "download/source.zh.srt", "assets/image.png", "failure.log",
    ):
        (run / name).write_text("keep or remove")
    status = run / "status.json"
    status.write_text(json.dumps({"state": state}))
    timestamp = time.time() - age * 86400
    os.utime(status, (timestamp, timestamp))
    return run


def test_default_keeps_twenty_and_preserves_report_and_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIA_KEEP_LAST", raising=False)
    monkeypatch.delenv("MEDIA_MAX_AGE_DAYS", raising=False)
    runs = [make_run(tmp_path, str(i), age=i) for i in range(21)]
    failed = make_run(tmp_path, "failed", "FAILED", age=30)
    active = make_run(tmp_path, "active", "TRANSCRIBING", age=30)
    cleanup_media(tmp_path)
    assert all((run / "download/source.mp4").exists() for run in runs[:20])
    old = runs[-1]
    assert not (old / "download/source.mp4").exists()
    assert not (old / "download/source.m4a").exists()
    assert not (old / "audio.wav").exists()
    for name in (
        "report.html", "transcript.md", "download/source.info.json",
        "download/source.zh.srt", "assets/image.png", "failure.log", "status.json",
    ):
        assert (old / name).exists()
    assert (failed / "audio.wav").exists()
    assert (active / "audio.wav").exists()
    cleanup_media(tmp_path)  # Repeated cleanup is harmless.


def test_age_and_disabled_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_KEEP_LAST", "0")
    monkeypatch.setenv("MEDIA_MAX_AGE_DAYS", "0")
    old = make_run(tmp_path, "old", age=8)
    recent = make_run(tmp_path, "recent", age=6)
    cleanup_media(tmp_path)
    assert (old / "audio.wav").exists()
    monkeypatch.setenv("MEDIA_MAX_AGE_DAYS", "7")
    cleanup_media(tmp_path)
    assert not (old / "download/source.m4a").exists()
    assert not (old / "audio.wav").exists()
    assert (recent / "audio.wav").exists()
