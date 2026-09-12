# Video Report Agent

线上网站 👉 [vreport.tri4t.xyz](https://vreport.tri4t.xyz)

[English](README.md) | 简体中文

把视频转写整理成自包含的 HTML 精读报告。**只需 ASR + video-report Skill，就可以在自己的 Coding Agent 中生成报告，无需部署整个项目。**

开发者在 Apple Silicon Mac 上使用 MLX Whisper 完成本地转写。这是开发者的运行环境；Windows 用户可以选择适合自己设备的 ASR 模型，也可以调用语音转写 API，再把文字稿交给 Skill。

## 快速开始：在自己的 Coding Agent 中生成报告

> 视频 / 音频 → 自选 ASR → 转写文本 → Coding Agent + video-report Skill → report.html

### 1. 准备转写文本

用你自己的 ASR 工具或 API 把视频、音频转成文字，保存为 `transcript.md`、TXT 或 SRT 等 Agent 能读取的文件。已有完整字幕或转写时可以直接复用，无需重新识别。

- Apple Silicon：开发者使用 MLX Whisper。
- Windows 或其他平台：使用与你的硬件、系统兼容的本地 ASR，或接入云端语音转写 API。
- 尽量保留时间戳、说话人和完整正文。没有可靠时间戳也可以生成报告，但不应编造章节时间。

ASR 负责把语音转成文字，Coding Agent 中的模型负责阅读、整理和生成 HTML；两者可以分别选择。

### 2. 获取 Skill 和模板

下载仓库，或只取出 [`src/video_report_agent/skills/video-report/`](src/video_report_agent/skills/video-report/) 整个目录。保留 `SKILL.md` 与 `assets/` 的相对位置，不能只复制提示词而漏掉模板。

在自己的工作目录中放置：

```text
my-report/
├── transcript.md
├── video-report/
│   ├── SKILL.md
│   └── assets/
│       └── report-template.html
└── output/
```

无需安装本项目的 Python 后端、Pi RPC 或 Web UI。使用能够读取本地文件、写入 HTML 的 Coding Agent，并配置好它自己的模型即可。支持 Skill 的 Agent 可以按其约定安装该目录；也可以直接要求 Agent 读取文件中的说明。

### 3. 把任务交给 Agent

在上述工作目录中打开 Coding Agent，发送：

```text
请读取 video-report/SKILL.md，并按照其中的要求，
使用 video-report/assets/report-template.html 作为默认样式底座，
完整阅读 transcript.md，生成自包含的 HTML 精读报告。

本次输出目录为 output/，最终文件为 output/report.html。
保留来源中的关键条件、数字、公式、人物归属和不确定性。
有可靠时间戳时标注章节时间，没有时不要编造。
不要修改原始转写。若可使用浏览器，请检查页面布局；
否则说明仅完成了静态检查。
```

用浏览器打开 `output/report.html` 即可阅读。Skill 提供内容组织、来源准确性与版式约束；实际效果取决于转写质量、Agent 的模型与可用工具。Skill 本身不包含 ASR 引擎，也不会自动配置你的转写服务。

## 效果展示

**Web 主页 · 默认状态**

![Web 前端主页默认状态](docs/images/web-home.png)

**报告示例 ·《削藩与分配：中国财政再平衡的逻辑与路径》**

下图展示报告顶部，完整长图通过链接查看，避免在 README 中展开整篇。

[![HTML 精读报告顶部预览](docs/images/report-preview.png)](docs/examples/report.png)

↗ 🔗 [HTML 报告](docs/examples/report.html) · [PNG 完整长图](docs/examples/report.png)

在 GitHub 中，HTML 链接打开文件页面；下载后用浏览器打开即可阅读。示例文件随仓库提供，无需启动本地服务。

## 可选：运行完整的本地项目

如果需要粘贴 Bilibili 链接后自动下载、转写、生成报告并查看历史，可以运行本仓库的本地 Web UI。

> Bilibili URL → yt-dlp → FFmpeg → ASR → Canonical Transcript → Pi RPC + Skill → report.html

### 环境与安装

需要 Python 3.12、uv、已加入 `PATH` 的 `ffmpeg` / `ffprobe`，以及 Pi 0.85.0 和报告模型凭证。`uv sync` 不会安装 Pi。以下命令在仓库根目录执行。

Apple Silicon 使用本地 MLX：

```sh
uv sync --extra mlx
uv run playwright install chromium
```

使用已内置的云端 ASR 路径时，无需安装 MLX：

```sh
uv sync
uv run playwright install chromium
```

将 `.env.example` 复制为 `.env`，设置报告模型：

```dotenv
PI_PROVIDER=deepseek
PI_MODEL=deepseek-flash
PI_API_KEY=your-api-key
```

### 选择 ASR

开发者的 Apple Silicon 本地配置：

```dotenv
ASR_BACKEND=mlx
ASR_MODEL=mlx-community/whisper-large-v3-turbo
```

使用已内置的百炼文件转写 API：

```dotenv
ASR_BACKEND=paraformer
ASR_MODEL=paraformer-v2
DASHSCOPE_API_KEY=your-dashscope-api-key
```

`paraformer` 后端支持 `paraformer-v1`、`paraformer-v2` 及代码中已适配的 Fun-ASR 文件转写模型。修改环境配置后需重启本地服务。云端 ASR 会上传音频，报告模型会接收用于报告生成的转写内容。

**独立使用 Skill 时，可以自由选择外部 ASR；接入本项目流水线时，目前仅内置 `mlx` 和 `paraformer` 后端。** 其他本地模型或 API 需要新增相应适配，不能仅修改模型名称就直接使用。Windows 用户可以先按前面的 Skill 路径生成报告；本文不将完整项目的 Windows 原生运行视为已验证能力。

### 启动与生成

```sh
uv run video-report web --port 8765
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)，粘贴公开的 Bilibili HTTPS 链接或完整 BV 号，点击“生成 Visual Report”。多 P 视频可用 `?p=2` 选择分 P。

也可以使用 CLI：

```sh
uv run video-report generate 'https://www.bilibili.com/video/BV...' --transcript-mode asr-only
```

产物保存在 `runs/<run-id>/`，包括 `transcript.md`、来源记录和 `report.html`。本地 Web UI 还提供通过 Chromium 生成的报告长图 `report.png`。

## 项目行为与边界

- 默认 ASR-only，关闭 OCR。可选 `fused` 模式用于字幕导入、OCR 与融合；相关依赖通过 `uv sync --extra enhancement` 安装，使用 MLX 时同时保留 `--extra mlx`。
- 每次生成使用独立的 `runs/<run-id>/` 工作目录，Pi 负责 Agent 循环、工具调用与上下文管理，Skill 负责报告编辑要求。
- 视频最长 3 小时，单次任务执行期限为 30 分钟，不含排队时间。本地任务停止后，已提交的云端 ASR 可能继续执行并计费。
- 下载入口支持公开 Bilibili 视频，不提供登录或私有视频访问。生成的内容仍需结合原始来源判断准确性。
- Pi 使用本机文件与命令工具；独立工作目录是一种工作约定，不是操作系统沙箱。

## 开发检查

```sh
uv run --extra enhancement pytest -q
uv run ruff check src tests
uv lock --check
```

报告写作规则见 [`video-report/SKILL.md`](src/video_report_agent/skills/video-report/SKILL.md)，默认样式见 [`report-template.html`](src/video_report_agent/skills/video-report/assets/report-template.html)。
