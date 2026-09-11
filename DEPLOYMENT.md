# Docker + Cloudflare Tunnel（Windows）

本方案用 macOS 构建 `linux/amd64` 镜像，再导入 Intel/AMD Windows 的 Docker Desktop（WSL2、Linux containers）。运行 Public 模式、Paraformer、单任务并发；无需 CUDA、MLX 或 Windows Python/Node。Windows ARM 机器需另行构建 arm64 镜像。

## 1. macOS 构建和打包

```sh
cp .env.docker.example .env.docker
# 编辑 .env.docker，填写 PI_API_KEY、DASHSCOPE_API_KEY 和模型配置
docker compose --env-file .env.docker build app
docker compose --env-file .env.docker --profile tunnel pull cloudflared
mkdir -p artifacts/windows-deployment
docker save --platform linux/amd64 -o artifacts/windows-deployment/images.tar video-report-agent:linux-amd64 cloudflare/cloudflared:latest
cp docker-compose.yml .env.docker.example DEPLOYMENT.md artifacts/windows-deployment/
```

整个 `artifacts/windows-deployment` 目录交给 Windows。`images.tar` 包含应用和 Tunnel 镜像，导入后无需在 Windows 重新编译或下载 Python 依赖。不要打包 `.env`、`.env.docker`、`config/pi/auth.json` 或已有用户数据。构建上下文采用白名单，只复制源码、锁文件、公开模型配置。

## 2. Windows 本地启动

安装并启动 Docker Desktop，启用 WSL2 引擎和 Linux containers。建议先分配 4 CPU、8 GB 内存及足够的视频存储空间。在部署目录打开 PowerShell：

```powershell
docker load -i images.tar
Copy-Item .env.docker.example .env.docker
notepad .env.docker
docker compose --env-file .env.docker up -d --no-build app
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail 100 app
```

填写两个 API Key；本地检查保留 `PUBLIC_ORIGIN=http://localhost:8765`。浏览器打开 http://localhost:8765，生成一条短视频报告，确认报告和 PNG 均可访问。默认端口只绑定本机，Tunnel 通过 Compose 内网访问应用。若修改 `WEB_PORT`，本地 `PUBLIC_ORIGIN` 也需包含对应端口。

## 3. Cloudflare 域名和 Tunnel

在 Cloudflare 管理的域名下创建 remotely-managed Tunnel，选择 Docker，复制其 token 到 `.env.docker` 的 `CLOUDFLARE_TUNNEL_TOKEN`。不要把完整安装命令填进去。

给该 Tunnel 添加 published application route：

- 域名：例如 `reports.example.com`。
- Service 类型：HTTP。
- Service URL：`app:8765`（即 `http://app:8765`，不是 localhost）。

将 `.env.docker` 中 `PUBLIC_ORIGIN` 改为 `https://reports.example.com`，不带尾部斜杠。受控 Beta 应先在 Cloudflare Access 中为这个域名建立 Self-hosted application，Allow 规则只允许指定邮箱，再启动 Tunnel。Tunnel 本身不提供用户访问授权。

```powershell
docker compose --env-file .env.docker --profile tunnel up -d --no-build
docker compose --env-file .env.docker logs --tail 100 cloudflared
```

通过 HTTPS 域名完成登录和生成检查。设置 HTTPS origin 后，使用域名操作；本地 HTTP 页面不能正确承载 Secure 会话 Cookie 和跨域提交。

Tunnel 使用出站连接，无需在路由器映射入站端口。Windows 需保持联网、避免休眠，并确保登录后 Docker Desktop 启动。`restart: unless-stopped` 只负责 Docker 引擎运行时重启容器。

官方说明：[Tunnel 设置](https://developers.cloudflare.com/tunnel/setup/)、[运行参数](https://developers.cloudflare.com/tunnel/advanced/run-parameters/)。

## 4. Linux / Paraformer 验收

真实验收会产生 Paraformer 和报告模型调用费用。使用独立 Compose 项目名及未生成过的短视频，避免复用旧转写被误认作真实 ASR：

```sh
WEB_PORT=18765 PUBLIC_ORIGIN=http://localhost:18765 docker compose --env-file .env.docker -p video-report-acceptance up -d --no-build app
docker compose --env-file .env.docker -p video-report-acceptance exec app python -c "import platform; print(platform.system(), platform.machine())"
```

在 http://localhost:18765 提交短视频；用下方命令保存验收产物：

```sh
mkdir -p artifacts/docker-acceptance
docker compose --env-file .env.docker -p video-report-acceptance cp app:/app/runs/. artifacts/docker-acceptance/
```

验收至少检查：

- Linux x86_64、Pi 0.85.0、FFmpeg 和 Chromium 可执行。
- Public 页面可访问，模型配置接口返回 403，不同浏览器身份均可查看所有已完成的历史报告；排队和运行中的任务仍按浏览器身份隔离。
- 新任务 `status.json` 为 `RENDERED`；`transcript_reused_from` 为空，`asr.json` 为 Paraformer，存在 `asr-task.json`；无 `image_error`，`report.html` 和 `report.png` 存在。
- 停止并重新启动容器后，用原 Cookie 仍能读取报告；`runs/.session-key` 和报告随数据卷保留。
- Windows 上再重复一次真实短视频生成；macOS Linux 容器通过不等于 Windows 或公网验收通过。

如果下载失败、云端返回错误或镜像未成功构建，记录失败阶段，不标记实机验收通过。已有 `asr-task.json` 的失败任务不直接重跑，先在云端查询任务，避免未知提交重复计费。

## 5. 数据、更新和备份

固定项目名 `video-report` 创建 `video-report_runs` 与 `video-report_pi` 卷。前者保存报告、下载、队列和 `.session-key`；后者保存 Pi 模型配置和运行状态。首次启动会从镜像初始化模型配置，后续镜像升级不会覆盖已有 Pi 卷；需要更改自定义 Provider 时同步更新卷内 `models.json`。API Key 每次由环境变量注入。

在 Windows PowerShell 中备份（先停止接收请求并等待生成完成）：

```powershell
docker compose --env-file .env.docker --profile tunnel stop
New-Item -ItemType Directory -Force backup
docker compose --env-file .env.docker cp app:/app/runs/. backup/runs
docker compose --env-file .env.docker cp app:/app/config/pi/. backup/pi
docker compose --env-file .env.docker --profile tunnel start
```

备份还应安全保管 `.env.docker`。恢复时先创建容器，将备份复制回对应目录并让 UID 10001 可读写，再启动服务。不要执行 `docker compose down -v`，它会删除数据卷。普通 `down` 保留卷。不要让两个服务实例共用同一个 runs 卷。

更新镜像：停止服务、`docker load -i images.tar`、再执行 `up -d --no-build`。停止最多等待 31 分钟，让现有 30 分钟执行期限结束；强制终止的 RUNNING 任务重启后会标记失败，不会自动续跑云端任务。媒体保留策略只清理成功任务的下载和音频，报告、日志、失败任务仍占空间，需要定期查看磁盘用量。
