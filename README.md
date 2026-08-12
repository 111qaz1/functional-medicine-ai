# Functional Medicine Nutrition AI

面向功能医学病例分析与营养干预审核的本地化工作台。系统负责病例资料预检、结构化提取、医生校对、身体系统分析、营养素草案、安全复核、报告发布和 PDF 导出。

## 核心原则

- 上传阶段只进行格式、大小、哈希、PDF 页数和文本层预检，不生成医学结论。
- 病例资料经用户授权后才发送至配置的大模型服务。
- 大模型负责文档语义提取和病例综合，不直接决定 SKU、剂量、禁忌或发布结果。
- 产品资格、证据准入、产品排序、禁忌、剂量档位和审核发布由本地版本化规则控制。
- 所有推荐产品均来自 `backend/app/data/product_catalog.json` 中的本地目录。
- 最终结果必须经过医生校对和审核，不能替代医学诊断或治疗。

## 主要功能

- 统一上传病例报告、问卷、检查结果和补充说明。
- 异步逐文件分析与病例级综合，支持失败重试和病例内文档缓存。
- 固定 MSQ 模板本地结构化提取，普通医疗问卷由文档模型提取并进入人工校对。
- 数值型、非数值型及患者自述病情校对，保留来源文件、页码和原文证据。
- 异常指标按身体系统归类，并区分客观异常、患者自述和背景信息。
- 本地产品目录、支持目标、系统覆盖、禁忌和安全规则匹配。
- 按病例场景选择已批准剂量档位，支持医生改档并记录备注。
- 慢性食物敏感、MSQ 等专项结果独立展示。
- 可选本地 RAG 检索和患者可见报告解释融合。
- 草案审核、发布、PDF 下载及外部 `/api/v1` 接口。

## 支持的资料格式

- 文档：PDF、DOCX、PPTX、TXT、Markdown、CSV、JSON。
- 图片：PNG、JPG/JPEG、BMP、GIF、TIF/TIFF、WebP。
- 单文件默认上限为 50 MB。
- 单个 PDF 默认最多 50 页，超出后需拆分上传。
- PPTX 可提取幻灯片中的原生 DrawingML 文本；仅存在于嵌入图片中的文字不属于原生文本提取范围。

## 技术架构

- `backend/`：FastAPI、SQLite、异步分析任务、文档解析、病例综合、推荐与报告服务。
- `frontend/`：Next.js 病例工作台、异常校对、草案审核、产品管理和系统配置界面。
- `backend/app/data/`：产品目录、剂量映射、支持目标、知识和本地规则数据。
- `.runtime/`：运行数据库、上传附件和生成结果；该目录不应提交到 Git。
- `knowledge/`：部署方提供的本地知识目录。
- `compose.yaml`：前后端 Docker Compose 编排。

## Docker 快速启动

### 1. 获取主分支

```bash
git clone --branch main https://github.com/111qaz1/functional-medicine-ai.git
cd functional-medicine-ai
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
```

至少设置模型 API Key 和外部接口共享密钥：

```env
BACKEND_PORT=7800
FRONTEND_PORT=3100
FM_PUBLIC_BASE_URL=http://localhost:3100

LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_API_KEY=替换为实际_API_Key
LLM_MODEL=kimi-k2.6
LLM_API_STYLE=chat

FM_EXTERNAL_TRUST_SHARED_SECRET=替换为随机高强度字符串
```

如果部署机没有本地 `bge-m3` 模型，将 RAG 关闭：

```env
FM_RAG_ENABLED=0
FM_RAG_LLM_FUSION_ENABLED=0
```

如果启用 RAG，请将完整模型放入项目根目录的 `bge-m3/`，并保留 `.env.example` 中的模型路径配置。

### 3. 构建并启动

```bash
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
```

默认访问地址：

- 前端工作台：`http://localhost:3100`
- 后端健康检查：`http://127.0.0.1:7800/health`
- RAG 健康检查：`http://127.0.0.1:7800/health/rag`

端口以 `.env` 中的 `FRONTEND_PORT` 和 `BACKEND_PORT` 为准。

## 业务流程

