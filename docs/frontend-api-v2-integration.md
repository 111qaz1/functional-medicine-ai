# `/api/v2` 前端对接工作台

## 1. 本轮范围

新增独立前端入口，覆盖：

`病例建档 → 病历/问卷上传 → 综合分析 → 医生差量复核 → 草案审批 → 报告状态 → PDF 下载`

- 入口：`/integration/cases`
- 病例工作流：`/integration/cases/{case_id}`
- 现有内部首页、`/cases/{id}`、产品配置、助手页面及 `/api/internal` 客户端保持不变。
- 未修改后端、数据库或 `/api/v2` 契约。
- 未引用或复制甲方 `dist` 中的压缩代码、资源和样式。

## 2. 运行方式

### 2.1 连接真实 `/api/v2`

浏览器只请求同源 `/api/v2`。Next 中间件从服务端环境读取预签发 Token，并覆盖浏览器可能传入的 `Authorization`：

```env
INTERNAL_API_BASE_URL=http://backend:8000
FM_API_V2_BEARER_TOKEN=由服务端预签发的访问令牌
```

Token 不会进入客户端脚本、URL、日志、Local Storage、Session Storage 或 Fixture 数据。Token 签发与共享密钥签名不属于前端职责；缺少 Token 时 `/api/v2` 返回 `503 application/problem+json` 和 `FRONTEND_INTEGRATION_NOT_CONFIGURED`。

### 2.2 Fixture 验收

Fixture 只在非生产 `next dev` 中启用，使用完全虚构数据，不访问网络、后端、模型或真实病例：

```powershell
cd frontend
$env:FM_WORKFLOW_FIXTURE_MODE='1'
$env:FM_WORKFLOW_FIXTURE_SCENARIO='success'
npm.cmd run dev
```

打开 `http://127.0.0.1:3000/integration/cases`。生产构建即使设置 `FM_WORKFLOW_FIXTURE_MODE=1` 也会强制关闭 Fixture。

可选场景：

| 场景 | 目的 |
|---|---|
| `success` | 建档到 PDF 的完整成功流 |
| `attachment_partial_failure` | 同批附件部分成功、部分失败 |
| `analysis_failure` | Operation 以公开分析错误结束 |
| `draft_generation_failure` | 草案失败并通过重试恢复 |
| `revision_conflict` | 复核返回 `409`，保留本地编辑 |
| `approval_validation_error` | 审批返回类型化 `422` |
| `report_not_ready` | 审批后报告仍返回 `409` |
| `authentication_failure` | 所有资源请求返回 `401` |

Fixture 状态仅保存在当前标签页的 `sessionStorage`，键为 `fm-ai-v2-workflow-fixture`，内容只包含虚构病例状态。

## 3. 页面与契约

`WorkflowGateway` 完整覆盖 13 条 v2 路由：

| 方法 | 路径 |
|---|---|
| POST | `/api/v2/cases` |
| GET | `/api/v2/cases/{case_id}` |
| PUT | `/api/v2/cases/{case_id}/clinical-summary` |
| POST | `/api/v2/cases/{case_id}/attachments` |
| POST | `/api/v2/cases/{case_id}/analyses` |
| GET | `/api/v2/operations/{operation_id}` |
| GET | `/api/v2/cases/{case_id}/analyses/latest` |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/reviews` |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/draft-generation:retry` |
| GET | `/api/v2/drafts/{draft_id}` |
| POST | `/api/v2/drafts/{draft_id}/approval` |
| GET | `/api/v2/drafts/{draft_id}/report` |
| GET | `/api/v2/drafts/{draft_id}/report.pdf` |

实现要点：

- 建档只提交 `customer_name`、`consultant_id`、`notes`。
- 病历与问卷各有独立 multipart 上传入口，逐项显示批次结果，不提供契约未支持的删除操作。
- Operation 轮询不重叠；页面隐藏时暂停，恢复后继续；15 分钟仅停止自动轮询，不把业务状态改成失败。用户可手动停止和继续轮询。
- 页面刷新后按病例 ID 恢复病例、最新分析、草案和报告；发现运行中 Operation 时重新加入轮询。
- 复核保存服务端修订快照，只发送 `add`、`update`、`remove` 差量。空差量表示确认当前结果；`409` 不自动合并医学内容。
- 审批默认发送 `publishable_summary: null`；只发送排除 SKU 和改选剂量，非默认剂量必须填写说明，至少保留一项推荐。
- PDF 下载前先读取报告状态，并使用服务端 `Content-Disposition` 文件名。
- HTTP 错误统一映射 `ProblemDetails`；业务执行失败保留在 HTTP 200 的 Operation `status="failed"` 中。

## 4. 风格适配边界

