"""Per-run token and cost estimates shared by benchmarks and callers."""

import json
import math


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
