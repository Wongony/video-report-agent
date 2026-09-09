import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from video_report_agent.analytics import Analytics, call_costs
from video_report_agent.pipeline import write_json
from video_report_agent.web import create_server


def message(cost=0.12):
    return {"type": "message_end", "message": {
        "role": "assistant", "usage": {"totalTokens": 120, "cost": {"total": cost}},
    }}


def test_costs_count_once_include_failed_calls_and_skip_reused_asr(tmp_path):
    events = [message(), {"type": "agent_end", "messages": [message()["message"]]},
              message(0), {"type": "message_end", "message": {"role": "toolResult"}}]
    (tmp_path / "pi.events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + '\n{"type":'
    )
    write_json(tmp_path / "asr.json", {
        "backend": "paraformer", "usage": {"content_duration_ms": 60000},
    })
    costs = call_costs(tmp_path, {"state": "FAILED"}, .0002)
    assert costs["llm_calls"] == 2
    assert costs["tokens"] == 240
    assert costs["llm_usd_estimate"] == .12
    assert costs["llm_unpriced_calls"] == 1
    assert costs["asr_cny_estimate"] == pytest.approx(.012)
    assert call_costs(tmp_path, {"transcript_reused_from": "old"}, .0002)[
        "asr_cny_estimate"
    ] == 0
    assert call_costs(tmp_path, {}, None)["asr_cny_estimate"] is None


def test_history_filter_unknowns_and_restart(tmp_path):
    for name, state, owner in [("success", "RENDERED", "a"),
                               ("failed", "FAILED", "a"), ("old", "RENDERED", None)]:
        run = tmp_path / name
        run.mkdir()
        write_json(run / "status.json", {
            "run_id": name, "state": state, "started_at": 0, "finished_at": 10,
        })
        if owner:
            write_json(run / "queue.json", {"owner_id": owner, "queued_at": 0})
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    write_json(comparison / "status.json", {"state": "COMPLETE"})
    stats = Analytics(tmp_path)
    stats.record("visit", "b")
    stats.record("visit", "b")
    stats.record("submit", "b", http_status=429)
    summary = Analytics(tmp_path).snapshot()["summary"]
    assert summary["visitors"] == 2
    assert summary["submitting_visitors"] == 2
    assert summary["page_views"] == 2
    assert summary["tasks"] == 3
    assert summary["success_rate"] == 2 / 3
    assert summary["rejected_attempts"] == 1
    assert summary["legacy_tasks_without_owner"] == 1
    assert summary["llm_usd_estimate"] is None
    assert summary["asr_cny_estimate"] is None
    filtered = stats.snapshot("1970-01-01", "1970-01-01")["summary"]
    assert filtered["tasks"] == 3
    assert filtered["page_views"] == 0
    with pytest.raises(ValueError):
        stats.snapshot("2026-02-30")
    with pytest.raises(ValueError):
        stats.snapshot("2026-02-02", "2026-01-01")


def test_admin_auth_visits_polling_and_rejected_submissions(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-secret")
    monkeypatch.delenv("ASR_CNY_PER_SECOND", raising=False)
    def generate(run):
        write_json(run / "status.json", {"run_id": run.name, "state": "RENDERED"})

    monkeypatch.setattr("video_report_agent.web.generate", generate)
    server = create_server(tmp_path, 0, mode="public")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        for path in ("/", "/", "/api/visual-report/runs", "/api/visual-report/current"):
            with client.open(base + path) as response:
                response.read()
        for authorization in (None, "Bearer wrong"):
            headers = {"Authorization": authorization} if authorization else {}
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(base + "/api/admin/stats", headers=headers))
            assert exc.value.code == 401
        with client.open(base + "/admin") as response:
            page = response.read()
            assert b"test-admin-secret" not in page
        with pytest.raises(HTTPError) as exc:
            client.open(Request(base + "/api/visual-report/runs", data=b'{"url":"bad"}'))
        assert exc.value.code == 400
        with client.open(Request(
            base + "/api/visual-report/runs", data=b'{"url":"BV1aTtb6uE7d"}'
        )) as response:
            assert response.status == 202
            response.read()
        headers = {"Authorization": "Bearer test-admin-secret"}
        with urlopen(Request(base + "/api/admin/stats", headers=headers)) as response:
            assert response.headers["Cache-Control"] == "no-store"
            stats = json.load(response)["summary"]
        assert stats["visitors"] == 1
        assert stats["page_views"] == 2
        assert stats["submission_attempts"] == 2
        assert stats["rejected_attempts"] == 1
        assert stats["tasks"] == 1
        assert stats["submitting_visitors"] == 1
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + "/api/admin/stats?start=bad", headers=headers))
        assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_admin_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    server = create_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ("/admin", "/api/admin/stats"):
            with pytest.raises(HTTPError) as exc:
                urlopen(f"http://127.0.0.1:{server.server_port}" + path)
            assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
