import json
from pathlib import Path

import pytest

from video_report_agent import usage


def message(cost=0.12, tokens=120):
    return json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant", "usage": {"totalTokens": tokens, "cost": {"total": cost}},
        },
    }) + "\n"


def test_cached_matches_full_and_does_not_open_unchanged_log(tmp_path, monkeypatch):
    log = tmp_path / "pi.events.jsonl"
    log.write_text(message() + message(0) + '{"type":"agent_end"}\n')
    expected = usage.call_costs(tmp_path, {}, None)
    assert expected["llm_calls"] == 2
    assert expected["tokens"] == 240
    assert expected["llm_usd_estimate"] == 0.12
    assert expected["llm_unpriced_calls"] == 1
    assert usage.cached_call_costs(tmp_path, {}, None) == expected
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path != log, "unchanged Pi log was opened"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert usage.cached_call_costs(tmp_path, {}, None) == expected


@pytest.mark.parametrize("change", ["append", "truncate", "rewrite", "replace", "delete"])
def test_log_changes_invalidate(tmp_path, change):
    log = tmp_path / "pi.events.jsonl"
    log.write_text(message())
    usage.cached_call_costs(tmp_path, {}, None)
    if change == "append":
        with log.open("a") as stream:
            stream.write(message())
    elif change == "truncate":
        log.write_text("")
    elif change == "rewrite":
        size = log.stat().st_size
        log.write_text(message(0.34))
        assert log.stat().st_size == size
    elif change == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_text(message(0.34))
        replacement.replace(log)
    else:
        log.unlink()
    assert usage.cached_call_costs(tmp_path, {}, None) == usage.call_costs(tmp_path, {}, None)


@pytest.mark.parametrize("bad", ["{", "[]", '{"version":1}', "invalid_llm", "version"])
def test_invalid_cache_is_rebuilt(tmp_path, monkeypatch, bad):
    (tmp_path / "pi.events.jsonl").write_text(message())
    expected = usage.cached_call_costs(tmp_path, {}, None)
    path = tmp_path / "usage.json"
    if bad == "version":
        monkeypatch.setattr(usage, "USAGE_CACHE_VERSION", usage.USAGE_CACHE_VERSION + 1)
    elif bad == "invalid_llm":
        cache = json.loads(path.read_text())
        cache["llm"]["tokens"] = "broken"
        path.write_text(json.dumps(cache))
    else:
        path.write_text(bad)
    assert usage.cached_call_costs(tmp_path, {}, None) == expected
    cache = json.loads(path.read_text())
    assert cache["version"] == usage.USAGE_CACHE_VERSION
    assert cache["llm"]["tokens"] == 120


def test_partial_line_is_counted_only_after_newline(tmp_path):
    log = tmp_path / "pi.events.jsonl"
    log.write_text(message() + message().rstrip("\n"))
    assert usage.cached_call_costs(tmp_path, {}, None)["llm_calls"] == 1
    with log.open("a") as stream:
        stream.write("\n")
    assert usage.cached_call_costs(tmp_path, {}, None)["llm_calls"] == 2


def test_append_during_read_does_not_publish_cache(tmp_path, monkeypatch):
    log = tmp_path / "pi.events.jsonl"
    log.write_text(message())
    original = usage._llm_costs

    def append_after_read(run):
        result = original(run)
        with log.open("a") as stream:
            stream.write(message())
        return result

    monkeypatch.setattr(usage, "_llm_costs", append_after_read)
    assert usage.cached_call_costs(tmp_path, {}, None)["llm_calls"] == 1
    assert not (tmp_path / "usage.json").exists()
    monkeypatch.setattr(usage, "_llm_costs", original)
    assert usage.cached_call_costs(tmp_path, {}, None)["llm_calls"] == 2


def test_asr_is_fresh_on_cache_hit(tmp_path, monkeypatch):
    usage.cached_call_costs(tmp_path, {}, None)

    def unexpected(run):
        pytest.fail("LLM cache missed")

    monkeypatch.setattr(usage, "_llm_costs", unexpected)
    asr = tmp_path / "asr.json"
    asr.write_text(json.dumps({
        "backend": "paraformer", "usage": {"content_duration_ms": 60000},
    }))
    assert usage.cached_call_costs(tmp_path, {}, 0.0002)["asr_cny_estimate"] == 0.012
    assert usage.cached_call_costs(tmp_path, {}, 0.0004)["asr_cny_estimate"] == 0.024
    assert usage.cached_call_costs(
        tmp_path, {"transcript_reused_from": "old"}, 0.0004,
    )["asr_basis"] == "reused"
    asr.write_text('{"backend":"mlx"}')
    assert usage.cached_call_costs(tmp_path, {}, None)["asr_basis"] == "local"
    assert "asr_cny_estimate" not in json.loads((tmp_path / "usage.json").read_text())["llm"]


def test_cache_write_failure_returns_usage_and_cleans_same_directory_temp(tmp_path, monkeypatch):
    (tmp_path / "pi.events.jsonl").write_text(message())
    expected = usage.call_costs(tmp_path, {}, None)
    seen = []

    def fail_replace(source, target):
        assert source.parent == target.parent == tmp_path
        assert source.is_file()
        seen.append(source)
        raise OSError("read-only destination")

    monkeypatch.setattr(usage.os, "replace", fail_replace)
    assert usage.cached_call_costs(tmp_path, {}, None) == expected
    assert usage.cached_call_costs(tmp_path, {}, None) == expected
    assert len(set(seen)) == 2
    assert all(not path.exists() for path in seen)
