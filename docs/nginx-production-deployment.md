# Nginx 正式部署说明

这份说明用于正式环境。测试环境可以直接访问 `3000` 和 `8000`，正式环境建议统一通过 Nginx 暴露 `80/443`，前端和后端只在服务器本机监听。

完整 `.env`、端口、HTTPS、千问 API Key 和 RAG 模型目录推荐配置见：`docs/production-recommended-config.md`。

## 部署结构

```text
浏览器 / 甲方系统
        |
        | HTTPS 443
        v
Nginx 反向代理
        |-- / 前端页面       -> 127.0.0.1:3000
        |-- /auth 等后端接口 -> 127.0.0.1:8000
        |-- /api/v1 外部接口 -> 127.0.0.1:8000
```

正式环境只需要对外开放 `80` 和 `443`。`3000`、`8000` 不对外开放。

## 环境变量

项目根目录 `.env` 建议配置为：

```env
NEXT_PUBLIC_API_BASE_URL=https://正式域名
FM_CORS_ALLOW_ORIGINS=https://正式域名
FM_SESSION_COOKIE_SECURE=1
```

示例：

```env
NEXT_PUBLIC_API_BASE_URL=https://fm.example.com
FM_CORS_ALLOW_ORIGINS=https://fm.example.com
FM_SESSION_COOKIE_SECURE=1
```

注意：`NEXT_PUBLIC_API_BASE_URL` 会写入前端构建结果，修改后必须重新构建前端镜像或重新执行 `npm run build`。

## Docker Compose 启动

当前 `compose.yaml` 默认只把容器端口绑定到服务器本机：

```text
127.0.0.1:3000 -> frontend
127.0.0.1:8000 -> backend
```

启动：

```bash
docker compose up --build -d
```

本机验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:3000
```

## 非 Docker 启动

后端只监听本机：

```bash
cd functional-medicine-ai
source .venv/bin/activate
export PYTHONPATH=backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端生产启动：

```bash
cd functional-medicine-ai/frontend
npm run build
npm run start -- --hostname 127.0.0.1 --port 3000
```

## Nginx 配置

仓库提供示例配置：

```text
deploy/nginx/functional-medicine-ai.conf.example
```

复制到 Nginx 站点目录：

```bash
sudo cp deploy/nginx/functional-medicine-ai.conf.example /etc/nginx/sites-available/functional-medicine-ai.conf
sudo ln -s /etc/nginx/sites-available/functional-medicine-ai.conf /etc/nginx/sites-enabled/functional-medicine-ai.conf
```

把配置中的 `fm.example.com` 替换为正式域名，并确认 HTTPS 证书路径正确。

校验并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 证书

如果使用 Let's Encrypt，可以参考：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 正式域名
```

证书申请完成后再次校验：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 验收

服务器本机：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:3000
```

域名访问：

```bash
curl https://正式域名/health
curl https://正式域名/openapi.json
```

浏览器检查：

- 打开 `https://正式域名`
- 注册或登录医生账号
- 创建病例
- 上传病例和问卷
- 生成营养素推荐
- 审核并下载 PDF

安全检查：

- 公网只开放 `80/443`。
- 不直接开放 `3000/8000`。
- 浏览器地址栏显示 HTTPS 正常。
- 登录后刷新页面仍保持会话。
