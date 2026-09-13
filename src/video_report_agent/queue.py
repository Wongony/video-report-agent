"""Single-process, file-backed FIFO queue for Web runs."""

import fcntl
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .pipeline import write_json

TERMINAL = {"RENDERED", "FAILED"}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class AdmissionError(ValueError):
    pass


class RunQueue:
    def __init__(
        self,
        root,
        generate,
        *,
        max_concurrency=1,
        max_active_per_owner=2,
        max_queue_length=20,
        daily_user_limit=3,
    ):
        if min(max_concurrency, max_active_per_owner, max_queue_length, daily_user_limit) < 1:
            raise ValueError("Queue limits must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_lock = (self.root / ".queue.lock").open("a")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file_lock.close()
            raise ValueError("This runs directory already has a scheduler") from None
        self.generate = generate
        self.max_concurrency = max_concurrency
        self.max_active_per_owner = max_active_per_owner
        self.max_queue_length = max_queue_length
        self.daily_user_limit = daily_user_limit
        self.lock = threading.RLock()
        self.records = {}
        self.waiting = []
        self.running = set()
        self.stopped = False
        self.executor = ThreadPoolExecutor(max_workers=max_concurrency)
        try:
            self._recover()
        except Exception:
            self.executor.shutdown()
            self.file_lock.close()
            raise

    def _save(self, run_id):
        write_json(self.root / run_id / "queue.json", self.records[run_id])

    def _fail(self, run_id, category, error):
        path = self.root / run_id / "status.json"
        try:
            status = json.loads(path.read_text())
        except (OSError, ValueError):
            status = {"run_id": run_id}
        status.update(
            state="FAILED",
            stage="FAILED",
            error_category=category,
            error=error,
            finished_at=time.time(),
        )
        write_json(path, status)
        self.records[run_id].update(state="FAILED", finished_at=status["finished_at"])
        self._save(run_id)

    def _recover(self):
        for path in self.root.glob("*/queue.json"):
            try:
                record = json.loads(path.read_text())
                if (
                    not isinstance(record, dict)
                    or not record.get("owner_id")
                    or not isinstance(record.get("queue_seq"), int)
                    or record.get("state") not in TERMINAL | {"QUEUED", "RUNNING"}
                ):
                    raise ValueError("Invalid queue record")
                run_id = path.parent.name
                self.records[run_id] = record
                if record["state"] == "RUNNING":
                    try:
                        status = json.loads((path.parent / "status.json").read_text())
                    except (OSError, ValueError):
                        status = {}
                    if status.get("state") in TERMINAL:
                        record.update(state=status["state"], finished_at=status.get("finished_at"))
                        self._save(run_id)
                    else:
                        self._fail(
                            run_id,
                            "SERVER_INTERRUPTED",
                            "Server stopped during execution; not automatically restarted",
                        )
                if record["state"] == "QUEUED":
                    self.waiting.append(run_id)
            except (OSError, ValueError, TypeError):
                logging.exception("Could not recover queue record %s", path)
        self.waiting.sort(key=lambda run_id: self.records[run_id]["queue_seq"])
        self.sequence = max((r["queue_seq"] for r in self.records.values()), default=0)

    def start(self):
        with self.lock:
            self._dispatch()

    def submit(self, owner_id, create, *, video_id=None):
        with self.lock:
            if self.stopped:
                raise AdmissionError("SERVER_STOPPING")
            if video_id is not None:
                for run_id, record in self.records.items():
                    if record["owner_id"] == owner_id and record["state"] in {"QUEUED", "RUNNING"}:
                        metadata = json.loads((self.root / run_id / "input.json").read_text())
                        if metadata["video_id"] == video_id:
                            raise AdmissionError("VIDEO_ALREADY_ACTIVE")
            active = sum(
                r["owner_id"] == owner_id and r["state"] in {"QUEUED", "RUNNING"}
                for r in self.records.values()
            )
            if active >= self.max_active_per_owner:
                raise AdmissionError("USER_ACTIVE_LIMIT")
            today = datetime.fromtimestamp(time.time(), SHANGHAI_TZ).date()
            daily_admissions = sum(
                record["owner_id"] == owner_id
                and isinstance(record.get("queued_at"), (int, float))
                and datetime.fromtimestamp(record["queued_at"], SHANGHAI_TZ).date() == today
                for record in self.records.values()
            )
            if daily_admissions >= self.daily_user_limit:
                raise AdmissionError("DAILY_USER_LIMIT")
            if len(self.waiting) >= self.max_queue_length:
                raise AdmissionError("QUEUE_FULL")
            run = create()
            self.sequence += 1
            self.records[run.name] = {
                "owner_id": owner_id,
                "queue_seq": self.sequence,
                "queued_at": time.time(),
                "state": "QUEUED",
                "execution_started_at": None,
                "finished_at": None,
            }
            try:
                self._save(run.name)
            except Exception:
                del self.records[run.name]
                raise
            self.waiting.append(run.name)
            self._dispatch()
            return run

    def _dispatch(self):
        while not self.stopped and len(self.running) < self.max_concurrency:
            owners = {self.records[r]["owner_id"] for r in self.running}
            run_id = next(
                (r for r in self.waiting if self.records[r]["owner_id"] not in owners), None
            )
            if run_id is None:
                return
            self.records[run_id].update(state="RUNNING", execution_started_at=time.time())
            self._save(run_id)
            self.waiting.remove(run_id)
            self.running.add(run_id)
            self.executor.submit(self._execute, run_id)

    def _execute(self, run_id):
        try:
            self.generate(self.root / run_id)
            status = json.loads((self.root / run_id / "status.json").read_text())
            if status.get("state") not in TERMINAL:
                raise ValueError("Pipeline returned without a terminal status")
            with self.lock:
                self.records[run_id].update(
                    state=status["state"], finished_at=status.get("finished_at", time.time())
                )
                self._save(run_id)
        except Exception as exc:
            with self.lock:
                self._fail(run_id, "EXECUTION_FAILURE", str(exc))
        finally:
            with self.lock:
                self.running.discard(run_id)
                self._dispatch()

    def statuses(self, owner_id, *, shared_reports=False):
        with self.lock:
            positions = {r: i + 1 for i, r in enumerate(self.waiting)}
            result = []
            for run_id, record in self.records.items():
                if record["owner_id"] != owner_id and not (
                    shared_reports and record["state"] == "RENDERED"
                ):
                    continue
                try:
                    status = json.loads((self.root / run_id / "status.json").read_text())
                except (OSError, ValueError):
                    status = {}
                position = positions.get(run_id)
                status.update({k: v for k, v in record.items() if k != "owner_id"})
                status.update(
                    run_id=run_id,
                    queue_position=position,
                    queued_ahead=position - 1 if position else 0,
                    running_count=len(self.running),
                )
                if record["state"] == "QUEUED":
                    status["stage"] = "QUEUED"
                elif record["state"] == "RUNNING" and status.get("stage") == "QUEUED":
                    status["stage"] = "STARTING"
                result.append(status)
            return sorted(result, key=lambda s: s["queue_seq"], reverse=True)

    def stop(self):
        with self.lock:
            self.stopped = True

    def close(self):
        self.stop()
        self.executor.shutdown(wait=True)
        self.file_lock.close()