1. 创建病例并上传资料。
2. 按需填写医生病例总结。
3. 确认第三方模型处理授权并开始综合分析。
4. 校对异常指标、患者自述病情及来源证据。
5. 保存校对并生成营养素草案。
6. 审核产品、剂量档位、安全提示和系统覆盖情况。
7. 发布报告并下载 PDF。

## 模型与本地规则边界

- 文本型资料由文档模型进行语义提取；扫描 PDF 和图片可进入视觉识别路径。
- 固定 MSQ 模板优先使用本地确定性解析，歧义字段按字段或条目隔离，不清空整份问卷。
- 病例缓存按病例隔离，不在不同病例之间复用文档分析结果。
- 模型提出支持目标并引用证据，本地规则验证证据资格并映射候选产品。
- 本地系统覆盖规则可以从已验证身体系统问题补充批准的支持目标，但不能创造异常或绕过安全规则。
- 硬禁忌、用药、孕哺、年龄、肝肾安全、医生复核和剂量规则优先于覆盖数量。
- RAG 仅用于经过过滤的知识解释，不修改病例事实、异常归属、产品、剂量或禁忌。

## 关键环境变量

完整配置以 `.env.example` 为准，常用项目包括：

- `INTERNAL_API_BASE_URL`：Next.js 服务端访问后端的地址；Docker Compose 默认使用 `http://backend:8000`，通常无需手动设置。
- `FM_PUBLIC_BASE_URL`：外部接口返回绝对下载地址时使用的公网地址；正式环境建议设置为 HTTPS 域名。
- `BACKEND_PORT`、`FRONTEND_PORT`：宿主机端口。
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_API_STYLE`：模型服务配置。
- `LLM_TIMEOUT_SECONDS`、`LLM_THINKING_TIMEOUT_SECONDS`：普通请求和思考请求超时。
- `FM_LLM_RETRY_ATTEMPTS`：临时网络或服务错误的自动重试次数。
- `FM_LLM_MAX_CONCURRENCY`、`FM_LLM_RPM_SOFT_LIMIT`、`FM_LLM_TPM_SOFT_LIMIT`：全局并发及软限流。
- `FM_ANALYSIS_WORKERS`、`FM_CASE_DOCUMENT_WORKERS`：分析任务和单病例文件并发。
- `FM_MAX_UPLOAD_MB`、`FM_MAX_PDF_PAGES`：上传大小和 PDF 页数限制。
- `FM_RAG_ENABLED`、`FM_RAG_LLM_FUSION_ENABLED`、`FM_RAG_MODEL_HOST_DIR`：RAG 开关和模型路径。
- `FM_SESSION_COOKIE_SECURE`、`FM_CORS_ALLOW_ORIGINS`：生产环境 Cookie 和浏览器来源安全配置。

## 产品与知识管理

- 产品规则可在工作台的产品管理页面维护；新草案会读取保存后的目录。
- 当前仓库产品目录包含 31 个逻辑 SKU，实际交付以版本库中的目录文件为准。
- 原始 Excel、真实病例、患者附件、`.env`、`.runtime`、本地数据库和模型文件不得提交到 Git。
- 进入自动推荐的知识必须具备已审核状态；参考资料不能直接改变产品、剂量或禁忌规则。

## 验证命令

后端测试：

```bash
python -m unittest discover -s backend/tests -v
```

前端类型检查和构建：

```bash
cd frontend
npx tsc --noEmit
npm run build
```

Docker 配置检查：

```bash
docker compose config
```

## 部署文档

- [Windows + Docker 部署](docs/windows-docker-deployment.md)
- [Ubuntu + Docker 部署](docs/ubuntu-docker-deployment.md)
- [Docker 首次启动](docs/docker-first-run.md)
- [通用部署说明](docs/deployment.md)
- [Nginx 正式环境部署](docs/nginx-production-deployment.md)
- [生产环境推荐配置](docs/production-recommended-config.md)
- [外部 API 交付指南](docs/customer-api-delivery-guide.md)

## 安全与数据管理

- 医疗资料应按敏感数据管理，仅授权人员可以访问运行目录、数据库、备份和日志。
- 正式环境应使用 HTTPS、强随机密钥、受控 CORS、Secure Cookie、最小权限账号和定期备份。
- 日志不得记录患者原文、模型原始响应、API Key、服务器绝对路径或附件文件名。
- 删除病例、附件或运行目录前应确认备份和保留策略。
