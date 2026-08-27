# 新版五步工作台使用与对接说明

本文说明 `/integration` 新版医生工作台的入口、身份处理、五步业务流程、运行方式和验收要点。旧工作台和 `/api/v1` 保持兼容，新版工作台通过稳定的 `/api/v2` 契约连接同一后端。

## 1. 页面入口

- 病例列表与创建入口：`/integration/cases`
- 单病例工作台：`/integration/cases/{case_id}`
- 管理员医生账号管理：`/doctors`
- 根路径仍是旧工作台，不用于验收新版五步流程。

本地开发时必须使用同一个主机名访问页面。例如服务启动在 `localhost:3000`，浏览器也应打开：

```text
http://localhost:3000/integration/cases
```

不要在同一次登录中混用 `localhost` 和 `127.0.0.1`。新版工作台的写操作执行同源检查，混用主机名会被视为不同来源。

## 2. 身份与病例隔离

- 浏览器登录后使用 HttpOnly `fm_session` Cookie，不在前端保存固定 Bearer Token。
- Next 中间层丢弃浏览器自行传入的 `Authorization`，再把当前医生会话转换为后端 Bearer 请求。
- 写操作只接受同源请求；会话失效统一回到医生登录状态。
- 医生只能查看和处理本人病例。管理员可以维护医生账号，但不会默认越权查看其他医生病例。
- 首次部署且系统没有账号时，登录页会显示“初始化系统管理员”；初始化完成后，后续医生账号由管理员创建、启停或重置密码。

## 3. 五步工作流

新版工作台一次只显示一个业务步骤，当前步骤保存在 URL 的 `step` 查询参数中：

```text
case → attachments → review → draft → report
```

已完成且未失效的步骤可以从左侧导航返回查看；尚未满足前置条件的步骤保持锁定。

### 第 1 步：病例

- 查看客户名称、顾问 ID、备注和病例更新时间。
- 编辑并保存临床摘要。
- 临床摘要变化后，以后端返回的病例状态为准重新判断旧分析、草案和报告是否仍有效。

### 第 2 步：资料

- 病历、检查报告、医疗问卷、MSQ、慢性食物敏感报告和图片共用一个多文件上传入口。
- 每个文件分别显示成功、重复或失败结果；同批次单个文件失败不会回滚其他已保存文件。
- 上传阶段先执行文件预检和文本解析。普通可提取文档优先使用本地解析；扫描件或图片可能使用已配置的视觉识别能力。
- 病例级综合分析不会在上传时自动开始。医生必须确认资料可发送至已配置的第三方模型服务，再点击“确认资料并开始综合分析”。
- 分析进度在本步骤持续显示，包括准备资料、文件分析、病例级综合和证据校验；分析完成后自动进入复核。

### 第 3 步：复核

- 顶部展示病例综合摘要、系统发现、警告和分析状态。
- 异常指标卡片显示结果、单位、已有参考范围、来源文件、页码、原文证据、报告解释和中性医学解释。
- 医生可新增、修改或删除异常指标；缺少结构化数值时保留原报告结论，不生成虚假参考范围。
- 当前营养素显示真实来源文件；医生补充项目标记为“医生补充”。
- 只有分析识别到食敏指标时才显示慢性食物敏感区域，并按轻度、中度、重度和待确认分组。
- 点击“保存校对并生成营养素草案”后启动草案生成，进度仍在复核页显示；首次生成成功后自动进入方案审核。
- 草案生成成功后，医生仍可从左侧返回复核查看已提交的分析与校对内容。相同的已完成任务不会再次把页面强制跳回方案审核。

### 第 4 步：方案审核

- 审核推荐产品、营养素纳入或排除、批准剂量、调整备注、产品证据和安全提示。
- 本步骤不单独展开完整健康画像和四域生活方式报告正文；这些内容在最终报告中统一编辑。
- “继续编辑最终报告”只切换到下一步，不会在本步骤直接批准或发布报告。

### 第 5 步：最终报告

- 将结构化报告章节转换为完整 Markdown 报告。
- 支持编辑、编辑与预览分栏、纯预览和放大编辑。
- 最终批准同时提交报告正文、产品排除项、剂量调整和草案版本。
- 批准医生取当前登录医生，不能由浏览器填写或伪造。
- 批准成功后报告锁定，显示批准医生、批准时间和报告状态，并支持 PDF 下载。
- 遇到 `409` 版本冲突时保留本地编辑，医生应重新加载最新草案后再确认。

## 4. 导航与状态恢复

- 分析成功和草案生成成功只在对应任务首次完成时自动前进一次。
- 医生主动返回已完成步骤时，自动前进逻辑不得抢占导航。
- 浏览器刷新后从服务端恢复病例、最新分析、草案和报告；运行中的任务会恢复轮询，但不会重复启动。
- 浏览器前进和后退会同步 `step` 参数。
- 旧地址 `?step=analysis` 会根据分析状态迁移到 `attachments` 或 `review`。
- 离开病例、刷新状态或退出登录前，如存在未保存的临床摘要、复核或方案编辑，页面会要求确认。

## 5. 连接真实后端

### Docker Compose

项目根目录准备 `.env` 后执行：

```powershell
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
```

默认示例端口为：

