# Deployment Guide

## 目标交付形态

建议交付给部署方的是：
- 源码仓库
- `.env.example`
- `compose.yaml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- 本地数据文件：产品目录、已审核知识、指标字典
- 必要的本地资料目录：`功能医学相关资料/`

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
- `FM_REPORT_REFERENCE_PATH`

3. 确认以下目录存在
- `功能医学相关资料/`
- `.runtime/`

## Docker 启动

```bash
docker compose up --build
```

默认端口：
- Frontend: `3000`
- Backend: `8000`

## 验收步骤

1. 打开 `http://localhost:3000`
2. 创建一个新病例
3. 上传测试报告
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
- 如果部署方没有完整的 `功能医学相关资料/` 目录，系统仍能启动，但知识资料清单会不完整
- 如果以后要接云端 LLM，建议只把它作为报告整理层，不改变本地规则边界
