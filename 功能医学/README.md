# Functional Medicine Nutrition AI Local MVP

本项目现在收敛为一个“本地可跑通”的功能医学营养推荐工作台。

核心原则：
- 不调用 `ima`
- 只依赖本地资料、本地产品目录和人工校对后的病例数据
- 上传文件后允许人工修正，不要求 OCR 一次识别完全正确
- 云端 LLM 可选接入为“模型辅助层”；即使不接 LLM，本地规则层也能产出结构化草案
- 所有推荐 SKU 只能来自本地 `30` 款产品目录

## 当前已实现

- `backend/`：FastAPI 后端，包含病例建档、文件上传、自动抽取、人工解析校对、问卷提交、结构化推荐草案生成、审核发布和审计日志
- `frontend/`：Next.js 本地网页工作台，支持病例列表、上传、问卷、解析校对、草案审核和发布
- `MSQ 问卷导入`：在病例工作台的 MSQ 区域支持上传已填写的 `DOCX` 问卷，系统会自动识别其中的核心信息并带入当前病例分析流程
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
cd D:\medical
```

### 方式一：使用启动脚本

这是当前最推荐的本地打开方式，会同时拉起后端和前端，并按项目现有配置接入本地运行环境。

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
2. 选择分析模式
3. 上传 `PDF / DOCX / PPTX / TXT / PNG / JPG`
4. 自动抽取文本和指标
5. 在网页工作台中人工修正文本与标准化指标
6. 可手填问卷，或在 MSQ 区域上传已填写的 `DOCX` 问卷自动识别
7. 生成结构化推荐草案
8. 顾问审核后发布最终报告

分析模式说明：
- `本地知识优先`：保持当前默认逻辑，以本地产品规则、已审核知识和人工校对数据为主，模型仅做有限润色；如果没有配置模型，则完全走本地流程
- `大模型优先，本地知识辅助`：保持原有报告格式，但在草案生成阶段由大模型主导摘要、系统分析、生活方式内容和候选产品重排；本地产品目录、已审核知识、红旗规则和禁忌仍然负责边界约束

## 当前工作台入口

首页创建病例时，表单里新增了 `分析模式` 下拉框。先在这里选定模式，再进入病例工作台，后续草案才会按对应策略生成。

如果病例已经创建完成，但模式选错了，当前建议重新创建病例后再上传资料，避免不同分析模式的草案混在同一个病例里。

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

功能稳定后，可以直接用 Docker 方式把同一套本地版交给其他人部署。

新成员第一次用 Docker 启动项目时，优先参考：`docs/docker-first-run.md`。

## 团队协作

项目仍在开发阶段时，推荐使用“私有 Git 仓库交付源码 + Docker Compose 统一启动环境”的方式协作。Git 负责多人改代码、提交历史和合并；Docker Compose 负责让新成员快速跑起同一套前后端环境。

医学资料需要分级管理：整理后的 JSON/CSV 规则数据可以进入仓库，真实病例、原始 PDF/Word/Excel、`.env`、`.runtime` 和本地数据库不要进入 Git。

新成员加入、资料边界和分支流程详见：`docs/team-collaboration.md`。

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
- 云端 LLM 接入为可选增强项，当前默认仍使用本地 deterministic composer

## 可选模型增强

当同时配置了 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL` 后，后端会自动切换到“远端模型辅助 + 本地规则兜底”模式。

如果目标服务使用 `responses` 风格接口，例如火山方舟 `https://ark.cn-beijing.volces.com/api/v3/responses`，可额外设置：
- `LLM_API_STYLE=responses`

如果不确定服务是 `responses` 还是 `chat/completions`，可以保持默认：
- `LLM_API_STYLE=auto`

这套模式的边界是：
- 模型只能看到结构化的 `case_summary`、`key_lab_highlights`、本地候选产品和已审核知识命中结果
- 模型不能直接读取原始上传文件全文，也不会直接读取未审核知识文件
- 模型只能从本地规则已经筛出的候选 SKU 中做选择，任何目录外 SKU 都会被后端丢弃
- 一旦模型调用失败、返回空结果或返回不合规 JSON，系统会自动回退到本地 composer
- 红旗风险、禁忌、人工解析校对未完成等硬性边界，依旧由本地规则层决定，模型不能绕过
