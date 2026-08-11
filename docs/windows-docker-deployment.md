# Windows + Docker 部署说明

适用系统：Windows 10 22H2 或 Windows 11，使用 Docker Desktop 的 WSL 2 后端。

## 1. 安装环境

以管理员身份打开 PowerShell：

```powershell
wsl --install
winget install -e --id Git.Git
winget install -e --id Docker.DockerDesktop
```

安装完成后重启 Windows，启动 Docker Desktop，并确认使用 Linux containers。

```powershell
wsl --version
git --version
docker version
docker compose version
```

Docker Desktop 建议分配不少于 8 GB 内存。

## 2. 下载项目

```powershell
New-Item -ItemType Directory -Force C:\apps | Out-Null
Set-Location C:\apps
git clone --branch main https://github.com/111qaz1/functional-medicine-ai.git
Set-Location .\functional-medicine-ai
```

私有仓库需使用有权限的 GitHub 账号完成认证。

## 3. 配置环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

确认以下配置：

```env
BACKEND_PORT=7800
FRONTEND_PORT=3100
FM_PUBLIC_BASE_URL=http://localhost:3100
FM_SESSION_COOKIE_SECURE=0

LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_API_KEY=替换为Kimi_API_Key
LLM_MODEL=kimi-k2.6
LLM_API_STYLE=chat

FM_LLM_MAX_CONCURRENCY=90
FM_LLM_RPM_SOFT_LIMIT=475
FM_LLM_TPM_SOFT_LIMIT=2850000
FM_ANALYSIS_WORKERS=20
FM_CASE_DOCUMENT_WORKERS=2

FM_MAX_UPLOAD_MB=50
FM_MAX_PDF_PAGES=50
FM_EXTERNAL_TRUST_SHARED_SECRET=替换为随机高强度字符串
```

浏览器请求统一发送到 Next.js 的同源路径，再由前端容器转发至 FastAPI。不要再设置 `NEXT_PUBLIC_API_BASE_URL`，也不要把 `7800` 端口写入浏览器端配置。

- `INTERNAL_API_BASE_URL` 是 Next.js 服务端变量。Compose 默认设置为 `http://backend:8000`，通常不要在 `.env` 中覆盖。
- `FM_PUBLIC_BASE_URL` 只用于外部 API 生成绝对下载地址。本机部署填写 `http://localhost:3100`；通过 HTTPS 域名访问时填写正式域名，例如 `https://fm.example.com`。
- 通过 HTTPS 部署时设置 `FM_SESSION_COOKIE_SECURE=1`；仅在本机 HTTP 或临时局域网 HTTP 环境使用 `0`。
- `BACKEND_PORT=7800` 只开放在宿主机回环地址，供本机排障使用，不应映射到公网。

如果没有 `bge-m3` 模型文件，在 `.env` 中设置：

```env
FM_RAG_ENABLED=0
FM_RAG_LLM_FUSION_ENABLED=0
```

如果已有模型，将模型文件复制到项目根目录的 `bge-m3` 文件夹，并保持：

```env
FM_RAG_ENABLED=1
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
```

创建持久化目录：

```powershell
New-Item -ItemType Directory -Force .runtime, .runtime\uploads, knowledge, bge-m3 | Out-Null
```

## 4. 构建并启动

```powershell
docker compose config
docker compose up -d --build
docker compose ps
```

首次构建需要下载 Python、Node.js、PyTorch 等依赖。

如果只修改 `.env`，使用下面的命令重建容器即可，不需要删除镜像或数据：

```powershell
docker compose up -d --force-recreate
```

如果修改了代码、Dockerfile 或依赖，则必须带 `--build`：

```powershell
docker compose up -d --build --remove-orphans
```

## 5. 验证部署

```powershell
Invoke-RestMethod http://127.0.0.1:7800/health
Invoke-RestMethod http://127.0.0.1:3100/health
Start-Process http://localhost:3100
```

健康检查应返回：

```json
{"status":"ok"}
```

浏览器访问：`http://localhost:3100`。

第一个健康检查用于验证宿主机到 FastAPI 的本地排障链路；第二个用于验证实际的 `Next.js -> backend:8000` 转发链路。对外部署时只公开 Next.js/Nginx 入口，不公开 `7800`。

## 6. 常用命令

```powershell
# 查看状态
docker compose ps

# 查看全部日志
docker compose logs -f

# 查看后端日志
docker compose logs -f backend

# 重启
docker compose restart

# 停止并保留数据
docker compose down

# 更新代码并重新部署
git pull --ff-only
docker compose up -d --build
```

## 7. 数据备份

运行数据保存在项目根目录 `.runtime` 中。

```powershell
docker compose stop
$backupName = "runtime-backup-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss")
Compress-Archive -Path .runtime -DestinationPath $backupName
docker compose start
```

恢复时停止容器，将备份内容还原到 `.runtime` 后重新启动。

## 8. 故障排查

```powershell
# 容器未启动或反复重启
docker compose ps
docker compose logs --tail 200 backend
docker compose logs --tail 200 frontend

# 重新构建
docker compose down
docker compose build --no-cache
docker compose up -d
```

### Docker Desktop 拉取镜像超时或残留代理

如果日志包含 `Docker Desktop has no HTTPS proxy`、`registry-1.docker.io:443` 超时，或之前使用的本机代理端口（例如 `7897`）已经关闭：

1. 打开 Docker Desktop 的 `Settings -> Resources -> Proxies`。
2. 不需要代理时，将 `Docker Desktop proxy` 和 `Containers proxy` 都设置为 `No proxy`；需要代理时选择 `System proxy`，或在 `Manual configuration` 中填写当前确实可用的 HTTP/HTTPS 代理地址。
3. 不要保留已经停止监听的旧代理端口。应用设置并重启 Docker Desktop。
4. 分别验证基础镜像拉取和 Compose 构建：

```powershell
docker pull node:22-alpine
docker pull python:3.12-slim
docker compose build
docker compose up -d
```

`Docker Desktop proxy` 负责 Desktop/CLI 等宿主侧流量；`Containers proxy` 会用于镜像拉取，因此两处配置不一致时仍可能出现 `docker compose build` 超时。

代理模式的含义和界面位置以 [Docker Desktop 代理设置文档](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies) 为准。

- 前端无法调用后端：检查 `docker compose logs frontend`，并确认前端容器中的 `INTERNAL_API_BASE_URL` 为 `http://backend:8000`。
- Kimi 请求失败：检查 API Key、账户额度和服务器网络。
- RAG 模型缺失：复制完整模型到 `bge-m3`，或关闭 `FM_RAG_ENABLED`。
- 端口冲突：修改 `.env` 中的 `BACKEND_PORT`、`FRONTEND_PORT`；如使用外部报告下载地址，同时更新 `FM_PUBLIC_BASE_URL`。

Docker Desktop 安装要求以 [Docker 官方文档](https://docs.docker.com/desktop/setup/install/windows-install/) 为准。
