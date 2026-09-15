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
