# RAG 知识库接入与运维指南

本文档面向项目维护者，说明本地功能医学 RAG 知识库从语料构建、索引生成、Docker 运行到报告接入的完整流程。RAG 只作为报告描述增强层，不是推荐决策层。

## 安全边界

必须长期保持以下约束：

- 产品推荐仍只来自现有产品目录、规则引擎、医生规则和人工审核。
- RAG 命中不得推荐目录外营养素或产品。
- 涉及药物、处方剂量、高风险治疗、直接产品宣称、禁忌冲突或医生规则冲突的片段，会在进入报告生成前被 `rag_safety.py` 拒绝。
- 纯英文片段、英文实验室项目列表、英文 OCR 断句残片、中文比例过低片段，会被语料质量门禁拒绝。
- 客户可见报告不得显示教材名称、文件路径、页码、chunk id、`RAG` 标签或“功能医学知识库（仅供参考）”。
- 原始 DOCX/PDF 教材、真实病例、`.env`、API Key、运行时数据库、上传文件和本地模型权重不得提交到 Git。

## 已纳入仓库的交付物

RAG 必要数据位于 `backend/app/data`：

- `rag_corpus.jsonl`：清洗、去重后的 RAG 语料，每行是一条可检索片段，包含片段正文、来源类型、章节、主题标签等元数据。
- `rag_import_report.json`：语料导入统计，包括来源数量、过滤原因、去重数量、主题分布和安全说明。
- `rag_index/index.faiss`：FAISS dense 向量索引，用于语义检索。
- `rag_index/metadata.pkl`：检索元数据和 BM25 分词缓存，用于混合检索、片段回溯和稀疏关键词召回。
- `rag_index/manifest.json`：索引构建清单，记录模型、维度、语料哈希、构建时间和索引后端。

`BAAI/bge-m3` 模型权重不进入仓库。本地 Docker Compose 通过 `FM_RAG_MODEL_HOST_DIR` 把外部模型目录只读挂载到容器内 `/models/bge-m3`。

## 构建语料

原始 DOCX 仅保存在授权的本地目录，不进入 Git。构建命令示例：

```powershell
python scripts\build_rag_corpus.py `
  --docx-dir "D:\medical\AI学习功能医学相关资料\功能医学概论" `
  --output-dir backend\app\data `
  --staging-dir backend\app\data\rag_staging
```

脚本读取：

- `backend/app/data/knowledge_statements.json`
- 授权 DOCX 教材目录

脚本输出：

- `backend/app/data/rag_corpus.jsonl`
- `backend/app/data/rag_import_report.json`
- `backend/app/data/rag_staging` 下的临时抽样审查文件

交付前应删除 `backend/app/data/rag_staging`。该目录已忽略，可按需重新生成。

## 构建索引

生产索引使用 `BAAI/bge-m3`：

```powershell
python scripts\index_builder.py `
  --corpus-path backend\app\data\rag_corpus.jsonl `
  --output-dir backend\app\data\rag_index `
  --embedding-backend sentence-transformers `
  --model-name BAAI/bge-m3 `
  --batch-size 16
```

当前索引清单：

- 模型：`BAAI/bge-m3`
- 向量维度：`1024`
- 向量后端：`sentence_transformers`
- FAISS 类型：`IndexFlatIP`
- 文档数：`9436`

仅用于快速冒烟测试时，可以用 `--embedding-backend hashing` 构建小型回退索引。该结果不能代表生产检索质量。

## 外部模型目录

不要把 `BAAI/bge-m3` 复制进项目仓库。推荐外部路径：

```text
C:/RAG/models/bge-m3
```

如果 Windows HuggingFace 缓存快照中包含 Linux 容器无法解析的符号链接，需要在仓库外物化为普通文件目录。同一磁盘上优先使用硬链接，可避免额外占用完整 2.3 GB 物理空间。

Docker Compose 相关环境变量：

```env
FM_RAG_MODEL_HOST_DIR=C:/RAG/models/bge-m3
FM_RAG_MODEL_PATH=/models/bge-m3
FM_RAG_LOCAL_FILES_ONLY=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

验证模型可用：

```powershell
docker compose exec -T backend python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('/models/bge-m3', local_files_only=True); print(len(m.encode(['甲状腺功能'], normalize_embeddings=True)[0]))"
```

期望输出为 `1024`。

本地 Compose 建议保持外部模型挂载。若发布镜像需要在构建阶段内置模型，可设置 `PRELOAD_RAG_MODEL=1`，但运行时不要错误设置一个不存在的 `FM_RAG_MODEL_PATH`，否则系统会优先加载该路径并失败。

## Docker Compose 本地运行

启动完整项目：

```powershell
docker compose up --build -d
```

检查健康状态：

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/health/rag | ConvertTo-Json -Depth 8
```

