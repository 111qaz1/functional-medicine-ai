# 新开单 AI 病例部署步骤

适用于现有 8600 融合环境。本次包含前端融合、新开单独立病例及上传限制修复。

## 1. 准备源码和备份

取得两份 PR 的最新完整源码：[医生端 #5](https://github.com/111qaz1/joolun/pull/5)、[AI #49](https://github.com/111qaz1/functional-medicine-ai/pull/49)。未合并前不要直接下载 main。

备份现有 dist、AI 镜像、Nginx 配置及 AI 数据。更新源码时保留服务器 `.env`、`compose.yaml`、数据和模型挂载目录，不整目录删除，不执行 `down -v`。无需修改 Java 后端或数据库结构。

## 2. 构建医生端

生产配置仍使用 8600 和 `/prod-api`，不改成 localhost。在医生端源码目录执行：

```bash
npm ci --include=dev
npm run build:prod
```

将完整 dist 上传到 Nginx 可见的新版本目录，确认目录内直接有 `index.html` 和 `assets/`，保留旧目录用于回退。保留旧 assets 中新版缺少的 hash 文件，不覆盖新版同名资源。

## 3. 更新 AI 服务

在 AI 部署目录执行，沿用原 Compose 项目名及覆盖文件参数：

```bash
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml build backend frontend
docker compose --env-file .env -f compose.yaml up -d --no-deps --force-recreate backend frontend
docker compose --env-file .env -f compose.yaml ps
```

本次必须同时更新医生端和 backend；frontend 重建用于同步上传限制和 413 提示。现有 8600、Java 核验、issuer、密钥及数据路径配置保持原值。

## 4. 切换 Nginx

将 8600 的 Vue `root` 改为新 dist 目录；保留证书、`/prod-api/`、AI 路径代理和 Vue 路由回退。

在 `/api/v2/` 使用的公共代理片段内确认：

```nginx
client_max_body_size 110m;
```

其他前置网关也需允许该大小。后端仍限制单文件 50 MiB、每批最多 10 个、文件合计 100 MiB。

在实际运行的 Nginx 实例中检查成功后再重载。Docker 部署示例，先将容器名替换为实际值：

```bash
docker exec <Nginx容器名> nginx -t
docker exec <Nginx容器名> nginx -s reload
```

检查失败不要重载。3200 保持关闭，内部 Next.js/FastAPI 服务继续运行。

## 5. 验收和回退

从 8600 强制刷新并登录，为同一客户新开单，上传、分析并生成报告；刷新当前单应恢复本次 AI 病例。再次“新开单”或“继续开单”应保留客户、清空上单内容，AI 可重新上传分析，旧报告不自动展示。历史报告和附件仍保留在后台。

异常时配套恢复旧 dist、AI 镜像及 Nginx 配置，检查后重载；不恢复旧数据库覆盖更新期间新增的数据。
