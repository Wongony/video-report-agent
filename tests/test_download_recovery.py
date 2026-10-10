import json
import os
import subprocess
import sys
import time

import pytest

from video_report_agent import ingest
from video_report_agent.download_worker import select_backup_urls


def test_backup_uses_only_distinct_platform_urls_and_preserves_format(capsys):
    payload = {"dash": {"audio": [{
        "baseUrl": "https://primary.test/audio?token=secret",
        "backupUrl": ["https://primary.test/audio?token=secret",
                      "https://backup.test/audio?token=other"],
        "id": 123, "bandwidth": 2000,
    }]}}
    select_backup_urls(payload, backup=True)
    audio = payload["dash"]["audio"][0]
    assert audio["baseUrl"] == "https://backup.test/audio?token=other"
    assert audio["id"] == 123 and audio["bandwidth"] == 2000
    log = capsys.readouterr().err
    assert "host=backup.test" in log and "secret" not in log and "token" not in log


@pytest.mark.parametrize("progress,last_change,expected", [
    ({"downloaded_bytes": 95, "total_bytes": 100}, 29, False),
    ({"downloaded_bytes": 1, "total_bytes": 100, "eta": 10}, 29, False),
    ({"downloaded_bytes": 1, "total_bytes": 100, "eta": 100}, 29, True),
    ({"downloaded_bytes": 95, "total_bytes": 100}, 0, True),
    ({"status": "finished"}, 0, False),
    ({"total_bytes": "NA", "eta": "NA"}, 29, True),
])
def test_checkpoint_distinguishes_near_completion_and_stall(progress, last_change, expected):
    assert ingest._download_needs_switch(progress, 30, last_change) is expected


def controlled_workers(monkeypatch, scripts, *, budget=2, checkpoint=0.2):
    real_popen = subprocess.Popen
    processes = []

    def popen(command, **kwargs):
        script = scripts[min(len(processes), len(scripts) - 1)]
        process = real_popen([sys.executable, "-u", "-c", script], **kwargs)
        assert os.getpgid(process.pid) == os.getpgrp()
        processes.append((command, process))
        return process

    monkeypatch.setattr(ingest.subprocess, "Popen", popen)
    monkeypatch.setattr(ingest, "DOWNLOAD_CHECK_SECONDS", checkpoint)
    monkeypatch.setattr(ingest, "DOWNLOAD_STALL_SECONDS", 0.5)
    monkeypatch.setattr(ingest, "DOWNLOAD_BUDGET_SECONDS", budget)
    return processes


def test_stalled_download_switches_once_and_reaps_first_process(tmp_path, monkeypatch):
    processes = controlled_workers(monkeypatch, [
        "import time; time.sleep(20)",
        "print('/tmp/source.m4a')",
    ])
    result = ingest._run_download(["yt-dlp", "https://public.test/video"], tmp_path, None)
    assert result.returncode == 0 and result.stdout.strip() == "/tmp/source.m4a"
    assert len(processes) == 2
    assert processes[0][0][3] == "0" and processes[1][0][3] == "1"
    assert all(process.poll() is not None for _, process in processes)
    log = (tmp_path / "download.log").read_text()
    assert "reason=slow-or-stalled" in log and "retry=1" in log


def test_connection_failures_retry_once_and_redact_signed_urls(tmp_path, monkeypatch):
    processes = controlled_workers(monkeypatch, [
        "import sys; print('HTTP Error 403 https://cdn.test/a?secret=value', "
        "file=sys.stderr); sys.exit(1)",
    ])
    result = ingest._run_download(["yt-dlp", "https://public.test/video"], tmp_path, None)
    assert result.returncode == 1 and len(processes) == 2
    assert "HTTP Error 403" in result.stderr and "secret" not in result.stderr
    assert "secret" not in (tmp_path / "download.log").read_text()


def test_total_budget_terminates_even_near_complete_download(tmp_path, monkeypatch):
    progress = ingest._PROGRESS_PREFIX + json.dumps({
        "downloaded_bytes": 95, "total_bytes": 100,
    })
    processes = controlled_workers(monkeypatch, [
        f"import time; print({progress!r}); time.sleep(20)",
    ], budget=0.4)
    started = time.monotonic()
    with pytest.raises(ingest.UrlIngestError, match="下载超时.*联系管理员"):
        ingest._run_download(["yt-dlp", "https://public.test/video"], tmp_path, None)
    assert time.monotonic() - started < 2
    assert all(process.poll() is not None for _, process in processes)
    assert "total-budget-exhausted" in (tmp_path / "download.log").read_text()


def test_near_complete_download_continues_without_restart(tmp_path, monkeypatch):
    progress = ingest._PROGRESS_PREFIX + json.dumps({
        "downloaded_bytes": 95, "total_bytes": 100,
    })
    processes = controlled_workers(monkeypatch, [
        f"import time; print({progress!r}); time.sleep(0.3); print('/tmp/source.m4a')",
    ])
    result = ingest._run_download(["yt-dlp", "https://public.test/video"], tmp_path, None)
    assert result.returncode == 0 and len(processes) == 1
    assert "action=continue" in (tmp_path / "download.log").read_text()


def test_worker_termination_reaps_yt_dlp_child(tmp_path):
    pid_path = tmp_path / "child.pid"
    script = f'''
import sys, time
from pathlib import Path
from video_report_agent import download_worker

def simulated_ytdlp(args):
    child = download_worker.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path({str(pid_path)!r}).write_text(str(child.pid))
    time.sleep(30)

download_worker.yt_dlp.main = simulated_ytdlp
sys.argv = ["worker", "0"]
download_worker.main()
'''
    process = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_path.exists()
        child_pid = int(pid_path.read_text())
        process.terminate()
        process.wait(timeout=3)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_progress_template_is_json_when_estimate_or_eta_is_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from yt_dlp import YoutubeDL

    monkeypatch.setattr(ingest.shutil, "which", lambda _: "/test/yt-dlp")

    def runner(command, **kwargs):
        template = command[command.index("--progress-template") + 1].removeprefix("download:")
        with YoutubeDL() as ydl:
            output = ydl.evaluate_outtmpl(template, {"progress": {
                "status": "downloading", "downloaded_bytes": 1024,
                "total_bytes": 2048,
            }})
        progress = json.loads(output.removeprefix(ingest._PROGRESS_PREFIX))
        assert progress["eta"] is None and progress["downloaded_bytes"] == 1024
        return SimpleNamespace(returncode=1, stdout="", stderr="test")

    with pytest.raises(ingest.UrlIngestError):
        ingest.download_bilibili_video(ingest.validate_bilibili_url("BV1234567890"),
                                       tmp_path, runner=runner)


def test_no_backup_keeps_fresh_primary_url():
    payload = {"dash": {"audio": [{"base_url": "https://primary.test/audio"}]}}
    select_backup_urls(payload, backup=True)
    assert payload["dash"]["audio"][0]["base_url"] == "https://primary.test/audio"