`/health/rag` 应至少包含：

```json
{
  "loaded": true,
  "ready": true,
  "dense_ready": true,
  "dense_dimension": 1024,
  "faiss_loaded": true,
  "bm25_loaded": true
}
```

访问地址：

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`

## 报告生成接入

RAG 命中与既有 `knowledge_hits` 保持独立。推荐服务会构建安全 RAG 命中，经过 `rag_safety.py` 过滤后，写入草案内部区块：

- `RAG内部审查`
- `RAG总体健康画像`
- `RAG异常指标解释`
- `RAG生活方式干预`
- `RAG复查建议`

审核发布路径通过 `review_local.py` 将内部 RAG 命中自然融合到客户可见正文：

- 总体健康画像
- 关键指标
- 生活方式干预重点
- 复查与跟进建议

前端待审预览也会进行同类自然融合，避免医生在“审核后发布内容”中看到一份未增强的临时草稿。真实客户报告不显示 RAG 标记，也不单独列出“功能医学知识库（仅供参考）”。

个性化营养素方案不做额外 RAG 扩写。该区块以产品目录、推荐规则、禁忌和人工审核为准；药企方后续提供的产品营养素介绍可作为独立产品文案来源。

## 回归检查

检索质量检查：

```powershell
python scripts\evaluate_rag_retrieval.py --top-k 3
```

类 RAGAS 的确定性客观检查：

```powershell
python scripts\evaluate_rag_objective.py
```

固定病例前后对比：

```powershell
python -m scripts.generate_rag_report_comparison
```

Docker 后端安全测试：

```powershell
docker compose exec -T backend python -m unittest tests.test_safety_boundary
```

推荐草案和发布稿关键回归：

```powershell
docker compose exec -T backend python -m unittest `
  tests.test_recommendation.RecommendationServiceTests.test_keeps_internal_candidate_products_before_manual_parse_review `
  tests.test_recommendation.RecommendationServiceTests.test_approval_rejects_question_mark_corrupted_publishable_summary
```

## 添加下一本教材

1. 将新的授权 DOCX 放入本地非 Git 目录。
2. 重新运行 `scripts/build_rag_corpus.py`，`--docx-dir` 指向包含全部授权 DOCX 的目录。
3. 审查 `rag_import_report.json` 和 `rag_staging` 抽样文件。
4. 重新运行 `scripts/index_builder.py` 构建索引。
5. 运行检索、类 RAGAS、安全边界和固定病例对比检查。
6. 交付前删除 `rag_staging`，除非审查人明确要求保留抽样文件。

## 常见故障

### `DenseRetrievalUnavailable` 或 HuggingFace 离线错误

原因：容器无法从 `FM_RAG_MODEL_PATH` 加载 `BAAI/bge-m3`，常见于 Windows HuggingFace 缓存符号链接在 Linux 容器内不可读。

处理：

- 确认 `FM_RAG_MODEL_HOST_DIR` 指向仓库外的普通文件模型目录。
- 确认 `FM_RAG_MODEL_PATH=/models/bge-m3`。
- 重启 backend 后重新检查 `/health/rag`。

### `/health/rag` 显示索引已加载但 `dense_ready=false`

原因：FAISS 文件存在，但编码模型加载失败。

处理：

- 检查模型目录中是否存在 `config.json`、`modules.json`、`pytorch_model.bin`、tokenizer 文件和 `1_Pooling/config.json`。
- 从 HuggingFace 缓存重新物化外部模型目录，或在部署主机下载模型。

### Docker 构建下载 CUDA 包

`backend/Dockerfile` 应先安装 CPU 版 torch：

```dockerfile
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.7.1+cpu
```

如果再次出现 CUDA 依赖，检查该行是否仍位于 `pip install -r requirements.txt` 之前。

### 某个主题召回质量下降

运行：

```powershell
python scripts\evaluate_rag_retrieval.py --query "你的查询"
```

检查 dense/sparse 命中、主题标签和片段内容。若该查询具有临床重要性且持续弱召回，应调整分块策略或主题关键词后重建语料与索引。

### 报告中出现原始 RAG 文字

客户报告应由 `review_local.py` 将 RAG 片段自然化。如果出现生硬教材定义、英文残片或“功能医学知识库（仅供参考）”字样，应：

1. 在 `rag_safety.py` 增加拒绝规则，阻止该片段进入报告。
2. 或在 `review_local.py` / 前端预览中增加自然化规则。
3. 重新运行安全测试和固定病例对比。
