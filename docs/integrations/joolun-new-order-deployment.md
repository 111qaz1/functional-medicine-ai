# 两仓库 main 源码更新部署

适用于现有 8600 融合环境，包含新开单独立 AI 病例、前端业务融合及上传修复。采用源码替换方式，更新后保留两份完整源码供后续维护。

## 1. 下载并替换源码

下载并解压：

- [医生端 main.zip](https://github.com/111qaz1/joolun/archive/refs/heads/main.zip)，目录为 `joolun-main`。
- [AI main.zip](https://github.com/111qaz1/functional-medicine-ai/archive/refs/heads/main.zip)，目录为 `functional-medicine-ai-main`。

先备份现有两个项目源码、站点文件、AI 镜像、Nginx 配置及 AI 数据。将压缩包中的源码更新到对应项目目录，保留服务器原配置：医生端 `.env.production` 及已有环境覆盖文件；AI `.env`、`compose.yaml` 及覆盖文件。数据库、附件、报告、模型和知识库挂载目录不删除、不覆盖。无需修改 Java 后端或数据库结构。构建可提前完成，两个项目的切换安排在同一个短维护窗口。

## 2. 构建医生端

在更新后的医生端源码目录执行，使用 Node.js 20 或 22：

```bash
npm ci --include=dev
npm run build:prod -- --outDir web-release-20261022
```

生产配置继续使用 `VITE_APP_BASE_API=/prod-api`、`VITE_FM_EMBED_ENABLED=true`、`VITE_FM_EMBED_ORIGIN=https://paracelsus.huapont.com.cn:8600`，检查原环境覆盖文件没有将地址改回旧入口或 localhost。

把本次构建输出的 `web-release-20261022` 放入 Nginx 可见的站点目录，源码仍保留在项目目录。保留旧站点目录用于回退；旧 assets 中新版缺少的文件一并保留，不覆盖新版同名文件。

## 3. 重建 AI 服务

在更新后的 AI 部署目录执行，沿用原 Compose 项目名及覆盖文件参数：

```bash
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml build backend frontend
docker compose --env-file .env -f compose.yaml up -d --no-deps --force-recreate backend frontend
docker compose --env-file .env -f compose.yaml ps
```

本次两个服务都要重建。现有 8600、Java 核验地址、issuer、密钥及数据路径保持原值，不执行 `down -v`。

## 4. 更新 Nginx

将 8600 的 Vue `root` 指向本次构建后的站点目录，例如容器内 `/usr/share/nginx/html/web-release-20261022`。保留证书、`/prod-api/`、AI 路径代理和 Vue 路由回退。

在 `/api/v2/` 引用的公共代理片段中确认 `client_max_body_size 110m;`；前置网关也需允许该大小。后端仍限制单文件 50 MiB、每批最多 10 个、文件合计 100 MiB。

在实际 Nginx 实例中检查成功后再重载。Docker 示例，替换容器名：

```bash
docker exec <Nginx容器名> nginx -t
docker exec <Nginx容器名> nginx -s reload
```

检查失败不重载。3200 保持关闭，内部 Next.js/FastAPI 服务继续运行。

## 5. 验收和回退

从 8600 强制刷新并登录，为同一客户新开单，上传、分析并生成报告；刷新当前单应恢复本次 AI 病例。再次“新开单”或“继续开单”应保留客户、清空上单内容，并可重新上传分析。旧报告和附件保留在后台，不自动进入新单。

异常时配套恢复旧源码及配置、AI 镜像和站点文件，检查后重载 Nginx；不恢复旧数据库覆盖更新期间新增的数据。
