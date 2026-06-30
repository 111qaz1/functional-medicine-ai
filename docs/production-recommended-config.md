# 正式部署推荐配置

本文档给甲方正式部署时使用，重点说明 `.env`、Nginx、端口、HTTPS、千问 API Key 和 RAG 模型目录如何配置。

## 1. 部署目标

正式环境建议采用：

```text
浏览器 / 甲方业务系统
        |
        | HTTPS 443
        v
Nginx
        |-- 前端页面 -> 127.0.0.1:3000
        |-- 后端接口 -> 127.0.0.1:8000
        |-- /api/v1 外部接口 -> 127.0.0.1:8000
```

对外只开放 `80/443`。`3000/8000` 只允许服务器本机访问，不直接暴露到公网。

## 2. 必备文件和资产

仓库内包含：

- `backend/`：后端服务。
- `frontend/`：前端工作台。
- `compose.yaml`：Docker Compose 编排。
- `.env.example`：环境变量模板。
- `deploy/nginx/functional-medicine-ai.conf.example`：Nginx 配置模板。
- `docs/customer-api-delivery-guide.md`：外部接口说明。

需要甲方单独准备：

- `.env`：正式环境变量，不提交 Git。
- `bge-m3/`：RAG embedding 模型目录，不提交 Git。
- 千问 API Key：由甲方自行申请和保管。
- 正式域名和 HTTPS 证书。

## 3. 推荐目录结构

```text
/opt/functional-medicine-ai/
  backend/
  frontend/
  deploy/
  docs/
  postman/
  scripts/
  bge-m3/
  compose.yaml
  .env
```

`bge-m3/` 可以放在项目根目录，也可以放在服务器其他目录。若放在其他目录，修改 `.env` 中的 `FM_RAG_MODEL_HOST_DIR` 即可。

## 4. .env 推荐配置

在项目根目录创建 `.env`：

```env
# 端口只绑定服务器本机，由 Nginx 对外代理
BACKEND_PORT=8000
FRONTEND_PORT=3000

# 正式域名。修改后必须重新构建前端镜像
NEXT_PUBLIC_API_BASE_URL=https://fm.example.com
FM_CORS_ALLOW_ORIGINS=https://fm.example.com

# HTTPS 环境建议开启安全 Cookie
FM_SESSION_COOKIE_SECURE=1

# 外部系统接口共享密钥，必须替换为双方线下约定的高强度密钥
FM_EXTERNAL_TRUST_SHARED_SECRET=replace-with-strong-shared-secret

# RAG 本地检索
FM_RAG_ENABLED=1
FM_RAG_LOCAL_FILES_ONLY=1
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
FM_RAG_INDEX_DIR=/app/backend/app/data/rag_index
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

# 千问 / DashScope
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=replace-with-qwen-api-key
LLM_MODEL=qwen-plus
LLM_API_STYLE=chat
LLM_TIMEOUT_SECONDS=45
LLM_TEMPERATURE=0.1

# 运行数据
FM_RUNTIME_DIR=/app/.runtime
FM_UPLOAD_DIR=/app/.runtime/uploads
FM_SQLITE_PATH=/app/.runtime/app.sqlite3
```

如果只是内网 HTTP 测试，还没有 HTTPS，可以临时设置：

```env
FM_SESSION_COOKIE_SECURE=0
NEXT_PUBLIC_API_BASE_URL=http://服务器IP或域名
FM_CORS_ALLOW_ORIGINS=http://服务器IP或域名
```

正式 HTTPS 上线时再改回 `FM_SESSION_COOKIE_SECURE=1`。

## 5. RAG 模型目录

默认配置：

```env
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
```

其中：

- `FM_RAG_MODEL_HOST_DIR` 是服务器上的模型目录。
- `FM_RAG_MODEL_PATH` 是容器内路径，建议保持 `/models/bge-m3`。

模型目录至少应包含：

```text
bge-m3/
  config.json
  modules.json
  sentence_bert_config.json
  tokenizer.json
  tokenizer_config.json
  model.safetensors
```

