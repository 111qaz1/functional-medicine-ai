# Functional Medicine AI `/api/v2` 对接契约

## 1. 定位与兼容策略

`/api/v2` 是面向外部医疗系统前端的稳定 DTO 契约，覆盖病例建档、资料上传、综合分析、医生复核、草案审批与报告下载。

- `/api/v1` 保持原路径、请求和响应，不因 v2 上线而改变。
- v2 复用现有 Bearer Token；Token 仍由 `/api/v1/auth/token` 签发。
- v2 DTO 不直接复用后端领域对象，内部模型、Prompt、快照哈希、存储路径和审计对象不会出现在响应中。
- 本轮没有数据库迁移、容器配置、模型配置或前端代码变更。

所有 JSON 字段使用 `snake_case`，时间为带时区的 ISO 8601 字符串。请求出现未声明字段时返回 `422 application/problem+json`。

## 2. 路由

| 方法 | 路径 | 成功状态 | 响应 |
|---|---|---:|---|
| POST | `/api/v2/cases` | 201 | `CaseResponse` |
| GET | `/api/v2/cases?offset=0&limit=50` | 200 | `CaseListResponse` |
| GET | `/api/v2/cases/{case_id}` | 200 | `CaseResponse` |
| PUT | `/api/v2/cases/{case_id}/clinical-summary` | 200 | `CaseResponse` |
| POST | `/api/v2/cases/{case_id}/attachments` | 201 | `AttachmentBatchResponse` |
| POST | `/api/v2/cases/{case_id}/analyses` | 202 | `OperationResponse` |
| GET | `/api/v2/operations/{operation_id}` | 200 | `OperationResponse` |
| GET | `/api/v2/cases/{case_id}/analyses/latest` | 200 | `AnalysisResponse` |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/reviews` | 202 | `OperationResponse` |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/draft-generation:retry` | 202 | `OperationResponse` |
| GET | `/api/v2/drafts/{draft_id}` | 200 | `DraftResponse` |
| POST | `/api/v2/drafts/{draft_id}/approval` | 200 | `ApprovalResponse` |
| GET | `/api/v2/drafts/{draft_id}/report` | 200 | `ReportResponse` |
| GET | `/api/v2/drafts/{draft_id}/report.pdf` | 200 | `application/pdf` |

除 PDF 外，成功响应都是直接资源 DTO，不使用 `data` 外壳。批量附件响应使用 `{items, meta}`。

## 3. 鉴权

每个 v2 请求都必须携带当前系统签发的 Bearer Token：

```http
Authorization: Bearer <access_token>
```

病例、分析、草案和报告都按病例所有医生校验归属：

- 无有效 Token：`401 AUTHENTICATION_REQUIRED`
- 当前医生不拥有病例：`403 CASE_ACCESS_DENIED`
- 本轮不新增或修改 Token 签发协议。

## 4. 病例和附件

### 4.1 创建病例

```json
{
  "customer_name": "虚构样例用户",
  "consultant_id": "顾问-A",
  "notes": "仅用于接口联调的虚构数据"
}
```

响应示例：

```json
{
  "id": "case_xxx",
  "customer_name": "虚构样例用户",
  "consultant_id": "顾问-A",
  "status": "intake",
  "notes": "仅用于接口联调的虚构数据",
  "clinical_summary": null,
  "created_at": "2026-08-21T00:00:00Z",
  "updated_at": "2026-08-21T00:00:00Z",
  "attachments": []
}
```

### 4.2 临床摘要

```json
{
  "clinical_summary": "虚构临床摘要"
}
```

传入 `null` 或仅空白字符会清空摘要。资料变化后，现有服务仍按原规则把旧分析标记为过期。

### 4.3 上传附件

请求为 `multipart/form-data`：

- `files`：可重复的文件字段。
- `attachment_type`：`medical_record` 或 `questionnaire`。

批次按请求顺序逐个处理。单个文件失败不会回滚之前已经接受的文件：

```json
{
  "items": [
    {
      "file_id": "file_xxx",
      "filename": "synthetic-labs.txt",
      "attachment_type": "medical_record",
      "status": "parsed",
      "media_type": "text/plain",
      "size_bytes": 128,
      "parse_status": "parsed",
      "lab_item_count": 2,
      "warnings": [],
      "failure": null
    }
  ],
  "meta": {
    "case_id": "case_xxx",
    "case_status": "parsing_completed",
    "accepted_count": 1,
    "failed_count": 0
  }
}
```

