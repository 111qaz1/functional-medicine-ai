# Deployment Guide

## 目标交付形态

建议交付给部署方的是：
- 源码仓库
- `.env.example`
- `compose.yaml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- 本地数据文件：产品目录、已审核知识、指标字典
- 可选外部资料目录：`knowledge/`

真实病例、原始医学资料、`.env`、`.runtime/` 和本地数据库不要放入源码仓库。整理后的 JSON/CSV 规则数据可以随源码交付。

## 启动前准备

1. 复制环境变量模板

```bash
cp .env.example .env
```

2. 根据部署机器调整路径和 API 地址
- `NEXT_PUBLIC_API_BASE_URL`
- `FM_RUNTIME_DIR`
- `FM_UPLOAD_DIR`
- `FM_SQLITE_PATH`
- `FM_KNOWLEDGE_ROOT`
- `FM_REPORT_REFERENCE_PATH`（可选）

3. 确认以下目录存在
- `.runtime/`
- `knowledge/`（如果需要加载外部资料清单）

## Docker 启动

新成员第一次本地复现项目，建议先阅读：`docs/docker-first-run.md`。

```bash
docker compose up --build
```

默认端口：
- Frontend: `127.0.0.1:3000`
- Backend: `127.0.0.1:8000`

正式环境建议不要直接暴露 `3000/8000`，使用 Nginx 统一代理 `80/443`。

正式部署推荐配置参考：

- `docs/production-recommended-config.md`
- `docs/nginx-production-deployment.md`

## 验收步骤

1. 打开 `http://localhost:3000`
2. 创建一个新病例
3. 上传验收报告或脱敏样例报告
4. 进入解析校对页面并保存
5. 提交问卷
6. 生成结构化草案
7. 审核并发布

## 数据持久化

容器默认把本地运行数据写入：
- `.runtime/app.sqlite3`
- `.runtime/uploads/`

只要保留这个 volume 或目录，重启容器后数据不会丢失。

## 常见注意事项

- 如果前端能打开但请求后端失败，先检查 `NEXT_PUBLIC_API_BASE_URL` 是否指向浏览器可访问地址
- 如果部署方没有 `knowledge/` 目录或目录为空，系统仍能启动，但外部资料清单会为空
- 如果以后要接云端 LLM，建议只把它作为报告整理层，不改变本地规则边界