新增样式全部限定在 `.workflow-app` 命名空间。2026-08-22 对甲方 `dist` 和风格参考页进行只读对照后，确认两者共享 PARACELSUS 视觉语言：224px 深色侧栏、56px 顶栏、红色主操作、浅蓝灰页面、白色内容面板和紧凑的 14px 信息密度。当前默认主题为 `paracelsus`，核心变量包括：

```css
--workflow-bg
--workflow-surface
--workflow-surface-muted
--workflow-ink
--workflow-muted
--workflow-border
--workflow-accent
--workflow-accent-hover
--workflow-accent-strong
--workflow-accent-surface
--workflow-focus
--workflow-success
--workflow-success-surface
--workflow-warning
--workflow-warning-surface
--workflow-danger
--workflow-danger-surface
--workflow-info
--workflow-info-surface
--workflow-sidebar-bg
--workflow-sidebar-ink
--workflow-sidebar-muted
--workflow-radius-sm
--workflow-radius-md
--workflow-radius-lg
--workflow-sidebar-width
--workflow-header-height
--workflow-content-width
```

`WorkflowShell` 提供 `title`、`description`、`caseId`、`steps`、`currentStep`、`headerActions`、`brandSlot`、`contextSlot`、`children` 和 `theme` 属性。`brandSlot` 和 `contextSlot` 是甲方源码到位后的品牌及患者上下文接入点；当前使用文字回退和 v2 已有病例字段，不复制构建产物中的图片。步骤、区块、通知和业务状态通过稳定的 `workflow-*` class、`data-step`、`data-state`、`data-current-step` 暴露；按钮与状态文案集中在 `frontend/lib/api-v2/copy.ts`。

Fixture 与主题已解耦，开发 Fixture 和真实 Gateway 均默认使用 `data-theme="paracelsus"`；`test` 主题只通过 `WorkflowShell` 显式注入，用于证明换主题不需要修改 Gateway、差量构建或工作流状态机。后续甲方源码适配应优先替换：

1. `WorkflowShell` 页面外壳与导航容器。
2. `.workflow-app` 主题变量和布局令牌。
3. `copy.ts` 中的术语与状态文案。
4. `brandSlot`、`contextSlot` 及甲方患者、医生上下文到 `case_id`、`consultant_id`、`reviewer_id` 的启动参数映射。

不得通过重写 `review-diff.ts`、`approval.ts`、`workflow-state.ts` 或 `WorkflowGateway` 来完成纯视觉适配。

甲方当前提供的 `C:\Users\21547\Downloads\dist` 仍是 409 个生产构建文件，无源码、Source Map 或 `package.json`。本仓库未修改该目录，也未复制其中的压缩代码、样式或图片；当前结果是独立工作台的同风格实现，不等于已经嵌入甲方 Vue 工程。

## 5. 可访问性与响应式

- 桌面目标：1440×900；平板目标：768×1024。
- 主流程使用原生表单、按钮、链接、`details`、`progress` 和语义化标题。
- 错误使用 `role="alert"`；动态状态使用 `aria-live="polite"`；焦点使用高对比可见轮廓。
- 状态同时使用文字与 `data-state`，不只依赖颜色。
- 桌面使用 224px 步骤侧栏；768×1024 平板将侧栏缩为 176px，并把表单、上传和审批布局折叠为单列。
- 低于 768px 时侧栏转为顶部横向步骤导航；粗指针设备交互控件最小高度 44px。
- `prefers-reduced-motion` 下取消非必要过渡与动画。

## 6. 验证

```powershell
cd frontend
npm.cmd test
npx.cmd tsc --noEmit
npm.cmd run build
```

自动化覆盖 HTTP 路径与请求体、Problem Details、服务端 Token 注入、Operation 生命周期、差量复核、审批校验、Fixture 完整流和七类错误场景。Fixture 测试显式断言完整成功流未调用 `fetch`。

当前边界：已获得甲方生产构建产物和风格参考页，但尚未获得可编辑前端源码、真实后端测试环境、真实 Token 获取方式、患者上下文和权限指令。因此本轮只能证明独立工作台的视觉适配和 Fixture 行为，不能证明甲方真实鉴权、病例归属、上传限制、轮询延迟、PDF 网关行为或源码级嵌入。

## 7. 下一轮甲方适配门槛

拿到甲方源码和测试环境后，先形成证据化差异清单：

- 真实路由容器、菜单与页面权限指令。
- 请求封装、Token 生命周期和跨域/同源策略。
- 患者与医生上下文字段、病例归属和错误映射。
- 甲方后端运行时 OpenAPI 与当前 `/api/v2` 的差异。
- 甲方设计令牌、组件库、密度、断点和术语。

只修正已确认差异；未提供能力继续标记待确认，不新增猜测性后端接口。
