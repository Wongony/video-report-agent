"""Anonymous usage ledger and estimates from retained run artifacts."""

import json
import math
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def read_object(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def number(value):
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


def call_costs(run, status, asr_rate):
    """Count only completed assistant-message events, never streamed usage copies."""
    calls, tokens, estimates, unknown = 0, 0, [], 0
    path = run / "pi.events.jsonl"
    if path.is_file():
        with path.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # A currently running process may have a partial last line.
                if not isinstance(event, dict) or event.get("type") != "message_end":
                    continue
                message = event.get("message", {})
                if message.get("role") != "assistant":
                    continue
                calls += 1
                usage = message.get("usage") or {}
                count = usage.get("totalTokens")
                if number(count):
                    tokens += count
                cost = (usage.get("cost") or {}).get("total")
                # Zero-filled custom pricing and failed calls do not prove free usage.
                if number(cost) and cost > 0:
                    estimates.append(cost)
                else:
                    unknown += 1
    asr = read_object(run / "asr.json")
    duration = asr.get("usage", {}).get("content_duration_ms")
    if status.get("transcript_reused_from"):
        asr_cost, asr_basis = 0, "reused"
    elif asr.get("backend") == "mlx":
        asr_cost, asr_basis = 0, "local"
    elif asr.get("backend") == "paraformer" and number(duration) and asr_rate is not None:
        asr_cost, asr_basis = duration / 1000 * asr_rate, "duration_estimate"
    else:
        asr_cost, asr_basis = None, "unknown"
    return {
        "llm_calls": calls, "tokens": tokens,
        "llm_usd_estimate": sum(estimates) if estimates else None,
        "llm_unpriced_calls": unknown,
        "asr_cny_estimate": asr_cost, "asr_basis": asr_basis,
    }


class Analytics:
    def __init__(self, root: Path, *, asr_rate=None):
        self.root = root
        self.path = root / ".usage.jsonl"
        self.lock = threading.Lock()
        self.asr_rate = asr_rate
        if not self.path.exists():
            self.record("started")

    def record(self, kind, owner=None, **fields):
        event = {"kind": kind, "at": time.time(), "owner": owner, **fields}
        with self.lock, self.path.open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def snapshot(self, start=None, end=None):
        for value in (start, end):
            if value:
                datetime.strptime(value, "%Y-%m-%d")
        if start and end and start > end:
            raise ValueError("开始日期不能晚于结束日期")

        def included(at):
            if not number(at):
                return not start and not end
            date = datetime.fromtimestamp(at, ZONE).strftime("%Y-%m-%d")
            return (not start or date >= start) and (not end or date <= end)

        with self.lock:
            events = []
            for line in self.path.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        since = next((e["at"] for e in events if e.get("kind") == "started"), None)
        events = [e for e in events if included(e.get("at"))]
        visitors = {e["owner"] for e in events if e.get("owner")}
        submissions = [e for e in events if e.get("kind") == "submit"]
        submitting_visitors = {e["owner"] for e in submissions if e.get("owner")}
        rows = []
        for path in self.root.glob("*/status.json"):
            status = read_object(path)
            if status.get("run_id") != path.parent.name:
                continue
            queue = read_object(path.parent / "queue.json")
            at = queue.get("queued_at", status.get("started_at"))
            if not included(at):
                continue
            owner = queue.get("owner_id")
            if owner:
                visitors.add(owner)
                submitting_visitors.add(owner)
            metadata = read_object(path.parent / "input.json")
            state = queue.get("state", status.get("state"))
            started = queue.get("execution_started_at", status.get("started_at"))
            finished = queue.get("finished_at", status.get("finished_at"))
            rows.append({
                "run_id": path.parent.name, "owner": owner, "submitted_at": at,
                "state": state, "video_id": metadata.get("video_id"),
                "elapsed_seconds": max(0, finished - started)
                if number(finished) and number(started) else None,
                **call_costs(path.parent, status, self.asr_rate),
            })
        states = Counter(r["state"] for r in rows)
        ended = states["RENDERED"] + states["FAILED"]
        return {
            "tracking_since": since, "timezone": "Asia/Shanghai",
            "asr_cny_per_second": self.asr_rate,
            "summary": {
                "visitors": len(visitors),
                "submitting_visitors": len(submitting_visitors),
                "page_views": sum(e.get("kind") == "visit" for e in events),
                "submission_attempts": len(submissions),
                "rejected_attempts": sum(e["http_status"] != 202 for e in submissions),
                "tasks": len(rows), "successful_reports": states["RENDERED"],
                "failed_tasks": states["FAILED"],
                "active_tasks": len(rows) - ended,
                "success_rate": states["RENDERED"] / ended if ended else None,
                "llm_calls": sum(r["llm_calls"] for r in rows),
                "tokens": sum(r["tokens"] for r in rows),
                "llm_usd_estimate": sum(r["llm_usd_estimate"] or 0 for r in rows)
                if any(r["llm_usd_estimate"] is not None for r in rows) else None,
                "llm_unpriced_calls": sum(r["llm_unpriced_calls"] for r in rows),
                "llm_unrecorded_tasks": sum(r["llm_calls"] == 0 for r in rows),
                "asr_cny_estimate": sum(r["asr_cny_estimate"] or 0 for r in rows)
                if any(r["asr_cny_estimate"] is not None for r in rows) else None,
                "asr_unknown_tasks": sum(r["asr_cny_estimate"] is None for r in rows),
                "legacy_tasks_without_owner": sum(not r["owner"] for r in rows),
            },
            "runs": sorted(rows, key=lambda r: r["submitted_at"] or 0, reverse=True),
        }