项目状态固定为 `parsed`、`pending`、`questionnaire_imported`、`duplicate` 或 `failed`。整个批次没有任何可接受文件时返回 `422 ATTACHMENT_BATCH_REJECTED`。

## 5. 异步 Operation

启动综合分析和提交复核都返回 `202`，并设置：

```http
Location: /api/v2/operations/<analysis_id>
```

Operation 不单独持久化：`operation_id` 等于 `analysis_id`，从现有分析记录投影。

```json
{
  "operation_id": "analysis_xxx",
  "kind": "case_workflow",
  "stage": "analysis",
  "status": "running",
  "case_id": "case_xxx",
  "analysis_id": "analysis_xxx",
  "draft_id": null,
  "progress": {
    "current": 1,
    "total": 3,
    "percent": 33,
    "current_item": "synthetic-labs.txt"
  },
  "failure": null,
  "created_at": "2026-08-21T00:00:00Z",
  "updated_at": "2026-08-21T00:00:05Z"
}
```

固定枚举：

- `stage`：`analysis`、`draft_generation`
- `status`：`queued`、`running`、`succeeded`、`failed`

Operation 业务执行失败仍返回 HTTP 200，并在 `failure` 中提供稳定错误码、公开消息和 `retryable`。HTTP 4xx/5xx 只表示轮询请求本身失败。

领域服务产生的异常文本不会直接进入 `failure` 或 `draft_generation.error`。已知模型失败会映射为稳定公开错误码；未知异常统一降级为 `ANALYSIS_FAILED` 或 `DRAFT_GENERATION_FAILED`。

## 6. 分析结果与差量复核

`AnalysisResponse` 返回公开摘要、系统发现、异常指标、当前补充剂、食物敏感、进度、警告和草案生成状态。以下内部字段不会返回：

- `snapshot_hash`
- `model_version`
- `prompt_version`
- 标准化候选及内部系统映射
- 文档分析缓存和内部审计结构

### 6.1 差量指令

复核请求可以包含三组变化：

```json
{
  "expected_revision": 1,
  "finding_changes": [
    {
      "op": "update",
      "id": "finding-001",
      "changes": {
        "name": "医生确认后的指标名称",
        "abnormal_flag": "high"
      }
    },
    {
      "op": "remove",
      "id": "finding-002"
    },
    {
      "op": "add",
      "value": {
        "name": "医生补充指标",
        "abnormal_flag": "high",
        "source_file_id": "file-001",
        "source_file_name": "synthetic-labs.txt",
        "source_page": 1,
        "source_text": "虚构证据文本"
      }
    }
  ],
  "supplement_changes": [],
  "food_sensitivity_changes": []
}
```

每组变化使用 `op` 判别联合：

- `update`：必须给出当前条目 ID，`changes` 至少包含一个可编辑字段。
- `remove`：必须给出当前条目 ID。
- `add`：不得提供领域 ID，由服务端生成。

同一条目不能在一个请求中被操作两次。未知 ID、分析快照之外的文件引用或其他无效差量返回 `422`；修订号不一致返回 `409 ANALYSIS_REVISION_CONFLICT`。草案生成任务仍在运行时返回 `409 DRAFT_GENERATION_IN_PROGRESS`，不会将未应用的差量误报为成功。三个变化数组可以同时为空，用于确认当前结果并继续生成草案。

后端会以当前分析快照为基础应用变化；客户端未提交的置信度、证据状态、标准化信息和系统映射会原样保留。

## 7. 草案和审批

`DraftResponse` 提供：

- 公开摘要、重点指标和生活方式建议。
- 推荐 SKU、当前剂量和可选剂量。
- 推荐原因、证据、警告、禁忌和缺失信息。
- 人工复核标志、置信度和草案修订号。

审批请求不接受任意 `edits` 字典：

```json
{
  "expected_revision": 3,
  "publishable_summary": "医生确认后的公开总结",
  "excluded_sku_ids": ["SKU-002"],
  "dosage_overrides": [
    {
      "sku_id": "SKU-001",
      "option_id": "alternate",
      "note": "医生根据虚构联调条件调整"
    }
  ]
}
```

约束：

- 重复、未知或同时被排除和改剂量的 SKU 返回 `422`。
- 选项必须属于对应 SKU。
- 选择非当前剂量时必须填写说明。
- 排除后至少保留一项推荐，否则返回 `409 EMPTY_PUBLISHABLE_RECOMMENDATIONS`。
- `publishable_summary` 为 `null` 时沿用系统生成报告。

