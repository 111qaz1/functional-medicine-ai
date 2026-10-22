# JooLun 8600 源码部署说明

更新方式：下载两个仓库的 main 源码压缩包，替换对应源码，再在部署环境完成构建。源码保留在项目目录，便于查看和维护本次修改。

完整步骤及下载链接见 [两仓库 main 源码更新部署](docs/integrations/joolun-new-order-deployment.md)。本次需配套更新医生端、AI backend 和 frontend；保留原环境、部署配置和业务数据。

## 现有融合环境配置

医生端 `.env.production` 保持：

```ini
VITE_APP_BASE_API=/prod-api
VITE_FM_EMBED_ENABLED=true
VITE_FM_EMBED_ORIGIN=https://paracelsus.huapont.com.cn:8600
```

AI `.env` 保持以下配置，Java 核验地址和 issuer 沿用现有实际值：

```ini
FM_PUBLIC_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_BASE_URL=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_CORS_ALLOW_ORIGINS=https://paracelsus.huapont.com.cn:8600
FM_JOOLUN_EMBED_ENABLED=1
FM_JOOLUN_AUTH_MODE=java_token
FM_SESSION_COOKIE_SECURE=1
INTERNAL_API_BASE_URL=http://backend:8000
```

不使用独立管理页面时，`FM_INTERNAL_ADMIN_ORIGINS` 留空。模型、密钥、数据与挂载路径不随源码覆盖。首次配置参考 `deploy/joolun-single-entry.env.example`，只合入需要的变量。

## 当前行为

浏览器从 8600 进入：原业务经 `/prod-api/` 调用 Java，AI 页面和接口经内部 Next.js 调用 FastAPI。3200 保持关闭，内部服务继续运行。

新开单建立独立空白 AI 病例，同单刷新恢复进度；旧报告与附件保留在后台，不自动带入新单。新旧系统并行使用时，Cookie 不按端口隔离，应使用独立域名。

## 排查

- 会话 403：检查 frontend 实际加载的公开地址是否为 8600，代理是否改写 Origin 或指向旧实例。
- 登录后会话 404：检查 backend 是否已更新并包含 `POST /api/v2/integrations/joolun/token-sessions`。
- 上传 413：检查实际 `/api/v2/` 代理及前置网关的上传上限；应用返回的结构化错误按单文件、数量或批次限制处理。
- 新开单仍是旧报告或提示开单标识不匹配：确认两个仓库源码已配套更新并完成构建，浏览器强制刷新。
