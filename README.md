# Functional Medicine Nutrition AI 本地部署版

本项目现在收敛为一个“本地可跑通”的功能医学营养推荐工作台。

核心原则：
- 不调用 `ima`
- 上传阶段只做格式、大小、哈希、PDF 页数和文本层预检，不生成指标
- 综合分析使用已配置的 Doubao Responses 模型；模型失败时明确失败并允许重试，不回退旧规则解析器
- 营养素草案只基于医生确认后的异常、问卷和本地产品/禁忌/剂量规则生成
- 所有推荐 SKU 只能来自本地 `30` 款产品目录

## 当前已实现

- `backend/`：FastAPI 后端，包含统一资料预检、SQLite 分析任务、逐文件模型缓存、病例综合、异常校对、草案生成、审核发布和 PDF
- `frontend/`：Next.js 工作台，支持统一上传、异步进度、只读 MSQ/专项摘要、数值及非数值异常校对、草案审核和发布
- `MSQ`：与其他病例资料从同一上传区进入，由大模型转换为现有问卷结构；工作台只读展示
- `frontend/app/products`：支持新增、修改、删除产品规则；保存后会直接影响后续新生成的推荐草案
- `backend/app/data/product_catalog.json`：已替换为真实 `30` 款本地产品目录
- `backend/app/data/knowledge_statements.json`：已替换为本地已审核知识条目
- `backend/app/data/marker_dictionary.json`：已清理为可直接匹配中文指标的本地指标字典
- `backend/app/repositories/in_memory.py`：已替换为基于 SQLite 的本地持久化仓储
- `backend/tests/`：后端单测覆盖了解析和推荐边界

## 本地目录约束

产品目录已按当前业务规则收口：
- `25 + 8` 个来源 sheet 最终保留 `30` 个逻辑 SKU
- 删除 `综合消化酶`
- 鱼油只保留 `11rTG鱼油90%`
- 甘氨酸镁保留一个逻辑 SKU，并标记为 `pending_spec_decision`

## 如何打开程序

推荐先进入项目根目录：

```bash
cd <项目目录>
```

### 方式一：使用启动脚本

这是当前最推荐的本地打开方式，会同时拉起后端和前端，并按项目现有配置接入本地运行环境。

当前综合分析使用 Doubao Responses 配置，先在项目根目录 `.env` 中确认：

```env
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_API_KEY=你的 Ark API Key
LLM_MODEL=doubao-seed-2-0-mini-260215
LLM_API_STYLE=responses
```

然后启动：

```bat
scripts\start-local-doubao.cmd
```

启动后默认访问：
- 前端工作台：`http://127.0.0.1:3000`
- 后端健康检查：`http://127.0.0.1:8000/health`

停止本地服务：

```bat
scripts\stop-local.cmd
```

### 方式二：手动启动

#### 后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

后端健康检查：
`http://127.0.0.1:8000/health`

#### 前端

```bash
cd frontend
npm install
npm run dev -- --hostname 127.0.0.1 --port 3000
```

前端默认地址：
`http://127.0.0.1:3000`

如果 `3000` 端口已被占用，Next.js 在某些启动方式下可能会自动切到 `3001` 或更高端口；请以终端日志里实际显示的地址为准。

### 打开后如何确认程序正常

- 浏览器能打开前端工作台页面
- 访问 `http://127.0.0.1:8000/health` 返回 `{"status":"ok"}`
- 上传病例后，病例列表可以正常刷新

## 默认流程

1. 创建病例
2. 从统一上传区上传病例报告、MSQ、肠道报告、慢性食物敏感报告或总结截图
3. 确认第三方模型处理授权，点击“确认资料并开始综合分析”
4. 等待逐文件分析、病例级综合和证据校验完成
5. 医生修改、删除或补充数值与非数值异常
6. 点击“保存校对并生成营养素草案”
7. 审核发布并下载 PDF

系统统一由大模型完成报告语义提取和病例综合；产品资格、产品排序、安全检查与剂量映射继续由本地版本化规则控制。

## 产品管理说明

在首页点击 `产品规则` 可进入产品管理页。

目前支持：
- 新增产品
- 修改现有产品规则
- 删除旧产品

这些变更保存后会立即写入本地产品目录和 SQLite 仓储，后续重新生成的健康报告会自动读取最新产品规则，不需要额外重启服务。

## 关键环境变量