审批成功后，`report_url` 指向同一版本的 PDF 下载接口。

## 8. Problem Details

所有 v2 HTTP 错误使用 `application/problem+json`：

```json
{
  "type": "urn:fm-ai:problem:analysis-revision-conflict",
  "title": "Analysis revision conflict",
  "status": 409,
  "detail": "The analysis was updated. Fetch the latest revision before submitting.",
  "instance": "/api/v2/cases/case_xxx/analyses/analysis_xxx/reviews",
  "code": "ANALYSIS_REVISION_CONFLICT",
  "errors": []
}
```

主要错误码：

| HTTP | code | 含义 |
|---:|---|---|
| 401 | `AUTHENTICATION_REQUIRED` | Token 缺失或失效 |
| 403 | `CASE_ACCESS_DENIED` | 当前医生不拥有病例 |
| 404 | `CASE_NOT_FOUND` / `ANALYSIS_NOT_FOUND` / `DRAFT_NOT_FOUND` | 资源不存在 |
| 404 | `REPORT_NOT_FOUND` | 报告记录存在但 PDF 文件不可用 |
| 409 | `ANALYSIS_START_CONFLICT` | 病例尚无有效资料或当前状态不能启动分析 |
| 409 | `ANALYSIS_REVISION_CONFLICT` | 复核提交基于旧修订 |
| 409 | `DRAFT_GENERATION_IN_PROGRESS` | 当前草案生成尚未结束，不能再次复核 |
| 409 | `DRAFT_STALE` | 病例资料变化导致草案过期 |
| 409 | `REPORT_NOT_READY` | 草案尚未审批或已发布 PDF 当前不可用 |
| 422 | `REQUEST_VALIDATION_FAILED` | DTO 或字段类型不合法 |
| 422 | `THIRD_PARTY_PROCESSING_CONFIRMATION_REQUIRED` | 未确认第三方模型处理授权 |
| 422 | `UNKNOWN_REVIEW_ITEM` | 差量指令引用未知条目 |
| 422 | `INVALID_REVIEW_CHANGES` | 差量引用分析外文件或不符合领域约束 |
| 422 | `INVALID_DOSAGE_OVERRIDE` | 剂量选项不合法 |
| 500 | `INTERNAL_SERVER_ERROR` | 未公开内部异常详情 |

验证错误的 `errors` 数组只包含字段位置、公开消息和错误类型，不回显请求正文。

## 9. v1 到 v2 对照

| v1 | v2 |
|---|---|
| `POST /api/v1/cases` | `POST /api/v2/cases` |
| `POST /api/v1/cases/{id}/attachments` | `POST /api/v2/cases/{id}/attachments`，附件类型改为 `medical_record` / `questionnaire` |
| `POST /api/v1/cases/{id}/nutrition-recommendations` | 拆分为分析、复核、Operation 和草案资源 |
| `GET /api/v1/cases/{id}/nutrition-recommendations/latest` | `GET /api/v2/cases/{id}/analyses/latest` 后按 `draft_id` 获取草案 |
| `GET /api/v1/drafts/{id}/plan-summary` | `GET /api/v2/drafts/{id}` 的 `public_summary` |
| `GET /api/v1/drafts/{id}/report-download` | `GET /api/v2/drafts/{id}/report` |
| `GET /api/v1/drafts/{id}/report.pdf` | `GET /api/v2/drafts/{id}/report.pdf` |

甲方前端到达后，应以运行时 OpenAPI 为准生成或校验客户端类型；任何必要的字段适配先形成契约差异清单，不直接修改 v1。

## 10. 验证

聚焦测试：

```powershell
python -m unittest discover -s backend/tests -p "test_api_v2_*.py" -v
```

测试固定：

- v1 八条 OpenAPI paths 哈希不变。
- v2 十三条 paths 和完整 OpenAPI 组件哈希不变。
- DTO 拒绝额外字段和空更新。
- Mapper 不泄漏内部字段并保留未修改领域数据。
- 病例归属、附件阶段性失败、Operation、修订冲突、真实空差量复核、审批和 PDF 均有 HTTP 契约测试。
- 未知执行异常不会进入公开响应；缺失 PDF 不会先发送 `200 application/pdf` 后再发生流式失败。
- 测试仅使用虚构数据，不调用真实模型、密钥或病例。

