# Ubuntu + Docker 部署说明

适用系统：Ubuntu Server 22.04 LTS、24.04 LTS 或 26.04 LTS。

以下示例使用：

- 项目目录：`/opt/functional-medicine-ai`
- 前端本机端口：`3100`
- 后端本机端口：`7800`
- Nginx 对外端口：`80`

## 1. 安装 Docker Engine

```bash
sudo apt update
sudo apt install -y ca-certificates curl git nginx

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
```

验证安装：

```bash
docker version
docker compose version
docker run --rm hello-world
```

## 2. 下载项目

```bash
sudo mkdir -p /opt/functional-medicine-ai
sudo chown "$USER":"$USER" /opt/functional-medicine-ai
git clone --branch dev1 https://github.com/111qaz1/functional-medicine-ai.git /opt/functional-medicine-ai
cd /opt/functional-medicine-ai
```

私有仓库需使用有权限的 GitHub 账号或 Deploy Key。

## 3. 配置环境变量

```bash
cp .env.example .env
nano .env
```

将 `SERVER_IP` 替换为服务器实际 IP；使用域名时替换为域名。浏览器 API 统一由 Next.js 同源转发，不需要配置浏览器可见的后端地址。

```env
BACKEND_PORT=7800
FRONTEND_PORT=3100
FM_PUBLIC_BASE_URL=http://SERVER_IP
FM_SESSION_COOKIE_SECURE=0

LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_API_KEY=替换为Kimi_API_Key
LLM_MODEL=kimi-k2.6
LLM_API_STYLE=chat

FM_LLM_MAX_CONCURRENCY=90
FM_LLM_RPM_SOFT_LIMIT=475
FM_LLM_TPM_SOFT_LIMIT=2850000
FM_ANALYSIS_WORKERS=20
FM_CASE_DOCUMENT_WORKERS=2

FM_MAX_UPLOAD_MB=50
FM_MAX_PDF_PAGES=50
FM_EXTERNAL_TRUST_SHARED_SECRET=替换为随机高强度字符串
```

没有 `bge-m3` 模型时设置：

```env
FM_RAG_ENABLED=0
FM_RAG_LLM_FUSION_ENABLED=0
```

已有模型时，将模型目录复制到 `/opt/functional-medicine-ai/bge-m3`，并保持：

```env
FM_RAG_ENABLED=1
FM_RAG_MODEL_HOST_DIR=./bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
```

创建持久化目录：

```bash
mkdir -p .runtime/uploads knowledge bge-m3 backups
```

## 4. 构建并启动

```bash
docker compose config
docker compose up -d --build
docker compose ps
```

验证容器本机端口：

```bash
curl http://127.0.0.1:7800/health
curl -I http://127.0.0.1:3100
```

健康检查应返回：

```json
{"status":"ok"}
```

## 5. 配置 Nginx

Compose 默认只把前后端端口绑定到 `127.0.0.1`，外部设备通过 Nginx 访问。

```bash
sudo nano /etc/nginx/sites-available/functional-medicine-ai
```

写入：

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 100m;

    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    location / {
        proxy_pass http://127.0.0.1:3100;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }
}
```

启用配置：

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sfn /etc/nginx/sites-available/functional-medicine-ai /etc/nginx/sites-enabled/functional-medicine-ai
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

启用防火墙：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
sudo ufw status
```

浏览器访问：`http://SERVER_IP`。

公网或正式医疗环境应配置域名和 HTTPS，参见 `docs/nginx-production-deployment.md`。

## 6. 常用命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
docker compose logs -f backend

# 重启
docker compose restart

# 停止并保留数据
docker compose down

# 更新代码并重新部署
git pull --ff-only
docker compose up -d --build
```

## 7. 数据备份

```bash
cd /opt/functional-medicine-ai
docker compose stop
tar -czf "backups/runtime-$(date +%Y%m%d-%H%M%S).tar.gz" .runtime
docker compose start
```

恢复时停止容器，将备份中的 `.runtime` 还原到项目根目录后重新启动。

## 8. 故障排查

```bash
docker compose ps
docker compose logs --tail 200 backend
docker compose logs --tail 200 frontend
sudo nginx -t
sudo tail -n 100 /var/log/nginx/error.log
```

- 页面可打开但接口失败：检查前端日志，并确认 Compose 为前端设置了 `INTERNAL_API_BASE_URL=http://backend:8000`。
- 后端不健康：查看 `docker compose logs backend`，检查 Kimi 配置和模型目录。
- RAG 模型缺失：复制完整模型到 `bge-m3`，或关闭 `FM_RAG_ENABLED`。
- Kimi 请求超时：检查服务器能否访问 `https://api.moonshot.cn`。

Docker Engine 安装命令以 [Docker 官方 Ubuntu 文档](https://docs.docker.com/engine/install/ubuntu/) 为准。
