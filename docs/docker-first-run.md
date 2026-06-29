# Docker 首次启动指南

这份文档给第一次加入项目的新成员使用。目标是从 GitHub 私有仓库拉取源码后，用 Docker Compose 在本地快速启动完整前后端。

## 需要先安装

- Git
- Docker Desktop
- 已被邀请进入 GitHub 私有仓库的 GitHub 账号

启动前请先打开 Docker Desktop，并确认 Docker 已经运行。

检查命令：

```bash
docker --version
docker compose version
docker ps
```

如果 `docker ps` 提示无法连接 Docker daemon，通常是 Docker Desktop 没有启动。

## 首次拉取项目

```bash
git clone https://github.com/111qaz1/functional-medicine-ai.git
cd functional-medicine-ai
```

如果使用 Windows PowerShell，也可以直接进入自己想放项目的目录后执行以上命令。

## 准备环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell 也可以使用：

```powershell
Copy-Item .env.example .env
```

默认 `.env` 可以让系统启动，但不包含真实模型 API Key。如果需要图片识别或大模型增强，需要负责人单独提供相关配置。

## 可选资料目录

如果负责人提供了外部资料包，在项目根目录创建 `knowledge/`，然后把资料放进去：

```bash
mkdir knowledge
```

说明：

- `knowledge/` 不进入 Git。
- 没有 `knowledge/` 资料包时，系统仍能启动。
- 仓库里的 `backend/app/data/` 已包含整理后的结构化 JSON 知识数据。

## 启动项目

首次启动或依赖变化后，建议使用：

```bash
docker compose up --build -d
```

日常再次启动可以使用：

```bash
docker compose up -d
```

启动完成后访问：

- 前端工作台：`http://localhost:3000`
- 后端健康检查：`http://localhost:8000/health`

健康检查正常时会返回：

```json
{"status":"ok"}
```

## 常用命令

查看容器状态：

```bash
docker compose ps
```

查看运行日志：

```bash
docker compose logs -f
```

只看后端日志：

```bash
docker compose logs -f backend
```

停止项目：

```bash
docker compose down
```

重新构建并启动：

```bash
docker compose up --build -d
```

## 常见问题

### 端口被占用

如果 `3000` 或 `8000` 端口被占用，先检查是否已经启动过本项目：

```bash
docker compose ps
```

如果需要停止旧容器：

```bash
docker compose down
```

### 前端能打开但请求后端失败

检查 `.env` 里的配置：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

本地 Docker 启动时一般保持这个默认值即可。

### 图片识别或模型增强不可用

确认 `.env` 里是否配置了：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_API_STYLE=auto
```

如果使用千问/通义千问，推荐配置为：

```env
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=你的千问API Key
LLM_MODEL=qwen-plus
LLM_API_STYLE=chat
```

本地脚本启动方式：

```powershell
.\scripts\start-local-qwen.cmd
```

真实 API Key 不放入 Git，需要负责人单独提供。

### 修改代码后没有生效

生产 Docker 镜像不会像本地开发模式一样自动热更新。修改代码后需要重新构建：

```bash
docker compose up --build -d
```

## 新成员验证清单

- 能打开 `http://localhost:3000`
- `http://localhost:8000/health` 返回 `{"status":"ok"}`
- 能进入公共工作台
- 能创建验收病例
- 能看到产品规则、医生规则或相关管理页面

如果以上都正常，说明本地 Docker 复现成功。
