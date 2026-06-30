# 接口交付与部署使用说明

本文档用于说明功能医学营养推荐系统的接口交付内容、部署步骤、模型资产、接口鉴权、接口调用流程和验收标准。

## 1. 交付内容

本次以 Git 仓库形式交付项目源码与部署配置。交付仓库包含：

- `backend/`：FastAPI 后端服务。
- `frontend/`：Next.js 前端工作台。
- `docs/`：部署、接口、安全和系统说明文档。
- `postman/`：外部接口调用示例。
- `scripts/`：本地启动和辅助脚本。
- `knowledge/`：项目运行所需的本地知识目录。
- `compose.yaml`：Docker Compose 编排文件。
- `.env.example`：环境变量模板。
- `README.md`：项目总说明。

以下内容不进入 Git 仓库：

- `.env`
- `.runtime/`
- `work/`
- `bge-m3/`
- 真实病例、原始报告、API Key、本地 SQLite 数据库和上传文件。

## 2. bge-m3 模型资产

RAG 检索使用 `BAAI/bge-m3` 作为本地 embedding 模型。该模型权重体积较大，不进入 Git 仓库，也不进入 Docker 镜像。

部署时需单独准备 `bge-m3/` 模型目录，并放置到项目根目录：

```text
functional-medicine-ai/
  bge-m3/
  compose.yaml
  .env
```

默认配置如下：

```text
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
```

`FM_RAG_MODEL_HOST_DIR` 是宿主机模型目录，`FM_RAG_MODEL_PATH` 是容器内模型目录。容器内路径保持 `/models/bge-m3`。

模型目录需包含完整的 `BAAI/bge-m3` 文件，例如：

```text
bge-m3/
  config.json
  modules.json
  tokenizer.json
  tokenizer_config.json
  sentence_bert_config.json
  ...
```

## 3. 部署环境要求

部署机器需安装：

- Docker Desktop 或 Docker Engine。
- Docker Compose v2。
- 可访问部署端口的浏览器或接口调用工具。

默认端口：

- 后端 API：`8000`
- 前端工作台：`3000`

端口配置写入 `.env`：

```text
BACKEND_PORT=8000
FRONTEND_PORT=3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## 4. 环境变量配置

在项目根目录复制环境变量模板：

```bash
copy .env.example .env
```

Linux 或 macOS：

```bash
cp .env.example .env
```

部署前确认以下配置：

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
FM_EXTERNAL_TRUST_SHARED_SECRET=replace-with-strong-shared-secret
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
FM_RAG_LOCAL_FILES_ONLY=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

大模型 OCR 或报告润色功能使用以下配置：

```text
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_API_STYLE=auto
```

如果部署方使用千问/通义千问，推荐配置为：

```text
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-qwen-api-key
LLM_MODEL=qwen-plus
LLM_API_STYLE=chat
```

Windows 本地非 Docker 启动可使用：

```powershell
.\scripts\start-local-qwen.cmd
```

`FM_EXTERNAL_TRUST_SHARED_SECRET` 是外部系统换取 Bearer token 的系统间共享密钥。生产环境使用双方线下约定的高强度密钥，不使用示例值。

## 5. 启动服务

在项目根目录执行：

```bash
docker compose build --no-cache
docker compose up -d
```

查看容器状态：

```bash
docker compose ps
```

健康检查：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/rag
```

`/health` 正常返回：

```json
{"status":"ok"}
```

前端工作台地址：

```text
http://localhost:3000
```

后端接口文档地址：

```text
http://localhost:8000/docs
```

正式环境建议使用 Nginx 统一代理和 HTTPS，不直接暴露 `3000/8000`，具体配置参考：

```text
docs/production-recommended-config.md
docs/nginx-production-deployment.md
```

## 6. 接口鉴权

外部接口统一挂载在：

```text
/api/v1
```

接口采用 Bearer token。外部系统先使用共享密钥签名医生身份，调用 `/api/v1/auth/token` 换取短期 token，后续接口携带：

```http
Authorization: Bearer <access_token>
```

签名串按以下顺序使用换行符拼接：

```text
issuer
doctor_id
doctor_name
timestamp
nonce
```

签名算法：

```text
HMAC-SHA256(shared_secret, canonical_payload).hexdigest()
```

`timestamp` 与服务器时间偏差超过 300 秒时，接口拒绝请求。部署服务器与外部系统服务器需保持时间同步。

## 7. 接口调用流程

### 7.1 换取 Token

```http
POST /api/v1/auth/token
Content-Type: application/json
```

