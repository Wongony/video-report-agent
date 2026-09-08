"""Project model configuration backed by the installed Pi catalog and credentials."""
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .pi import DEFAULT_MODEL, DEFAULT_PROVIDER, PI_AGENT_DIR, PiRunner

_LOCK = threading.Lock()


def _pi(request):
    executable = shutil.which("pi")
    if not executable:
        raise ValueError("未安装 Pi，无法读取模型配置")
    package = Path(executable).resolve().parent
    while not (package / "package.json").is_file() and package != package.parent:
        package = package.parent
    try:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("model_catalog.mjs")), str(package),
             str(PI_AGENT_DIR)],
            input=json.dumps(request), text=True, capture_output=True, timeout=30,
            env={**os.environ, "PI_OFFLINE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("无法读取 Pi 配置，请检查本机 Pi 安装") from exc
    if result.returncode:
        raise ValueError("Pi 配置读取失败，请检查项目 config/pi 中的模型配置")
    return json.loads(result.stdout)


def catalog():
    data = _pi({"action": "list"})
    default = {"provider": os.getenv("PI_PROVIDER", DEFAULT_PROVIDER),
               "model": os.getenv("PI_MODEL", DEFAULT_MODEL)}
    config_path = PI_AGENT_DIR / "models.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    explicit = {(p, m["id"]) for p, cfg in config.get("providers", {}).items()
                for m in cfg.get("models", [])}
    for model in data["models"]:
        model["configured"] |= (model["provider"], model["model"]) in explicit
        if all(model[k] == default[k] for k in default):
            model["configured"] = True
    data["default"] = default
    return data


def validate_selection(data):
    if not any(key in data for key in ("provider", "model", "thinking")):
        return {}
    match = next((m for m in catalog()["models"] if m["configured"]
                  and m["provider"] == data.get("provider")
                  and m["model"] == data.get("model")), None)
    if match is None:
        raise ValueError("请选择已配置的模型")
    thinking = data.get("thinking")
    if thinking not in match["thinking_levels"]:
        raise ValueError("该模型不支持所选思考强度")
    return {"provider": match["provider"], "model": match["model"], "thinking": thinking}


def save_model(data):
    key = data.get("api_key", "")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("请填写 API Key")
    with _LOCK:
        if data.get("kind") == "custom":
            for field in ("name", "url", "model"):
                if not isinstance(data.get(field), str) or not data[field].strip():
                    raise ValueError("请填写名称、URL 和具体模型")
            url = urlsplit(data["url"].strip())
            if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                raise ValueError("URL 必须是完整的 HTTP 或 HTTPS 接口地址")
            provider = "custom-" + uuid.uuid4().hex[:12]
            path = PI_AGENT_DIR / "models.json"
            config = json.loads(path.read_text()) if path.exists() else {}
            model = {"id": data["model"].strip(), "name": data["name"].strip(),
                     "reasoning": data.get("reasoning") is True, "input": ["text"]}
            config.setdefault("providers", {})[provider] = {
                "baseUrl": data["url"].strip().rstrip("/"), "api": "openai-completions",
                "models": [model],
            }
            PI_AGENT_DIR.mkdir(parents=True, exist_ok=True)
            _pi({"action": "save-key", "provider": provider, "api_key": key.strip()})
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2))
            temporary.replace(path)
            return {"provider": provider, "model": model["id"]}
        if data.get("kind") != "builtin":
            raise ValueError("请选择 Pi Provider 或自定义")
        match = next((m for m in catalog()["models"]
                      if m["provider"] == data.get("provider")
                      and m["model"] == data.get("model")), None)
        if not match or not match["supports_api_key"]:
            raise ValueError("请选择有效的 Provider 和模型")
        _pi({"action": "save-key", "provider": match["provider"], "api_key": key.strip()})
        return {"provider": match["provider"], "model": match["model"]}


def check_connection(data):
    selection = validate_selection(data)
    if not selection:
        raise ValueError("请选择已配置的模型")
    runner = PiRunner(**selection)
    try:
        result = _pi({"action": "check", **selection, "api_key": runner.api_key})
    except ValueError:
        result = {"connected": False}
    return {"connected": result.get("connected") is True,
            "error": None if result.get("connected") is True else
            "未能连接模型服务，请检查 API Key、服务地址及模型权限后重试。"}