这些进程内契约测试不是甲方真实调用证明。仓库同时提供第一阶段真实 HTTP 集合：

- `postman/api_v2.postman_collection.json`
- `postman/api_v2.local.postman_environment.json`
- `postman/fixtures/api-v2-synthetic-labs.txt`
- `postman/fixtures/api-v2-invalid.exe`

本地服务的 `FM_EXTERNAL_TRUST_SHARED_SECRET` 必须与测试环境中的虚构共享密钥一致。集合从仓库根目录运行，使 multipart 文件路径可被 Newman 正确解析：

```powershell
npx.cmd --yes newman@6.2.2 run postman\api_v2.postman_collection.json `
  -e postman\api_v2.local.postman_environment.json `
  --working-dir . `
  --timeout 60000 `
  --timeout-request 10000 `
  --timeout-script 5000 `
  --delay-request 100 `
  -r cli,json `
  --reporter-json-export <隔离报告路径>
```

第一阶段显式移除 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL` 后运行，只验证不消耗模型额度的真实传输边界：

- 13 条 v2 路由均被真实 HTTP 请求命中。
- 病例、临床摘要、multipart 部分成功、分析启动、Operation 轮询和最新分析覆盖成功响应。
- 鉴权、归属、严格 DTO、工作流冲突、草案/审批/报告不存在及 PDF 错误媒体类型覆盖 Problem Details。
- 无模型配置时，分析任务按公开契约终止为 `status="failed"` 和 `code="ANALYSIS_FAILED"`，不暴露内部异常。

2026-08-21 的第一阶段隔离本地记录为 24 个请求、53 条断言、0 失败，Newman 退出码 0。

经病例与模型额度明确授权后，第二阶段使用 `postman/api_v2.model_e2e.postman_collection.json` 对一个匿名 PDF 样例（源文件 SHA-256 前缀 `5c10c63c6094`，24 页）执行单病例真实模型闭环。运行结果为 168 个 HTTP 请求（含异步轮询）、182 条断言、0 失败，Newman 退出码 0，约 3 分 17 秒。验证覆盖：

- 附件上传成功并启动分析，Operation 最终成功；最新分析存在异常发现且不泄漏快照哈希、模型/Prompt 版本或内部审计。
- 使用类型化 `finding_changes.update` 提交安全的原值差量，草案生成最终成功；草案含可审批推荐且不泄漏内部字段。
- 审批发布成功，报告资源进入可下载状态；PDF 响应媒体类型、附件文件名和 `%PDF` 文件头正确。
- 生成 PDF 为 239267 字节，SHA-256 为 `25e1d68fa83ddc33fd0c0f3bd4beec2a1e2e36d7b93c78075ddc5c94edfdc78b`。
- 数据库审计记录 4 次真实模型请求、0 失败，共 43776 tokens（prompt 36159、completion 7617、cached 1024）；低于本次批准的 12 次请求上限。

运行后发现测试包装器的外部请求计数只覆盖了病例分析提供器，未覆盖独立的随访计划提供器，因此计数文件错误显示 3 次；数据库的 4 次审计记录是本次结论的权威依据。包装器现已将随访、OCR、草案和 RAG 提供器纳入同一客户端上限，但该机械修复没有再次消耗额度复跑。

为避免病例数据留存，不保存 Newman 响应报告，隔离运行产生的病例副本、SQLite 数据库和报告 PDF 在提取上述聚合证据后删除；用户提供的源文件保持不变。该结果只证明一个授权 PDF 样例的单病例成功路径，不证明其他文件格式、真实问卷、多病例并发、负载/故障恢复或甲方前端适配。

同日全量后端回归实际运行 349 项，结果为 17 个失败、3 个错误，不能记为全绿。其中 2 个错误是当前环境缺少 FAISS 稠密索引，1 个错误是 Windows 清理临时 SQLite 文件时仍被占用；功能失败集中在既有报告分类、章节/风险提示和推荐/RAG 旧断言。仓库既有基线文档已经记录 17 个同类失败和 2 个 FAISS 环境错误；另外在未含 v2 改动的 `963d49d` 临时 worktree 中，代表性的报告分类失败、FAISS 错误和 SQLite 文件锁均可复现。因此当前没有证据将它们归因于 v2，但没有为 17 个失败逐项执行完整基线对照。v2 聚焦测试独立运行仍为 23/23 通过。
