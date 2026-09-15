import json
import subprocess
import sys
import time

from video_report_agent import execution
from video_report_agent.pipeline import create_run


def test_timeout_stops_worker_and_child(tmp_path, monkeypatch):
    run = create_run(tmp_path, "BV1aTtb6uE7d")
    original = subprocess.Popen
    child_code = "import time; from pathlib import Path; time.sleep(2); Path('survived').touch()"
    worker = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(30)"
    )

    def launch(command, **kwargs):
        return original([sys.executable, "-c", worker], cwd=run, **kwargs)

    monkeypatch.setattr(execution.subprocess, "Popen", launch)
    status = execution.generate(run, timeout=0.5)
    assert status["state"] == "FAILED"
    assert status["error_category"] == "EXECUTION_TIMEOUT"
    time.sleep(2)
    assert not (run / "survived").exists()
    assert json.loads((run / "status.json").read_text())["state"] == "FAILED"


def test_worker_completes_without_provider_call(tmp_path):
    run = create_run(tmp_path, "BV1aTtb6uE7d")
    # Invalid input fails inside the actual worker before network/provider work.
    path = run / "input.json"
    metadata = json.loads(path.read_text())
    metadata["url"] = "https://example.com/invalid"
    path.write_text(json.dumps(metadata))
    status = execution.generate(run, timeout=10)
    assert status["state"] == "FAILED"
    assert status["error_category"] != "EXECUTION_TIMEOUT"
    assert "Bilibili" in status["error"]


def test_cancel_stops_worker_and_child(tmp_path, monkeypatch):
    import threading

    run = create_run(tmp_path, "BV1aTtb6uE7d")
    original = subprocess.Popen
    child_code = "import time; from pathlib import Path; time.sleep(1); Path('survived').touch()"
    worker = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "Path('started').touch(); time.sleep(30)"
    )

    def launch(command, **kwargs):
        return original([sys.executable, "-c", worker], cwd=run, **kwargs)

    def cancel():
        while not (run / "started").exists():
            time.sleep(0.01)
        (run / "cancel.requested").touch()

    monkeypatch.setattr(execution.subprocess, "Popen", launch)
    thread = threading.Thread(target=cancel, daemon=True)
    thread.start()
    status = execution.generate(run, timeout=5)
    thread.join(timeout=1)
    assert status["state"] == "CANCELLED"
    time.sleep(1.1)
    assert not (run / "survived").exists()
    assert json.loads((run / "status.json").read_text())["state"] == "CANCELLED"