- 新旧前端：`http://localhost:3100`
- 新版工作台：`http://localhost:3100/integration/cases`
- 后端健康检查：`http://127.0.0.1:7800/health`

实际端口以 `.env` 中的 `FRONTEND_PORT` 和 `BACKEND_PORT` 为准。Compose 中的前端通过 `INTERNAL_API_BASE_URL=http://backend:8000` 访问后端。

### 本地 Next 开发服务器连接 Docker 后端

后端已经运行在宿主机 `7800` 端口时：

```powershell
cd frontend
$env:INTERNAL_API_BASE_URL='http://127.0.0.1:7800'
Remove-Item Env:FM_WORKFLOW_FIXTURE_MODE -ErrorAction SilentlyContinue
npm.cmd install
npm.cmd run dev -- --hostname localhost --port 3000
```

打开：

```text
http://localhost:3000/integration/cases
```

## 6. Fixture 界面验收

Fixture 只允许在非生产 `next dev` 中启用，不访问后端、模型或真实病例：

```powershell
cd frontend
$env:FM_WORKFLOW_FIXTURE_MODE='1'
$env:FM_WORKFLOW_FIXTURE_SCENARIO='success'
npm.cmd run dev -- --hostname localhost --port 3000
```

可选场景：

| 场景 | 用途 |
|---|---|
| `success` | 建档到 PDF 的完整成功流程 |
| `attachment_partial_failure` | 同批资料部分成功、部分失败 |
| `analysis_failure` | 综合分析任务失败 |
| `draft_generation_failure` | 草案生成失败并重试 |
| `revision_conflict` | 复核版本冲突并保留本地编辑 |
| `approval_validation_error` | 最终批准请求校验失败 |
| `report_not_ready` | 批准后报告暂未就绪 |
| `authentication_failure` | 资源请求返回未登录 |

Fixture 状态只保存在当前标签页的 `sessionStorage`，不作为真实后端验收结果。

## 7. API v2 路由

`WorkflowGateway` 使用以下公开工作流接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v2/cases` | 当前医生病例列表 |
| POST | `/api/v2/cases` | 创建病例 |
| GET | `/api/v2/cases/{case_id}` | 读取病例和资料状态 |
| PUT | `/api/v2/cases/{case_id}/clinical-summary` | 保存临床摘要 |
| POST | `/api/v2/cases/{case_id}/attachments` | 批量上传病例资料 |
| POST | `/api/v2/cases/{case_id}/analyses` | 启动综合分析 |
| GET | `/api/v2/operations/{operation_id}` | 读取分析或草案任务进度 |
| GET | `/api/v2/cases/{case_id}/analyses/latest` | 读取最新分析和复核数据 |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/reviews` | 提交医生差量复核 |
| POST | `/api/v2/cases/{case_id}/analyses/{analysis_id}/draft-generation:retry` | 重试草案生成 |
| GET | `/api/v2/drafts/{draft_id}` | 读取方案和报告章节 |
| POST | `/api/v2/drafts/{draft_id}/approval` | 批准最终报告 |
| GET | `/api/v2/drafts/{draft_id}/report` | 恢复已批准报告状态和正文 |
| GET | `/api/v2/drafts/{draft_id}/report.pdf` | 下载 PDF |

严格 DTO、Problem Details 和请求示例见 [`api-v2-standardization.md`](api-v2-standardization.md)。

## 8. 验收清单

1. 使用管理员初始化系统，并创建两个独立医生账号。
2. 两个医生分别登录，确认病例列表相互隔离。
3. 创建病例并在同一入口上传病历、检查报告和问卷。
4. 确认上传成功后启动真实综合分析，观察业务阶段进度。
5. 复核异常指标、营养素来源和识别到的食敏指标。
6. 保存复核并等待草案生成，进入方案审核。
7. 从方案审核返回复核，确认页面停留在复核且原分析内容仍可查看。
8. 进入最终报告，修改一处唯一测试文字，批准并下载 PDF。
9. 刷新页面，确认最终正文、批准医生、批准时间和下载状态可以恢复。
10. 使用另一医生直接访问该病例 ID，确认返回 `403 CASE_ACCESS_DENIED`。

## 9. 常见问题

### 登录返回 `Internal Server Error`

先检查后端健康状态和前端 `INTERNAL_API_BASE_URL`。如果后端数据库被重建，原账号可能不存在，应根据登录页提示重新初始化管理员，而不是继续使用旧数据库中的密码。

### 可以登录但创建病例提示“写操作只允许从当前医生工作台发起”

确认浏览器地址与启动主机名完全一致，不要混用 `localhost`、`127.0.0.1` 或不同端口；清除旧页面后从 `/integration/cases` 重新进入。

### 点击“复核”后又回到“方案审核”

正确行为是草案首次生成完成时自动前进一次，之后允许手动返回复核。若仍出现重复跳转，请确认运行的是包含该导航修复的最新前端构建，并重新构建前端容器。

## 10. 甲方融合边界

当前实现是独立的同风格五步工作台，并未反编译或复制甲方构建产物。后续拿到甲方可编辑源码、SSO、患者/就诊映射、回传和权限协议后，应优先替换页面外壳、身份映射和主题变量，不应为纯视觉适配重写 `/api/v2`、差量构建或工作流状态机。
