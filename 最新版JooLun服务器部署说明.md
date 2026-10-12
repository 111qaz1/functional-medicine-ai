# JooLun 单入口部署说明

## 1. 版本与改动

医生端仓库：[111qaz1/joolun](https://github.com/111qaz1/joolun)，本次基于 main 的 `a163032`，保留已有剂量显示修复。AI 仓库：[111qaz1/functional-medicine-ai](https://github.com/111qaz1/functional-medicine-ai)，本次基于 main 的 `3f75051`。

两个仓库的交付分支均为 `codex/joolun-single-entry-8600`。部署使用本次 PR 交付清单中的完整提交 SHA，不能再使用旧的 `codex/joolun-latest-ai-integration` 分支或 `145eaad`。部署前以 `git rev-parse HEAD` 核对实际版本；PR 未合并时，不从 main 获取本次改动。源码有本地修改时先保存，不能强制覆盖。

本次改动：
- Vue 生产配置改用 8600 同源 AI 地址；保留原 iframe、签名、商品回填及 Java 接口。
- Next.js 的换票成功和失败跳转统一使用公开地址；网络异常、超时及无效响应进入嵌入错误页。
- 新增前端变量 `FM_INTERNAL_ADMIN_ORIGINS`，供受控内网管理页面执行 v2 写操作；它不代替网络访问控制，也不增加 iframe 父来源。
- 8600 增加 AI 路径代理；3200 在验收后限制为内网管理。
- FastAPI 业务代码、接口协议、病例映射、数据库和商品映射不因本次入口迁移而变更；不新增 Java 接口。

共享密钥及浏览器签名方式本次保持不变。配置示例不含真实密钥。

## 2. 目标地址

| 地址 | 用途 |
| --- | --- |
| `https://paracelsus.huapont.com.cn:8600` | 唯一公开医生端入口 |
| 8600 的 `/prod-api/*` | 原 Java 后端，沿用原有上游 |
| 8600 的 `/integration/embed/*`、`/_next/*`、`/api/internal/*`、`/api/v2/*`、`/api/v1/*` | Next.js，内网 `172.16.0.140:3100` |
| `172.16.0.140:7800` | FastAPI；Next.js 容器仍通过 `http://backend:8000` 访问 |
| `https://paracelsus.huapont.com.cn:3200` | 仅受控内网/VPN 管理，不再对公网开放 |

保留 3100/7800 内部服务和原存储挂载。服务器文件名仍为 `compose.yaml`，不是本地备份名 `compose(2).yaml`。不要用仓库默认的 127.0.0.1 端口绑定覆盖服务器已有的 172.16.0.140 绑定。

## 3. 构建医生端

取得经过验证的交付提交，在服务器医生端源码目录确认 `VITE_APP_BASE_API=/prod-api`，生产配置为：

```ini
VITE_FM_EMBED_ENABLED=true
VITE_FM_EMBED_ORIGIN=https://paracelsus.huapont.com.cn:8600
VITE_FM_AI_API_BASE_URL=https://paracelsus.huapont.com.cn:8600/api/internal
VITE_FM_JOOLUN_ISSUER=joolun-windows-pilot
```

issuer 沿用服务器当前值；若原部署值不同，不改成示例值。将现有 `VITE_FM_JOOLUN_EMBED_SHARED_SECRET` 保留在忽略的 `.env.production.local` 或构建环境中，不能提交 Git。首次配置可参考医生端的 `.env.production.local.example`，不要覆盖已有私有配置。

检查 `.env.local`、`.env.production.local` 及进程环境是否仍覆盖为 3200。Vite 构建时固化这些变量，修改后必须重新构建。

```bash
npm install --include=dev
node --test scripts/test-functional-medicine-embed.mjs scripts/test-functional-medicine-workbench.mjs
npm run build:prod
```

使用 Node.js 20 或 22；已有锁定依赖文件时沿用，优先 `npm ci`。发布完整 `dist`，包括 assets 和压缩资源，不能只替换 index.html。

本次本地无密钥构建只用于验证编译，不能当作已配置的生产包；服务器须带入现有密钥重新构建。不要在校验输出中打印密钥。

## 4. 更新 AI 环境和镜像

将 `deploy/joolun-single-entry.env.example` 中的值合入服务器原 `.env`：

```ini
FM_PUBLIC_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_CORS_ALLOW_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_INTERNAL_ADMIN_ORIGINS=https://paracelsus.huapont.com.cn:3200
FM_JOOLUN_EMBED_ENABLED=1
FM_SESSION_COOKIE_SECURE=1
INTERNAL_API_BASE_URL=http://backend:8000
BACKEND_PORT=7800
FRONTEND_PORT=3100
```

确有需要的既有联调来源可保留，以英文逗号分隔。独立本机 Vue 联调须改用 8600 的 AI 地址，并保留其父来源/CORS；该来源不能继续依赖公网 3200。若继承的 X-Frame-Options 为 SAMEORIGIN，本机跨来源嵌入需单独按原联调策略调整，不能把“已配置 CSP”视为浏览器链路已通过。

共享密钥、运行路径、SKU 映射及 LLM/RAG 配置保持原值。不要用示例文件整体覆盖服务器 .env。

确认服务器 `compose.yaml` 的 frontend.environment 含：

```yaml
INTERNAL_API_BASE_URL: ${INTERNAL_API_BASE_URL:-http://backend:8000}
FM_SESSION_COOKIE_SECURE: ${FM_SESSION_COOKIE_SECURE:-0}
FM_JOOLUN_EMBED_BASE_URL: ${FM_JOOLUN_EMBED_BASE_URL:-}
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS: ${FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS:-}
FM_INTERNAL_ADMIN_ORIGINS: ${FM_INTERNAL_ADMIN_ORIGINS:-}
```

后台通过现有 env_file 读取 .env。本次 Next.js 有代码改动，必须构建 frontend 镜像：

```bash
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml build frontend
```

先构建，不立即切换。维护窗口内执行：

```bash
docker compose -f compose.yaml up -d --no-build --force-recreate backend frontend
docker compose -f compose.yaml ps
```

沿用原项目名、覆盖文件参数和镜像标识。FastAPI 沿用已部署镜像，不需要因本次入口调整而重建。仅执行 restart 不会加载新环境变量。

## 5. 合并 Nginx 配置

使用本仓库三个示例文件：
- `deploy/nginx/fm-next-proxy.conf.example`：安装为 `/etc/nginx/snippets/fm-next-proxy.conf`。
- `deploy/nginx/joolun-single-entry.locations.conf.example`：安装为 `/etc/nginx/snippets/joolun-single-entry.locations.conf`，在现有 8600 server 内 include。
- `deploy/nginx/joolun-private-admin.conf.example`：验收后合入现有 3200 server，替换旧的 location / 并限制来源。

8600 保留证书、Vue root 和 /prod-api；在同一 server 中加入：

```nginx
include /etc/nginx/snippets/joolun-single-entry.locations.conf;

# 保留原 Vue 路由回退
location / {
    try_files $uri $uri/ /index.html;
}
```

已有 location / 时不要重复添加。新增路径如与已有 location 冲突，合并对应块，不能重复或覆盖现有业务。示例使用 exact 和 ^~ 匹配，防止通用 .js/.css 静态规则抢占 Next.js 资源。

代理保留路径和查询参数，保留 Host 的外部端口、Cookie、Authorization 和请求体。上传上限 100m，读写超时 900s，关闭继承的代理缓存。不使用 /ai/ 前缀，不在 Nginx 剥离 /api/internal，也不把 /api/v2 直接指向 7800。

Next.js 通过配置生成 CSP。同源 8600 iframe 可保留 X-Frame-Options: SAMEORIGIN；不能继承 DENY 或不允许 8600 的 frame-ancestors。医生端 frame-src/connect-src 应允许同源。保留应用返回的 Set-Cookie、Cache-Control 和 Referrer-Policy。

本次不对外代理独立 AI 首页及管理页面。产品、模型、医生账号等独立页面通过内网 3200 访问，使用原 AI 管理账号。

```bash
sudo nginx -t
sudo systemctl reload nginx
```

3200 的管理来源示例默认 deny all，仅放行回环地址。部署人员须加入批准的管理/VPN 网段，再移除公网 NAT/防火墙放行。内网 DNS 指向可达的内部入口，浏览器地址仍使用证书匹配的域名。存在前置代理时必须在前置入口实施访问控制或正确配置可信 real_ip；不要直接放行一个所有公网流量都会经过的代理地址。3100、7800 也不得存在绕过该限制的公网映射。

## 6. 发布顺序与验收

本地可先执行 `python scripts/test_single_entry_nginx.py`（需要 nginx 在 PATH，或通过 --nginx 指定），用合成上游检查实际 Nginx 路径、请求头、请求体和管理来源限制，不连接服务器业务数据。

1. 记录两个提交、当前镜像 ID；备份旧 dist、Nginx、.env、compose.yaml，确认数据库及附件备份可用。
2. 提前构建新 Vue 和 Next.js；准备路径分流，检查现有位置冲突和管理访问网段。
3. 在短维护窗口同步更新 AI 环境及容器、新 Vue dist 和 Nginx。
4. 完成下面的公开入口及业务验收，再将 3200 限制为内网；保留回退配置。

配置中的 AI 公开地址是单值。切到 8600 后不保证旧 3200 iframe 仍可写入，不采用新旧入口并行操作。切换后刷新医生端重新换票；不要复用旧的一次性 ticket。

无账号检查：

```bash
curl -I https://paracelsus.huapont.com.cn:8600/
curl https://paracelsus.huapont.com.cn:8600/api/internal/health
curl -sS -D - -o /dev/null https://paracelsus.huapont.com.cn:8600/integration/embed/start
curl -sS -D - -o /dev/null https://paracelsus.huapont.com.cn:8600/api/v2/cases
```

预期：首页 200；health 返回 status=ok；无票据返回 307，Location 为 8600 的 /integration/embed/error?code=missing-ticket，且有 no-store/no-referrer；未登录 v2 返回 JSON 401，不是 Vue HTML。

用户执行真实业务验收：
- 原 Java 登录、客户、商品、处方功能正常。
- 进入开方页后自动建立 AI 会话；上传、分析、校对、批准、剂量回填和 PDF 下载完整通过。
- 手工修改不被覆盖；刷新复用原分析标识；票据失效可重新加载。
- 开发者工具确认 AI 请求、Next.js 静态资源和下载均不再指向 3200、3100、7800 或 localhost。
- 内网管理员可登录，维护产品、模型和账号，v2 写入正常。
- 公网无法访问 3200；内网管理仍可用。

本地构建与合成请求测试不能代替服务器上述业务验收；不自动使用真实账号或病例。

## 7. 回退

保留旧镜像标签和配置直到验收结束。回退时恢复旧 dist、Next.js 镜像、.env 和 Nginx；如已限制 3200，则按原配置恢复公网规则，再重新创建容器并检查旧入口。

本次无数据库迁移。回退不能恢复旧数据库覆盖期间新产生的病例或附件，也不要删除 .runtime。