不要把 `bge-m3` 模型复制进 Git 仓库，也不要复制进 `.runtime`。

## 6. Docker Compose 启动

首次启动或配置变更后：

```bash
docker compose up --build -d
```

查看状态：

```bash
docker compose ps
```

服务器本机验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:3000
```

如果修改了 `NEXT_PUBLIC_API_BASE_URL`，必须重新构建前端：

```bash
docker compose up --build -d frontend
```

## 7. Nginx 配置

复制模板：

```bash
sudo cp deploy/nginx/functional-medicine-ai.conf.example /etc/nginx/sites-available/functional-medicine-ai.conf
sudo ln -s /etc/nginx/sites-available/functional-medicine-ai.conf /etc/nginx/sites-enabled/functional-medicine-ai.conf
```

编辑配置：

```bash
sudo nano /etc/nginx/sites-available/functional-medicine-ai.conf
```

需要替换：

- `fm.example.com`：替换为正式域名。
- `ssl_certificate`：替换为正式证书路径。
- `ssl_certificate_key`：替换为正式私钥路径。

检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 8. HTTPS 证书

如果使用 Let's Encrypt：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d fm.example.com
```

证书生成后再次检查：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 9. 端口和防火墙

正式环境建议：

- 对外开放：`80`、`443`。
- 不对外开放：`3000`、`8000`。
- `3000/8000` 只给 Nginx 在服务器本机访问。

Docker Compose 当前默认绑定：

```text
127.0.0.1:3000 -> frontend
127.0.0.1:8000 -> backend
```

如果 `.env` 中改成 `FRONTEND_PORT=3100`、`BACKEND_PORT=8100`，Nginx 代理地址也要同步改成 `127.0.0.1:3100` 和 `127.0.0.1:8100`。

## 10. 外部接口安全

外部接口使用：

```text
POST /api/v1/auth/token
```

甲方系统使用 `FM_EXTERNAL_TRUST_SHARED_SECRET` 做 HMAC 签名，换取 14 天有效的 Bearer token。

注意：

- 生产密钥不能使用示例值。
- 密钥不要写入 Git。
- 密钥建议通过双方线下安全渠道交付。
- 如果密钥泄漏，需要立即更换 `.env` 并重启后端。

## 11. 验收清单

服务器本机：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:3000
```

域名：

```bash
curl https://fm.example.com/health
curl https://fm.example.com/openapi.json
```

浏览器：

- 打开 `https://fm.example.com`。
- 登录或注册医生账号。
- 创建病例。
- 上传病例和问卷。
- 生成营养素推荐。
- 审核并下载 PDF。

外部接口：

- 使用 Postman 导入 `postman/external_api.postman_collection.json`。
- 配置正式 `base_url` 和 `shared_secret`。
- 依次验证 token、建档、上传、生成推荐、报告下载。

## 12. 常见问题

如果前端能打开但接口失败：

- 检查 `NEXT_PUBLIC_API_BASE_URL` 是否为正式域名。
- 检查修改后是否重新构建前端。
- 检查 `FM_CORS_ALLOW_ORIGINS` 是否包含正式域名。

如果登录后刷新丢失会话：

- HTTPS 环境确认 `FM_SESSION_COOKIE_SECURE=1`。
- HTTP 测试环境临时使用 `FM_SESSION_COOKIE_SECURE=0`。
- 检查 Nginx 是否传递 `X-Forwarded-Proto`。

如果 RAG 不生效：

- 检查 `FM_RAG_MODEL_HOST_DIR` 是否指向真实 `bge-m3` 目录。
- 检查容器内 `/models/bge-m3` 是否存在模型文件。
- 检查 `backend/app/data/rag_index` 是否存在索引文件。

如果千问不生效：

- 检查 `LLM_API_KEY` 是否配置。
- 检查 `LLM_BASE_URL` 是否为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 检查服务器能否访问 DashScope。
- 检查模型名是否为甲方账号可用模型。
