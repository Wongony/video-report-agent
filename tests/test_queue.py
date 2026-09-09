import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from video_report_agent.pipeline import create_run, write_json
from video_report_agent.queue import AdmissionError, RunQueue
from video_report_agent.session import OwnerSessions


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_fifo_owner_slots_failure_and_positions(tmp_path):
    started = []
    releases = {}

    def generate(run):
        started.append(run.name)
        assert releases[run.name].wait(3)
        if run.name == first.name:
            raise RuntimeError("broken task")
        write_json(run / "status.json", {"run_id": run.name, "state": "RENDERED"})

    def create():
        run = create_run(tmp_path, "BV1aTtb6uE7d")
        releases[run.name] = threading.Event()
        return run

    queue = RunQueue(tmp_path, generate, max_concurrency=2, max_active_per_owner=3)
    try:
        first = queue.submit("a", create)
        second = queue.submit("a", create)
        third = queue.submit("b", create)
        fourth = queue.submit("c", create)
        wait_for(lambda: len(started) == 2)
        assert started == [first.name, third.name]
        rows = {s["run_id"]: s for s in queue.statuses("a")}
        assert rows[second.name]["queue_position"] == 1
        assert rows[second.name]["queued_ahead"] == 0
        assert rows[second.name]["running_count"] == 2
        assert queue.statuses("c")[0]["queue_position"] == 2
        releases[first.name].set()
        wait_for(lambda: second.name in started)
        assert fourth.name not in started
        assert (
            next(s for s in queue.statuses("a") if s["run_id"] == first.name)["state"] == "FAILED"
        )
        releases[third.name].set()
        wait_for(lambda: fourth.name in started)
    finally:
        for release in releases.values():
            release.set()
        queue.close()


def test_atomic_admission(tmp_path):
    release = threading.Event()

    def generate(run):
        release.wait(3)
        write_json(run / "status.json", {"state": "RENDERED"})

    queue = RunQueue(tmp_path, generate, max_active_per_owner=2, max_queue_length=2)

    def submit(owner):
        try:
            return queue.submit(owner, lambda: create_run(tmp_path, "BV1aTtb6uE7d"))
        except AdmissionError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=10) as callers:
            outcomes = list(callers.map(submit, ["a"] * 10))
        assert outcomes.count("USER_ACTIVE_LIMIT") == 8
        assert len(list(tmp_path.glob("*/input.json"))) == 2
        submit("b")
        assert submit("c") == "QUEUE_FULL"
        assert len(list(tmp_path.glob("*/input.json"))) == 3
    finally:
        release.set()
        queue.close()


def seed(root, owner, sequence, state, pipeline_state=None):
    run = create_run(root, "BV1aTtb6uE7d")
    write_json(
        run / "queue.json",
        {"owner_id": owner, "queue_seq": sequence, "state": state, "queued_at": sequence},
    )
    if pipeline_state:
        write_json(run / "status.json", {"run_id": run.name, "state": pipeline_state})
    return run


def test_restart_recovers_only_queued_and_single_instance(tmp_path):
    later = seed(tmp_path, "b", 4, "QUEUED")
    earlier = seed(tmp_path, "a", 1, "QUEUED")
    interrupted = seed(tmp_path, "c", 2, "RUNNING", "TRANSCRIBING")
    finished = seed(tmp_path, "d", 3, "RUNNING", "RENDERED")
    terminal = seed(tmp_path, "e", 5, "FAILED", "FAILED")
    legacy = create_run(tmp_path, "BV1aTtb6uE7d")
    terminal_before = (terminal / "queue.json").read_bytes()
    order = []

    def generate(run):
        order.append(run.name)
        write_json(run / "status.json", {"state": "RENDERED"})

    queue = RunQueue(tmp_path, generate)
    try:
        with pytest.raises(ValueError, match="already has a scheduler"):
            RunQueue(tmp_path, generate)
        assert queue.waiting == [earlier.name, later.name]
        assert queue.records[interrupted.name]["state"] == "FAILED"
        assert (
            json.loads((interrupted / "status.json").read_text())["error_category"]
            == "SERVER_INTERRUPTED"
        )
        assert queue.records[finished.name]["state"] == "RENDERED"
        assert (terminal / "queue.json").read_bytes() == terminal_before
        assert legacy.name not in queue.records
        queue.start()
        wait_for(lambda: len(order) == 2)
        assert order == [earlier.name, later.name]
    finally:
        queue.close()
    restarted = RunQueue(tmp_path, generate)
    assert not restarted.waiting
    restarted.close()


def test_shutdown_leaves_waiting_persisted(tmp_path):
    release = threading.Event()

    def generate(run):
        release.wait(3)
        write_json(run / "status.json", {"state": "RENDERED"})

    queue = RunQueue(tmp_path, generate)
    queue.submit("a", lambda: create_run(tmp_path, "BV1aTtb6uE7d"))
    waiting = queue.submit("b", lambda: create_run(tmp_path, "BV1aTtb6uE7d"))
    queue.stop()
    release.set()
    queue.close()
    assert json.loads((waiting / "queue.json").read_text())["state"] == "QUEUED"
    restarted = RunQueue(tmp_path, generate)
    assert restarted.waiting == [waiting.name]
    restarted.close()


def test_session_survives_restart_and_rejects_tampering(tmp_path):
    sessions = OwnerSessions(tmp_path, secure=True)
    owner, cookie = sessions.identify(None)
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert OwnerSessions(tmp_path).identify(cookie) == (owner, None)
    assert sessions.identify(cookie.replace(owner, "0" * 32))[0] != owner
