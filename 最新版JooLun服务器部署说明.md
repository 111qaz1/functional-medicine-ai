# JooLun 8600 单入口部署

更新日期：2026-10-22。适用于 `111qaz1/joolun` 医生端和 `111qaz1/functional-medicine-ai` 的 Java Token 融合版本。发布前记录两个仓库的实际提交 SHA；离线交付时同时保存源码版本、完整 dist 和校验和。

## 1. 当前链路与更新范围

```text
浏览器 → 8600 Nginx
          ├─ / 与 Vue 静态资源 → 医生端 dist
          ├─ /prod-api/* → 原 Java 后端
          └─ AI 页面、资源和 /api/v2/* → 内部 Next.js:3100 → FastAPI:8000
```

Vue 通过同源 `/integration/embed/session` 携带当前 Java 登录凭据建立或恢复会话；FastAPI 向固定 Java 地址核验医生身份及患者访问权限。Next.js 写入 HttpOnly AI Cookie，并为后续 AI 请求完成 Cookie → Bearer 转换。浏览器不再使用共享签名密钥，也不直接访问 FastAPI。

- 仅更新医生端业务或 20261021 融合版：构建并替换完整 Vue dist；Java 已支持对应接口时，无需重建 AI 服务。
- 从旧签名版本升级到 Java Token 版本：Vue、Next.js、FastAPI 必须配套更新并重建，不能只更新前端。
- 本次上传修复：重建 Next.js frontend，更新 Nginx 上传上限；已经运行新版认证后端时，无需为上传修复重建 backend。

3200 已关闭时保持关闭，不需要为融合版重新开放。内部 Next.js、FastAPI 及其必要连接必须保留。`joolun-integration-test` 是历史联调仓库，本次不更新。

## 2. 配置文件

合入需要的变量，保留现有模型、数据目录、SKU 映射、issuer、密钥及挂载；不要整份覆盖服务器配置。

AI `.env`：

```ini
FM_PUBLIC_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_CORS_ALLOW_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_ENABLED=1
FM_JOOLUN_AUTH_MODE=java_token
FM_JOOLUN_JAVA_API_BASE_URL=https://paracelsus.huapont.com.cn:8600/prod-api
FM_JOOLUN_ISSUER=joolun-windows-pilot
FM_SESSION_COOKIE_SECURE=1
FM_INTERNAL_ADMIN_ORIGINS=
INTERNAL_API_BASE_URL=http://backend:8000
```

issuer 必须沿用当前病例映射值。Java 核验地址可以是已有受信任内网地址，须从 backend 容器可达。`FM_INTERNAL_ADMIN_ORIGINS` 仅用于独立管理页面写操作；没有管理入口时留空，不影响 8600 医生端。

医生端 `.env.production`：

```ini
VITE_APP_BASE_API=/prod-api
VITE_FM_EMBED_ENABLED=true
VITE_FM_EMBED_ORIGIN=https://paracelsus.huapont.com.cn:8600
```

不需要 `.local` 文件。检查已有 `.env.local`、`.env.production.local` 和构建进程是否覆盖这些值；移除旧 `VITE_FM_JOOLUN_EMBED_SHARED_SECRET`、`VITE_FM_AI_API_BASE_URL` 和前端 issuer 配置。`VITE_*` 在构建时写入 dist，修改后需要重新构建。

`compose.yaml` 的 frontend.environment 须保留 `INTERNAL_API_BASE_URL`、`FM_JOOLUN_EMBED_BASE_URL`、`FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS`、`FM_INTERNAL_ADMIN_ORIGINS` 和 Secure Cookie 变量引用。backend 通过原 `env_file` 加载 `.env`。保留已有端口绑定及卷，不把仓库默认回环地址覆盖到跨主机部署中。

## 3. 构建和启动

先备份配置、旧 dist、镜像和业务数据。所有 Compose 命令沿用原项目名、覆盖文件及目录；不执行 `down -v`。

医生端使用 Node.js 20 或 22：

```bash
npm ci --include=dev
npm run test:integration
npm run build:prod
```

离线交付完整 dist 时，部署主机无需再次安装 Node.js 或构建。将新 dist 放到 Nginx 可见的新版本目录，确认该目录直接含 `index.html`、`assets/` 及压缩资源。保留上一版目录；将旧 assets 中新版缺少的文件保留到新目录，不覆盖新版文件，避免长开页面请求旧 hash 资源失败。

AI 仅上传修复：

```bash
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml build frontend
docker compose --env-file .env -f compose.yaml up -d --no-deps --force-recreate frontend
```

从旧认证版升级时，先将最新的 backend、frontend 配套源码更新到部署目录，再将上述 build/up 的服务改为 `backend frontend`。仅 `restart` 不能加载新环境，只有重新创建容器也不能更新旧镜像内的代码。