后端路径全部已环境变量化，避免写死本机绝对路径：
- `FM_PROJECT_ROOT`
- `FM_DATA_DIR`
- `FM_RUNTIME_DIR`
- `FM_UPLOAD_DIR`
- `FM_SQLITE_PATH`
- `FM_KNOWLEDGE_ROOT`
- `FM_REPORT_REFERENCE_PATH`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_API_STYLE`
- `LLM_TIMEOUT_SECONDS`
- `LLM_TEMPERATURE`
- `FM_LLM_RETRY_ATTEMPTS`（默认 `2`，表示初次请求失败后最多重试两次）
- `FM_LLM_RETRY_BASE_DELAY_SECONDS`（默认 `1`）
- `FM_LLM_RETRY_MAX_DELAY_SECONDS`（默认 `10`）
- `FM_LLM_MAX_CONCURRENCY`（默认 `90`，Tier2 官方上限为 `100`）
- `FM_LLM_RPM_SOFT_LIMIT`（默认 `475`，Tier2 官方上限为 `500`）
- `FM_LLM_TPM_SOFT_LIMIT`（默认 `2850000`，Tier2 官方上限为 `3000000`）
- `FM_LLM_RATE_LIMIT_WINDOW_SECONDS`（默认 `60`）
- `FM_LLM_DEFAULT_COMPLETION_RESERVATION`（默认 `32768`，仅用于本地排队预约，不会作为输出上限发送给模型）
- `FM_MAX_UPLOAD_MB`（默认 `50`）
- `FM_MAX_PDF_PAGES`（默认 `50`，单个 PDF 超过 50 页时拒绝上传并提示拆分）
- `FM_ANALYSIS_WORKERS`（默认且最大 `20`）
- `FM_CASE_DOCUMENT_WORKERS`（默认且最大 `2`）

病例分析、OCR、病例助手、可选 Composer、RAG 报告融合和处方建议请求
都会进入同一个进程内 FIFO 队列，同时受全局并发、RPM 和 TPM 软限制约束。
服务会保存响应中的真实 token 用量，并在同类调用积累至少 5 条数据后使用
历史 P95 动态调整后续预约量。应用不会向模型发送输出 token 上限。

前端：
- `NEXT_PUBLIC_API_BASE_URL`

可直接参考仓库根目录的 `.env.example`。

## Docker 交付

项目已补齐：
- `backend/Dockerfile`
- `frontend/Dockerfile`
- `compose.yaml`
- `.env.example`
- `docs/deployment.md`
- `docs/customer-api-delivery-guide.md`

功能稳定后，可以直接用 Docker 方式把同一套本地版交给其他人部署。

新成员第一次用 Docker 启动项目时，优先参考：`docs/docker-first-run.md`。
如果以接口形式交付给外部系统，优先参考：`docs/customer-api-delivery-guide.md`。
正式环境建议使用 Nginx 统一代理和 HTTPS，参考：`docs/nginx-production-deployment.md`。
甲方正式部署推荐配置参考：`docs/production-recommended-config.md`。

## 团队协作

推荐使用“私有 Git 仓库或源码包交付 + Docker Compose 统一启动环境”的方式协作。Git 负责提交历史和版本追踪；Docker Compose 负责让部署方快速跑起同一套前后端环境。

医学资料需要分级管理：整理后的 JSON/CSV 规则数据可以进入仓库，真实病例、原始 PDF/Word/Excel、`.env`、`.runtime` 和本地数据库不要进入 Git。

## 验证

后端测试：
```bash
python -m unittest discover -s backend/tests -v
```

前端构建：
```bash
cd frontend
npm run build
```

## 当前边界

- `0316测试报告1.pdf` 当前只作为报告结构参考，不做 1:1 版式复刻
- `功能医学相关资料` 目前按“全量纳管、仅已审核知识参与自动推荐”的方式处理
- 病例综合分析必须配置 Doubao Responses 模型；产品、禁忌、剂量和审核发布仍由本地逻辑约束

## 模型配置与边界

当同时配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 和 `LLM_API_STYLE=responses` 后，工作台可启动异步病例综合分析。

当前推荐配置：

```env
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_API_KEY=你的 Ark API Key
LLM_MODEL=doubao-seed-2-0-mini-260215
LLM_API_STYLE=responses
```

这套模式的边界是：
- 医生确认第三方处理授权后，病例分析模型会读取上传资料的文本层或扫描页图像；上传阶段本身不调用模型
- 病例分析模型不得输出产品、SKU、剂量和疗程；非法 JSON、超时或模型失败会明确标记任务失败，不走旧解析规则兜底
- 医生校对后，模型只基于确认异常做一次文本重新综合，不重新读取 PDF
- 草案阶段只把结构化病例上下文、本地候选产品和已审核知识命中交给 composer
- 模型只能从本地规则已经筛出的候选 SKU 中做选择，任何目录外 SKU 都会被后端丢弃
- 红旗风险、禁忌、剂量和人工审核等硬性边界由本地规则层决定，模型不能绕过
