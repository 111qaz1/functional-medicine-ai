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
git clone --branch dev1 https://github.com/111qaz1/functional-medicine-ai.git
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

## 5. 验证部署

```powershell
Invoke-RestMethod http://127.0.0.1:7800/health
Start-Process http://localhost:3100
```

健康检查应返回：

```json
{"status":"ok"}
```

浏览器访问：`http://localhost:3100`。

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

- 前端无法调用后端：检查 `docker compose logs frontend`，并确认前端容器中的 `INTERNAL_API_BASE_URL` 为 `http://backend:8000`。
- Kimi 请求失败：检查 API Key、账户额度和服务器网络。
- RAG 模型缺失：复制完整模型到 `bge-m3`，或关闭 `FM_RAG_ENABLED`。
- 端口冲突：修改 `.env` 中的 `BACKEND_PORT`、`FRONTEND_PORT`；如使用外部报告下载地址，同时更新 `FM_PUBLIC_BASE_URL`。

Docker Desktop 安装要求以 [Docker 官方文档](https://docs.docker.com/desktop/setup/install/windows-install/) 为准。
