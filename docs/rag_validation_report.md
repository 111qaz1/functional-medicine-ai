# RAG 接入验证报告

本文档记录当前 RAG 接入的最终验证状态，供交付审查使用。

## 需求对齐

- 现有产品推荐、禁忌规则、医生规则和人工审核仍是最高优先级安全层。
- RAG 仅用于报告描述增强，不参与目录外推荐，不覆盖医生规则。
- 客户可见报告不暴露教材来源、文件路径、页码、chunk id、`RAG` 标签或“功能医学知识库（仅供参考）”。
- 原始 DOCX 教材、真实病例、`.env`、API Key、本地运行数据库、上传文件和模型权重不进入 Git。
- Docker 可加载已构建 FAISS 索引，并通过外部目录加载 `BAAI/bge-m3`，启动时不需要重建 embedding。

## 语料与索引

语料统计：

- `knowledge_statements.json` 输入：`11900`
- reviewed 输入：`1057`
- reference_only 输入：`10843`
- 授权 DOCX 教材：`5`
- 最终 `rag_corpus.jsonl`：`9436`
- 清洗后 reference_only：`7832`
- DOCX chunk：`567`
- reviewed 知识：`1037`

过滤统计：

- 参考文献/书目噪声：`1567`
- 目录/索引噪声：`659`
- 出版信息/页码噪声：`773`
- 疑似病例报告类内容：`66`
- 问卷/表单类内容：`10`
- 过短片段：`77`

索引信息：

- 模型：`BAAI/bge-m3`
- embedding 后端：`sentence_transformers`
- 向量维度：`1024`
- FAISS 类型：`IndexFlatIP`
- 向量已归一化：`true`
- 文档数：`9436`

## Docker 验证

本地 Docker Compose 已验证：

- `http://localhost:8000/health` 返回 `{"status": "ok"}`
- `http://localhost:8000/health/rag` 返回：
  - `loaded=true`
  - `ready=true`
  - `dense_ready=true`
  - `dense_dimension=1024`
  - `faiss_loaded=true`
  - `bm25_loaded=true`
  - `model_ref=/models/bge-m3`

模型目录位于仓库外：

```text
./bge-m3
```

该目录以只读方式挂载到容器内：

```text
/models/bge-m3
```

外部模型目录使用本地 HuggingFace 缓存物化，优先使用硬链接，避免把 2.3 GB 模型权重复制进项目仓库。

## 报告生成验证

固定病例已通过草案生成与审核发布路径验证。最终 `publishable_report` 证明 RAG 内容不是只停留在内部草案区块，而是参与客户可见报告正文的自然化增强。

已验证的客户可见区块：

- 总体健康画像：自然融合甲状腺功能、抗体变化、症状表现、微量营养状态和整体代谢恢复的综合观察句。
- 关键指标：在 TSH/甲状腺相关指标解释中自然融合 HPT 轴、抗体变化和趋势评估。
- 生活方式干预重点：在睡眠、压力、运动等条目中自然融合睡眠节律、压力恢复、久坐和活动建议。
- 复查与跟进建议：自然融合甲状腺功能、抗体变化、症状和睡眠压力状态的趋势观察建议。

本轮新增可选豆包融合层后，融合链路调整为：

```text
检索片段 -> 本地安全过滤 -> 区块准入与本地兜底融合 -> 豆包医学编辑融合 -> 后端二次安全校验 -> 合并回客户报告
```

豆包融合层只允许改写 4 个客户报告区块，不允许接触或修改个性化营养素方案、风险提示、产品禁忌、医生规则和人工审核要求。模型输出不合规时自动回退到本地融合结果。

容器中注入豆包 `LLM_*` 后，`FM_LLM_DRAFT_COMPOSER_ENABLED` 默认保持 `0`，因此原有草案和规则基础报告不由豆包生成。豆包只在规则报告生成后参与 RAG 自然融合。

客户可见报告泄露检查均为阴性：

