# Video Report Agent

[English](README.md) | 简体中文

本地产品：Bilibili URL → yt-dlp → FFmpeg → MLX Whisper → Canonical Transcript（规范化转写稿）→ transcript.md → Pi RPC → DeepSeek + video-report skill → report.html。

## 运行

需要 Apple Silicon macOS（MLX）、Python 3.12、PATH 中可用的 FFmpeg/FFprobe，以及 PATH 中可用的 Pi 0.85.0。ASR 模型为 `mlx-community/whisper-large-v3-turbo`；首次运行时可能会下载该模型。

```sh
uv sync
cp .env.example .env
# 设置 PI_PROVIDER、PI_MODEL 和 PI_API_KEY。将 PI_API_KEY 留空时会使用项目 config/pi/auth.json。
uv run video-report web --port 8765
uv run video-report generate 'https://www.bilibili.com/video/BV.../'
```

打开 http://127.0.0.1:8765。默认转写模式为 `asr-only`，OCR 默认为 `off`。可选的 `fused` 模式会保留字幕发现、SRT/VTT/ASS 输入、RapidOCR PP-OCRv6 Small、保守融合（Fusion）以及规范化的来源溯源信息。可以在 UI 中启用该模式，也可以使用 `generate URL --transcript-mode fused --ocr-mode auto`。如需指定 ROI，可使用 `--ocr-mode roi --ocr-roi 0.05,0.72,0.95,0.98`（`on` 是 `roi` 的别名）。`--subtitle-file path.srt` 用于导入本地字幕。运行 `uv sync --extra enhancement` 可安装可选的 OCR 依赖。

现有 UI 支持输入 URL、轮询处理进度、打开报告以及列出已完成的报告。一个 worker 会按顺序处理各个 run。重启时，未完成的 run 会被标记为失败；已完成的报告仍然可用。

`runs/<id>/` 中包含 `input.json`、下载的媒体文件、`audio.wav`、原始 `asr.json`、带时间戳的 `transcript.md`、`SKILL.md`、从正式 skill 复制的 `assets/report-template.html`、Pi 会话与事件日志、`status.json` 以及 `report.html`。失败的 run 会完整保留。将 `--runs PATH` 放在命令之前，可以选择其他 run 根目录。

Pi 会以 `--mode rpc` 启动，并使用项目中的 `PI_PROVIDER` 和 `PI_MODEL`，同时启用标准的 `read,write,edit,bash` 工具。当设置了 `PI_API_KEY` 时，适配器会通过 `--api-key` 传入该密钥；持久化的 `invocation.json` 会使用 `[redacted]` 替代真实密钥。当该变量为空时，Pi 可以使用项目 `config/pi/auth.json` 中保存的凭证。启动时始终将 `PI_CODING_AGENT_DIR` 设为项目 `config/pi/` 的绝对路径，覆盖机器上的同名设置，不再加载用户目录中的 Pi 凭证、模型和设置。系统会显式指定 skill 和 session 路径，并禁用自动扩展/上下文发现。精简调用只指向 `transcript.md` 和该 skill，不会把 skill 内容嵌入调用中。适配器会等待 `agent_settled` 和成功的最终 assistant stop，然后检查 HTML 文件是否完整。重试、上下文压缩和工具执行由 Pi 负责。

源 skill 位于 `src/video_report_agent/skills/video-report/`，该 skill 从经过人工实际运行的 transcript-report-replica skill 保留而来（仅更改了名称）。生成的报告必须是自包含的。应用不会强制执行浏览器硬门禁，也不会运行 planner/critic 流程。

Pi 的 cwd 和 session 目录都位于对应的 run 内。系统指令会将工作限制在该目录中；但标准的 bash/read/write/edit **不是操作系统级沙箱**，从技术上仍保留访问宿主机的能力。请仅将其作为受信任的本地工具使用；容器隔离将在未来实现。生成的 HTML 会通过带沙箱 CSP 的方式提供服务。不要将旧的研究媒体发布到 `artifacts/` 或 `reports/` 下。

检查命令：`uv run --extra enhancement pytest -q`、`uv run ruff check src tests`、`uv lock --check`。

## 项目级 Pi 配置

CLI 从项目根目录加载 `.env`，部署环境变量优先。自定义模型在 `config/pi/models.json` 中配置；已提供普通智谱 API 的 `zhipu/glm-5.2`。使用它时，将 `.env` 中的 `PI_PROVIDER` 改为 `zhipu`、`PI_MODEL` 改为 `glm-5.2`，并将 `PI_API_KEY` 填为普通智谱 API Key。其他自定义 provider 按相同结构添加 `baseUrl`、`api` 和 `models`；密钥使用 `$环境变量名` 引用。

如果需要交互式登录，在项目根目录运行：

```sh
PI_CODING_AGENT_DIR="$PWD/config/pi" pi
# 在 Pi 内使用 /login
```

登录凭证和 Pi 运行状态已忽略提交；仅 `models.json` 纳入版本控制。部署时携带 `config/pi/models.json`，通过环境变量注入密钥，或单独配置项目登录状态。Pi 可执行程序仍需安装，报告工作目录仍为 `runs/<id>/`。

## 媒体自动清理

默认保留最近 20 条成功任务的下载视频和 `audio.wav`，按任务完成时间（`status.json` 修改时间）排序；同一视频的多次任务分别计数。启动 Web 服务及每次任务结束时执行清理，不运行服务或任务时不会定时清理。报告、assets、字幕、转录、下载元数据和日志全部保留，失败及进行中的任务不清理。被清理的视频下次使用时会重新下载。

在 `.env` 设置 `MEDIA_KEEP_LAST=10` 可改为最近 10 条。按 7 天清理时，设置 `MEDIA_KEEP_LAST=0` 和 `MEDIA_MAX_AGE_DAYS=7`；0 表示关闭该项限制。两项都启用时，超过任一限制便清理媒体。失败任务和报告仍会占用空间，这不是整个 runs 目录的容量上限。

Pi 可执行程序来自机器 PATH 中独立安装的 Pi，RPC 是本地子进程通信方式。项目独立管理 `config/pi/` 配置和 `runs/<id>/sessions/` 会话。下载源码不会安装 Pi，`uv sync` 也不安装它；新机器须安装 README 指定版本的 Pi、FFmpeg 和 Python 环境，并配置自己的模型凭据。当前 MLX Whisper 要求 Apple Silicon macOS，普通 Linux 服务器需先替换转录后端才能运行完整流程。