## 4. Nginx 修改

使用现有 Nginx，不需要重新安装。文件位置以实际运行实例为准；Docker 内 `include` 使用容器可见路径，修改相应宿主机挂载文件。

首次配置时，将 `deploy/nginx/fm-next-proxy.conf.example` 和 `joolun-single-entry.locations.conf.example` 安装为 `.conf`，逐处修改 include 路径及真实 Next.js 上游。已有 `fm-ai.locations.conf` 时合并差异，不重复添加 location。

8600 保留原证书、Java `/prod-api/` 代理和 Vue 路由回退；Vue 更新只切换 `root` 到新 dist。AI 路由规则：

| 路径 | 处理 |
| --- | --- |
| `/integration/embed/*`、`/_next/*`、`/api/v2/*` | 经 Next.js 转发，覆盖 `X-FM-Entry` 为 `public` |
| `/api/internal/health` | 仅 GET 健康检查 |
| 其余 `/api/internal/*`、`/api/v1/*` | 拒绝，不对公网开放旧管理 API |

公共代理片段保留 `Host $http_host`、`X-Forwarded-Host $http_host`、`X-Forwarded-Proto $scheme`，清除客户端 `X-FM-Session-BFF`，保留 Cookie、Authorization 和请求体。不清空或伪造 AI 请求的 Origin，不把 `/api/v2/` 直接转发到 FastAPI，不增加 `/ai/` 前缀。

上传上限在实际 `/api/v2/` location 或其公共片段内设置：

```nginx
client_max_body_size 110m;
proxy_read_timeout 900s;
proxy_send_timeout 900s;
```

默认后端单文件 50 MiB、每批最多 10 个且文件合计 100 MiB；Nginx 110m 和 Next.js 110mb 为 multipart 边界及字段预留空间。后端限制不因代理放宽而改变。自定义 `FM_MAX_UPLOAD_*` 时同步检查每层上限；前置网关也需允许该请求大小。只在 server 设置时，确认 location 没有更小的覆盖值。

在实际实例检查成功后再重载，例如 Docker：

```bash
docker exec <Nginx容器名> nginx -t
docker exec <Nginx容器名> nginx -s reload
```

宿主机部署使用该实例的 `nginx -t`、`nginx -s reload`；原有 `-c`、`-p` 参数需保留。检查失败不重载。

不使用独立管理页面时无需第三个管理片段。确需管理入口时，另配受限 HTTPS 站点、实际管理 Origin 和可信代理来源控制，再引用 `joolun-private-admin.conf.example`。新旧 AI 并行运行使用独立域名：Cookie 不按端口隔离，只改 3200 为 3201 不能避免同名 `fm_session` 冲突。

## 5. 验收与故障定位

开发环境可运行 `python scripts/test_single_entry_nginx.py` 检查 Nginx 分流和 100 MiB multipart 转发；完成 frontend 生产构建后，运行 `node scripts/test_upload_proxy.mjs` 检查 Next.js 的相同上传边界。两者只使用本机合成数据。

只输出必要配置，避免打印完整 `.env`：

```bash
docker compose --env-file .env -f compose.yaml exec frontend printenv FM_JOOLUN_EMBED_BASE_URL
docker compose --env-file .env -f compose.yaml exec frontend printenv INTERNAL_API_BASE_URL
docker compose --env-file .env -f compose.yaml exec backend python -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json")); print("POST token-sessions:", "post" in d.get("paths",{}).get("/api/v2/integrations/joolun/token-sessions",{}))'
```

预期依次为 8600 公开地址、`http://backend:8000`、`True`。

| 现象 | 重点检查 |
| --- | --- |
| session 403，提示只允许当前工作台 | frontend 运行环境是否仍是 3200，代理是否转到旧实例或改写 Origin；终端环境优先于 `.env` |
| session 登录后 404 | backend 是否有 `POST token-sessions`；更新源码并重建 backend |
| 上传 413，响应为 HTML/普通文本 | 实际 Nginx location、公共片段和前置网关上传上限；仅改 `.env` 不够 |
| 上传 413，响应为应用 problem JSON | 按错误码检查后端单文件、数量或批次限制 |

从 8600 登录验证客户切换、AI 会话、上传分析、校对批准、推荐回填、报告下载和刷新恢复。前端融合版另验证自由搭配单项每日 8、各餐合计 6、配方恢复、海关登记人、机构定价和角色权限。浏览器不得依赖 3200/3100/7800；自动化合成数据验证不代替真实登录和业务验收。

回退 Vue 时恢复旧 root，检查后 reload。回退 AI 时恢复对应镜像和环境再创建容器；不恢复旧数据库覆盖期间新增的数据。3200 的开放状态按当前已验收方案处理，无需为回退普通 Vue 更新重新开放。