- `RAG内部审查`
- `RAG总体健康画像`
- `RAG异常指标解释`
- `RAG生活方式干预`
- `RAG复查建议`
- `功能医学知识库`
- `仅供参考`
- `rag_query_failed`
- `DenseRetrievalUnavailable`
- `huggingface.co`
- 教材原始路径、页码或 chunk id

RAG 启用前后对比中，推荐 SKU 列表未因 RAG 发生变化。

## 英文残片事故修复验证

人工测试曾发现异常指标 RAG 命中中混入英文片段，例如英文实验室项目列表和英文断句残片。已新增质量门禁：

- 中文字符过少且英文字符过多的片段会被拒绝。
- 英文实验室列表，如 `potassium, chloride...`，会被拒绝。
- 英文断句残片，如 `marily palmitoleic...`，会被拒绝。
- 前端待审预览也会再次过滤这类片段，避免显示层泄露。

当前案例重新生成草案后验证：

- `potassium, chloride`：未出现
- `palmitoleic`：未出现
- `prostate specific antigen`：未出现
- `RAG内部审查` 中记录了 `non_chinese_fragment`、`english_lab_list_fragment`、`english_continuation_fragment` 等拒绝原因

## 安全测试

Docker 后端环境：

```text
docker compose exec -T backend python -m unittest tests.test_safety_boundary
```

结果：

```text
Ran 5 tests
OK
```

覆盖范围：

- RAG 不会引入目录外产品或营养素推荐。
- 禁忌和红旗规则优先级高于 RAG。
- 医生规则和人工规则不会被 RAG 覆盖。
- 手动发布稿仍会获得自然化 RAG 增强。
- 扩大召回后英文残片不会进入草案或报告。
- 可选豆包融合层只能改写准入区块，不能改写营养素方案。
- 可选豆包融合层输出内部标签或不合规内容时，会回退到本地融合。

补充回归：

```text
docker compose exec -T backend python -m unittest tests.test_recommendation.RecommendationServiceTests.test_keeps_internal_candidate_products_before_manual_parse_review tests.test_recommendation.RecommendationServiceTests.test_approval_rejects_question_mark_corrupted_publishable_summary
```

结果：

```text
Ran 2 tests
OK
```

覆盖范围：

- 人工解析校对未完成但已有候选产品时，内部草案仍保留候选营养素，不会误清空推荐草案。
- 含大量 `?` 的乱码发布稿会被拒绝并回退到服务端安全渲染。

## 已知限制

- `BAAI/bge-m3` 需要约 2.3 GB 外部模型资产，已明确保持在 Git 仓库外。
- Windows HuggingFace cache 快照可能包含容器不可读的符号链接，需要普通文件或硬链接形式的外部模型目录。
- 检索质量仍受语料分块、关键词和医学表达差异影响。新增教材或重建语料后必须运行 `scripts/evaluate_rag_retrieval.py`。
- 当前类 RAGAS 检查是确定性代理指标，不能替代临床专家审查。
- 自然化规则保持保守。若客户报告出现教材原句、英文残片或生硬表达，应优先在 `rag_safety.py` 拒绝该片段，或在 `review_local.py` 增加自然化规则。

## 建议审查文件

- `backend/app/data/rag_corpus.jsonl`
- `backend/app/data/rag_import_report.json`
- `backend/app/data/rag_index/`
- `backend/app/services/rag_retriever.py`
- `backend/app/services/rag_safety.py`
- `backend/app/services/recommendation_local_engine.py`
- `backend/app/services/review_local.py`
- `frontend/components/case-workbench-local.tsx`
- `scripts/build_rag_corpus.py`
- `scripts/index_builder.py`
- `scripts/evaluate_rag_retrieval.py`
- `scripts/evaluate_rag_objective.py`
- `scripts/generate_rag_report_comparison.py`
- `backend/tests/test_rag_retrieval.py`
- `backend/tests/test_safety_boundary.py`
- `backend/tests/test_recommendation.py`
- `compose.yaml`
- `backend/Dockerfile`
- `.env.example`
- `docs/rag_integration.md`
- `docs/rag_validation_report.md`
