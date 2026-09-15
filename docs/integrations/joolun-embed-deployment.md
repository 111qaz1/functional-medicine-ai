# Joolun 嵌入模式生产部署

`172.16.0.140` HTTP 内网联调采用四服务 Compose 与独立 Node 换票桥接，详见
甲方仓库 `docs/joolun-server-deployment.md`。下面的 HTTPS/Java 部署说明适用于后续正式切换。

本文档说明基础部署完成之上，启用"甲方开方页嵌入 AI 工作流"所需的增量配置。
适用于甲方部署融合系统的场景。

- 基础部署（`.env`、Nginx、HTTPS、千问、RAG）：见 [production-recommended-config.md](../production-recommended-config.md)。
- 接口契约（换票签名、幂等规则、事件协议）：见 [joolun-embed.md](./joolun-embed.md)。
- 甲方侧代码改动与部署：见甲方仓库 `joolun-integration-test` 的 `docs/functional-medicine-integration.md`。

## 部署架构

```text
医生浏览器
    |
    | HTTPS
    v
甲方系统 (doctor.example.com)
    |-- 开方页 iframe 加载 AI 工作流
    v
功能医学 AI (fm.example.com)
    |-- Nginx 443 -> frontend:3000 -> backend:8000
```

AI 系统通过 Nginx 对外提供 `https://fm.example.com`，与甲方系统各自独立域名。

## .env 追加配置

在基础部署的 `.env` 上追加：

```env
# 嵌入开关
FM_JOOLUN_EMBED_ENABLED=1

# 换票共享密钥，与甲方 Java 后端一致，双方线下交付，不进 Git
FM_JOOLUN_EMBED_SHARED_SECRET=replace-with-joolun-shared-secret

# AI 系统对外域名：用于生成 embed_url 和校验 API 写操作来源
FM_JOOLUN_EMBED_BASE_URL=https://fm.example.com

# 允许嵌入 AI 页面的甲方 Origin（CSP frame-ancestors 来源）
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS=https://doctor.example.com
```

### 变量说明

| 变量 | 作用 | 不配置的后果 |
|---|---|---|
| `FM_JOOLUN_EMBED_ENABLED` | 嵌入换票接口总开关 | 换票接口拒绝，AI 区域显示"AI 辅助暂时不可用" |
| `FM_JOOLUN_EMBED_SHARED_SECRET` | 与甲方 Java 后端的 HMAC 签名密钥 | 签名校验失败，换票被拒 |
| `FM_JOOLUN_EMBED_BASE_URL` | embed_url 生成基址；v2 API 写操作的合法来源 | embed_url 用请求 Origin 兜底，经反向代理时可能不一致 |
| `FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS` | 允许加载嵌入页的父页面（多个用逗号分隔） | **嵌入页返回 `frame-ancestors 'none'`，iframe 被浏览器拦截（白屏）** |

`FM_SESSION_COOKIE_SECURE=1` 在基础部署中已要求，嵌入场景必须确认：
生产 HTTPS 未显式设置时，Docker 默认 `0` 会使嵌入会话 Cookie 带 `SameSite=Lax`，
跨站 iframe 内无法发送，AI 页面登录态丢失。

## SKU 映射文件

映射文件保存 `sku_id -> goodsId + specId` 对应关系，由双方审核后提供。

默认路径是容器内 `/app/backend/app/data/joolun_sku_mapping.json`（构建进镜像，不便于更新）。
生产推荐放在挂载卷 `.runtime` 中，并在 `.env` 指定：

```env
FM_JOOLUN_SKU_MAPPING_PATH=/app/.runtime/joolun_sku_mapping.json
```

把映射文件放到服务器项目根目录 `./.runtime/joolun_sku_mapping.json`：

```json
{
  "version": 2,
  "mappings": [
    {
      "sku_id": "sku_zinc_complex",
      "status": "mapped",
      "goods_id": "2091816225546137602",
      "spec_id": "2091817634534486017",
      "product_type": 0,
      "enabled": true
    },
    {
      "sku_id": "sku_super_anti_inflammatory",
      "status": "unmapped",
      "reason": "partner_product_missing",
      "enabled": true
    }
  ]
}
```

映射清单以双方最终审核结果为准。文件格式校验在启动后首次使用时执行，
`version` 必须为 `2`，`mapped` 项必须含 `goods_id` 和 `spec_id`，
`unmapped` 项必须含 `reason`。

## 启动与重启

```bash
docker compose up --build -d
```

`.env` 或映射文件变更后需重启后端生效：

```bash
docker compose restart backend
```

## 甲方侧配合事项

以下由甲方完成，详见甲方仓库 `docs/functional-medicine-integration.md`：

1. Java 后端实现 `POST /admin/api/doctor/integrations/functional-medicine/embed-session`
   同源换票接口（从登录会话取医生身份，服务端 HMAC 签名后调 AI 后端换票）。
2. 甲方生产构建配置 `VITE_FM_EMBED_ORIGIN=https://fm.example.com`。
3. 提供稳定的就诊 ID 或处方草稿 ID（替换浏览器 sessionStorage 临时草稿键）。
4. 生产包删除本地 Vite 桥接（`functional-medicine-bridge.js`）。

## 嵌入链路验收清单

基础部署验收（独立访问 `https://fm.example.com`，见
[production-recommended-config.md](../production-recommended-config.md)）通过后，
在甲方开方页执行端到端验收：

1. 医生登录甲方系统后进入开方页，第二步左栏出现 AI 工作流，无需二次登录。
2. AI 页面内上传病例资料，执行综合分析，五步工作流可走完。
3. 批准推荐后，商品自动加入甲方处方，剂量回填正确。
4. 医生手动修改过的商品不被 AI 覆盖。
5. 刷新甲方页面，同一患者恢复同一 AI 病例。
6. 用甲方提供的正式就诊 ID 验证跨设备恢复。

## 常见问题排查

**AI 区域显示"AI 辅助暂时不可用"**，按顺序排查：

1. `FM_JOOLUN_EMBED_ENABLED` 是否为 `1`。
2. 甲方 Java 换票接口是否实现并可达（浏览器 F12 看同源请求返回）。
3. `FM_JOOLUN_EMBED_SHARED_SECRET` 双方是否一致。
4. 服务器时钟偏差（签名时间窗口 5 分钟）。

**iframe 打不开或白屏**：

1. `FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS` 是否包含甲方生产 Origin
   （协议 + 域名 + 端口完全一致，无尾斜杠）。
2. 浏览器控制台是否有 CSP `frame-ancestors` 拦截信息。

**AI 页面能打开但操作报"医生登录已失效"**：

1. `FM_SESSION_COOKIE_SECURE=1` 是否已设置。
2. Nginx 是否传递 `X-Forwarded-Proto`。
