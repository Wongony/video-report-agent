# Video Report Agent

[简体中文](README.zh-CN.md) | English

Local product: Bilibili URL → yt-dlp → FFmpeg → MLX Whisper → Canonical Transcript → transcript.md → Pi RPC → DeepSeek + video-report skill → report.html.

## Run

Requires Apple Silicon macOS (MLX), Python 3.12, FFmpeg/FFprobe on PATH and Pi 0.85.0 on PATH. The ASR model is `mlx-community/whisper-large-v3-turbo`; the first run may download it.

```sh
uv sync
cp .env.example .env
# Set PI_PROVIDER, PI_MODEL and PI_API_KEY. Leave PI_API_KEY empty to use project config/pi/auth.json.
uv run video-report web --port 8765
uv run video-report generate 'https://www.bilibili.com/video/BV.../'
```

Open http://127.0.0.1:8765. Default transcript mode is `asr-only`, OCR `off`. Optional `fused` preserves subtitle discovery, SRT/VTT/ASS input, RapidOCR PP-OCRv6 Small, conservative Fusion and canonical source provenance. Enable it in the UI or use `generate URL --transcript-mode fused --ocr-mode auto`; explicit ROI uses `--ocr-mode roi --ocr-roi 0.05,0.72,0.95,0.98` (`on` aliases `roi`). `--subtitle-file path.srt` imports a local subtitle. `uv sync --extra enhancement` installs the optional OCR dependencies.

The existing UI accepts URLs, polls progress, opens reports and lists completed reports. One worker processes runs sequentially. Restarting marks unfinished runs failed; completed reports remain available.

`runs/<id>/` contains input.json, downloaded media, audio.wav, raw asr.json, timestamped transcript.md, SKILL.md and assets/report-template.html copied from the formal skill, Pi sessions and event logs, status.json, and report.html. Failed runs remain intact. `--runs PATH` before the command chooses another run root.

Pi is started with `--mode rpc`, the project `PI_PROVIDER` and `PI_MODEL`, and standard `read,write,edit,bash` tools. When `PI_API_KEY` is set, the adapter passes it through `--api-key`; the persisted `invocation.json` contains `[redacted]` instead of the key. When it is empty, Pi can use the selected provider's credential from project `config/pi/auth.json`. `PI_CODING_AGENT_DIR` is always set to the absolute project `config/pi/` path, overriding any machine setting; user-level Pi credentials, models and settings are not loaded. Explicit skill and session paths are used, and automatic extension/context discovery is disabled. The thin invocation points to transcript.md and the skill; it does not embed the skill. The adapter waits for `agent_settled` and a successful final assistant stop, then checks for a complete HTML file. Pi owns retries, compaction and tool execution.

Source skill: `src/video_report_agent/skills/video-report/`, preserved from the manually exercised transcript-report-replica skill (renamed only). Generated reports must be self-contained. The application does not impose a browser hard gate or run a planner/critic pipeline.

Pi's cwd and session directory are inside the run. A system instruction confines work there; standard bash/read/write/edit are **not an OS sandbox** and technically retain host access. Use only as a trusted local tool; container isolation remains future work. Generated HTML is served with a sandbox CSP. Do not publish old research media under artifacts/ or reports/.

Checks: `uv run --extra enhancement pytest -q`, `uv run ruff check src tests`, `uv lock --check`.

## Project Pi configuration

The CLI loads `.env` from the project root; deployment environment variables take precedence. Edit `config/pi/models.json` for custom providers. The included `zhipu/glm-5.2` uses the ordinary BigModel API. Select it with `PI_PROVIDER=zhipu`, `PI_MODEL=glm-5.2`, and your ordinary API key in `PI_API_KEY`. Add other providers with their `baseUrl`, `api`, and `models`; reference secrets as `$ENV_VAR`.

For interactive login, run from the project root:

```sh
PI_CODING_AGENT_DIR="$PWD/config/pi" pi
# Use /login inside Pi.
```

Only `config/pi/models.json` is versioned; credentials and Pi runtime state are ignored. Deploy this config with the project and supply credentials through environment variables or project login. The Pi executable must still be installed. Report workspaces remain `runs/<id>/`.

## Media retention

By default, keep media for the latest 20 successful runs, ordered by completion (`status.json` modification time). Repeated videos count as separate runs. Cleanup runs on web startup and after each generation, removing only downloaded MP4 files and `audio.wav`. Reports, assets, transcripts, subtitles, metadata and logs remain; failed and active runs are untouched. Removed videos must be downloaded again for later runs.

Set `MEDIA_KEEP_LAST=10` for ten runs, or `MEDIA_KEEP_LAST=0` and `MEDIA_MAX_AGE_DAYS=7` for seven days. Zero disables a limit; if both are enabled, either limit expires media. Cleanup does not run while the application is stopped. Preserved reports and failed runs still consume storage.
