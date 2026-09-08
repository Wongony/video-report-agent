import json

import pytest

from video_report_agent import model_config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(model_config, "PI_AGENT_DIR", tmp_path)
    return tmp_path


def test_custom_model_uses_pi_catalog_and_private_auth(isolated_config):
    selected = model_config.save_model({
        "kind": "custom", "name": "Test model", "url": "https://example.com/v1",
        "model": "test-model", "api_key": "!literal-secret", "reasoning": True,
    })
    config = (isolated_config / "models.json").read_text()
    assert "literal-secret" not in config
    provider = json.loads(config)["providers"][selected["provider"]]
    assert provider["api"] == "openai-completions"
    auth = json.loads((isolated_config / "auth.json").read_text())
    assert auth[selected["provider"]] == {"type": "api_key", "key": "!literal-secret"}
    assert (isolated_config / "auth.json").stat().st_mode & 0o077 == 0
    catalog = model_config.catalog()
    assert "literal-secret" not in json.dumps(catalog)
    model = next(m for m in catalog["models"] if m["provider"] == selected["provider"])
    assert model["configured"]
    assert "high" in model["thinking_levels"]
    assert model_config.validate_selection({**selected, "thinking": "high"}) == {
        **selected, "thinking": "high",
    }
    with pytest.raises(ValueError, match="思考强度"):
        model_config.validate_selection({**selected, "thinking": "nonsense"})


def test_builtin_key_keeps_existing_models(isolated_config):
    original = {"providers": {}}
    (isolated_config / "models.json").write_text(json.dumps(original))
    builtin = next(m for m in model_config.catalog()["models"] if m["provider"] == "openai")
    selected = model_config.save_model({
        "kind": "builtin", "provider": "openai", "model": builtin["model"],
        "api_key": "test-secret",
    })
    assert selected["provider"] == "openai"
    assert json.loads((isolated_config / "models.json").read_text()) == original
    assert next(m for m in model_config.catalog()["models"]
                if m["provider"] == "openai")["configured"]


def test_invalid_config_does_not_save_key(isolated_config):
    with pytest.raises(ValueError):
        model_config.save_model({"kind": "builtin", "provider": "missing", "api_key": "secret"})
    assert "secret" not in (isolated_config / "auth.json").read_text()


def test_connection_uses_selected_credentials_and_handles_failure(monkeypatch):
    selection = {"provider": "custom-test", "model": "model", "thinking": "low"}
    monkeypatch.setattr(model_config, "validate_selection", lambda data: selection)
    monkeypatch.setenv("PI_API_KEY", "unrelated-secret")
    requests = []

    def probe(data):
        requests.append(data)
        return {"connected": True}

    monkeypatch.setattr(model_config, "_pi", probe)
    assert model_config.check_connection(selection)["connected"]
    assert requests == [{"action": "check", **selection, "api_key": None}]
    monkeypatch.setattr(model_config, "_pi", lambda data: {"connected": False})
    result = model_config.check_connection(selection)
    assert not result["connected"]
    assert "未能连接模型服务" in result["error"]


def test_connection_real_pi_transport_with_local_stub(isolated_config):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.headers.get("Authorization"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1,
                     "model": "test-model", "choices": [{"index": 0,
                     "delta": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    selected = model_config.save_model({
        "kind": "custom", "name": "Stub", "url": f"http://127.0.0.1:{server.server_port}/v1",
        "model": "test-model", "api_key": "stub-secret", "reasoning": True,
    })
    try:
        assert model_config.check_connection({**selected, "thinking": "low"})["connected"]
        assert received[0][0] == "Bearer stub-secret"
        assert received[0][1]["model"] == "test-model"
        assert received[0][1]["reasoning_effort"] == "low"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert not model_config.check_connection({**selected, "thinking": "low"})["connected"]
