"""Enforce a wall-clock deadline for one pipeline and its local subprocesses."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .pipeline import write_json

RUN_TIMEOUT_SECONDS = 30 * 60


def generate(run: Path, *, timeout=RUN_TIMEOUT_SECONDS, env: dict[str, str] | None = None) -> dict:
    run = run.resolve()
    command = [
        sys.executable, "-c",
        "from pathlib import Path; import sys; "
        "from video_report_agent.pipeline import generate; generate(Path(sys.argv[1]))",
        str(run),
    ]
    with (run / "worker.log").open("ab") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=log, start_new_session=True,
            env={**os.environ, **env} if env is not None else None,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # The worker and yt-dlp/FFmpeg/Pi/browser children share this process group.
            # Kill before writing FAILED so a surviving worker cannot overwrite it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    path = run / "status.json"
    status = json.loads(path.read_text())
    if timed_out or process.returncode or status.get("state") not in {"RENDERED", "FAILED"}:
        status.update(
            state="FAILED", stage="FAILED", finished_at=time.time(),
            error_category="EXECUTION_TIMEOUT" if timed_out else "EXECUTION_FAILURE",
            error=("任务执行超过 30 分钟，已停止本地处理。" if timed_out
                   else "任务进程异常结束，请查看 worker.log。"),
        )
        write_json(path, status)
    return status
