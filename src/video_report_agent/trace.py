"""Small, append-only pipeline spans; Pi keeps its own detailed event log."""

import json
import time
from contextlib import contextmanager
from uuid import uuid4

from .redaction import redact


class RunTrace:
    def __init__(self, run):
        self.path = run / "run.trace.jsonl"
        self.run_id = run.name

    def emit(self, event, span_id, stage, **fields):
        record = {
            "type": event,
            "timestamp": time.time(),
            "run_id": self.run_id,
            "span_id": span_id,
            "stage": stage,
            **redact(fields),
        }
        with self.path.open("a") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def cancelled(self):
        """Close interrupted spans after the worker has stopped writing."""
        active = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    # A killed worker may leave its final line incomplete.
                    continue
                if event.get("type") == "stage_start":
                    active[event["span_id"]] = event
                elif event.get("type") == "stage_end":
                    active.pop(event["span_id"], None)
        now = time.time()
        for span_id, event in active.items():
            self.emit("stage_end", span_id, event["stage"], status="cancelled",
                      elapsed_ms=round(max(0, now - event["timestamp"]) * 1000, 3),
                      error={"type": "Cancelled", "message": "用户取消任务"})
        self.emit("run_cancelled", "cancel", "cancellation")

    @contextmanager
    def span(self, stage, **fields):
        span_id = uuid4().hex
        started = time.monotonic()
        self.emit("stage_start", span_id, stage, **fields)
        output = {}
        try:
            yield output
        except Exception as exc:
            self.emit(
                "stage_end",
                span_id,
                stage,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                status="error",
                error={"type": type(exc).__name__, "message": str(exc)},
                **output,
            )
            raise
        else:
            self.emit(
                "stage_end",
                span_id,
                stage,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                status="ok",
                **output,
            )
