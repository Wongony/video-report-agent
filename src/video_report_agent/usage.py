"""Per-run token and cost estimates shared by benchmarks and callers."""

import json
import math
import os
import tempfile
from pathlib import Path

# Bump for schema OR counting/pricing semantics changes, even if fields stay identical.
USAGE_CACHE_VERSION = 1


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


def _llm_costs(run):
    """Count only completed assistant-message events, never streamed usage copies."""
    calls, tokens, estimates, unknown = 0, 0, [], 0
    path = run / "pi.events.jsonl"
    if path.is_file():
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
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
    return {
        "llm_calls": calls, "tokens": tokens,
        "llm_usd_estimate": sum(estimates) if estimates else None,
        "llm_unpriced_calls": unknown,
    }


def _asr_costs(run, status, asr_rate):
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
        "asr_cny_estimate": asr_cost, "asr_basis": asr_basis,
    }


def call_costs(run, status, asr_rate):
    """Calculate current estimates without reading or writing a cache."""
    return {**_llm_costs(run), **_asr_costs(run, status, asr_rate)}


def _signature(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    # Metadata precision depends on the host filesystem; this is not a content hash.
    return {
        "device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
    }


def _valid_llm(value):
    return (
        isinstance(value, dict)
        and set(value) == {"llm_calls", "tokens", "llm_usd_estimate", "llm_unpriced_calls"}
        and all(number(value[key]) for key in ("llm_calls", "tokens", "llm_unpriced_calls"))
        and (value["llm_usd_estimate"] is None or number(value["llm_usd_estimate"]))
    )


def _write_cache(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=".usage-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream)
        os.replace(temporary, path)
    except OSError:
        pass  # Caching is optional; a write failure must not hide computed usage.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def cached_call_costs(run, status, asr_rate):
    """Reuse persisted LLM estimates until the log changes; always refresh ASR."""
    path = run / "pi.events.jsonl"
    signature = _signature(path)
    cache_path = run / "usage.json"
    cache = read_object(cache_path)
    llm = cache.get("llm")
    if not (
        cache.get("version") == USAGE_CACHE_VERSION
        and "source" in cache and cache["source"] == signature
        and _valid_llm(llm)
    ):
        llm = _llm_costs(run)
        if signature == _signature(path):
            _write_cache(cache_path, {
                "version": USAGE_CACHE_VERSION, "source": signature, "llm": llm,
            })
    return {**llm, **_asr_costs(run, status, asr_rate)}