请求示例：

```json
{
  "issuer": "external-system",
  "doctor_id": "doctor-001",
  "doctor_name": "医生姓名",
  "timestamp": 1760000000,
  "nonce": "random-nonce-001",
  "signature": "hex_hmac_sha256_signature"
}
```

响应示例：

```json
{
  "access_token": "sess_xxx",
  "token_type": "bearer",
  "expires_in_days": 14,
  "doctor_id": "doctor_ext_xxx",
  "display_name": "医生姓名"
}
```

### 7.2 创建病例

```http
POST /api/v1/cases
Authorization: Bearer <access_token>
Content-Type: application/json
```

请求示例：

```json
{
  "customer_name": "客户姓名",
  "consultant_id": "顾问姓名",
  "notes": "备注",
  "analysis_mode": "llm_primary"
}
```

响应中返回 `case_id`。

### 7.3 上传附件

```http
POST /api/v1/cases/{case_id}/attachments
Authorization: Bearer <access_token>
Content-Type: multipart/form-data
```

表单字段：

- `files`：一个或多个附件。
- `attachment_type`：`case` 或 `questionnaire`。

`case` 用于上传体检报告、检验报告、病例资料；`questionnaire` 用于上传已填写问卷。

### 7.4 生成营养推荐草案

```http
POST /api/v1/cases/{case_id}/nutrition-recommendations
Authorization: Bearer <access_token>
```

返回内容包括：

- `draft_id`
- `manual_review_required`
- `confidence`
- `recommendations`
- `contraindications`
- `missing_info`

该接口只生成草案，不自动发布最终报告。草案需人工审核后发布。

### 7.5 获取最近一次推荐草案

```http
GET /api/v1/cases/{case_id}/nutrition-recommendations/latest
Authorization: Bearer <access_token>
```

### 7.6 获取报告下载地址

```http
GET /api/v1/drafts/{draft_id}/report-download
Authorization: Bearer <access_token>
```

已审核发布的草案返回下载地址。未审核草案返回 `409`。

### 7.7 下载 PDF 报告

```http
GET /api/v1/drafts/{draft_id}/report.pdf
Authorization: Bearer <access_token>
```

## 8. Postman 验证

项目提供 Postman 示例：

- `postman/external_api.postman_collection.json`
- `postman/external_api.local.postman_environment.json`
- `postman/fixtures/labs.txt`

使用前将环境文件中的 `base_url`、`shared_secret` 改成实际部署值。

Newman 验证命令：

```bash
npx newman run postman/external_api.postman_collection.json -e postman/external_api.local.postman_environment.json --working-dir .
```

## 9. 验收标准

交付验收覆盖以下项目：

1. `docker compose build --no-cache` 执行成功。
2. `docker compose up -d` 后后端和前端容器正常运行。
3. `/health` 返回 `{"status":"ok"}`。
4. `/health/rag` 显示索引和模型加载状态正常。
5. 外部系统通过签名换取 Bearer token。
6. 外部系统通过接口创建病例、上传附件、生成推荐草案。
7. 未审核草案不能直接下载报告。
8. 人工审核发布后下载 PDF 报告。
9. Docker 镜像中不包含测试目录、真实病例、`.env`、运行时数据库和模型权重。

## 10. 故障排查

### bge-m3 目录不存在

现象：后端启动后 `/health/rag` 显示模型不可用，或日志提示无法加载 `/models/bge-m3`。

处理：

- 确认项目根目录存在 `bge-m3/`。
- 确认 `.env` 中 `FM_RAG_MODEL_HOST_DIR` 指向正确模型目录。
- 确认模型目录不是空目录，且包含 `config.json`、`modules.json`、`tokenizer.json` 等文件。
- 修改后重启后端容器。

### Token 换取失败

处理：

- 确认双方使用同一个 `FM_EXTERNAL_TRUST_SHARED_SECRET`。
- 确认签名串字段顺序和换行符完全一致。
- 确认服务器时间同步，时间偏差不超过 300 秒。
- 确认 `nonce` 长度不少于 8 位。

### 前端能打开但接口失败

处理：

- 确认 `.env` 中 `NEXT_PUBLIC_API_BASE_URL` 是浏览器可访问的后端地址。
- 修改该变量后重新构建前端镜像。

### 大模型不可用

处理：

- 未配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 时，系统回退到本地规则流程。
- 图片 OCR 或大模型润色功能需填写可用模型服务配置。
- 模型调用失败不会绕过本地产品目录、禁忌规则和人工审核边界。
