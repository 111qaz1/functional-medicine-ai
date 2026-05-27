from __future__ import annotations

import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.rag_retriever import RagRetriever, hashing_embedding, tokenize


DEFAULT_INDEX_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "rag_index"


class RagRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name) / "rag_index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.entries = [
            self._entry("metabolic", "代谢综合征常与胰岛素抵抗、血糖波动、体重管理和炎症负担相关。", ["代谢", "炎症"]),
            self._entry("thyroid", "甲状腺功能减退需要关注TSH、游离T3、游离T4以及桥本相关抗体。", ["甲状腺"]),
            self._entry("gut", "肠道菌群、肠道通透性和消化功能会影响免疫耐受与营养吸收。", ["肠道", "免疫"]),
            self._entry("inflammation", "慢性炎症可与氧化应激、CRP升高和生活方式因素相互叠加。", ["炎症"]),
            self._entry("sleep", "睡眠不足、慢性疲劳和压力负荷会影响HPA轴与夜间恢复。", ["睡眠/疲劳"]),
        ]
        texts = [entry["text"] for entry in self.entries]
        vectors = hashing_embedding(texts, dimension=128)
        try:
            import faiss
        except Exception:
            faiss = None
        if faiss is not None and hasattr(faiss, "IndexFlatIP"):
            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(np.asarray(vectors, dtype="float32"))
            faiss.write_index(index, str(self.index_dir / "index.faiss"))
        with (self.index_dir / "metadata.pkl").open("wb") as handle:
            pickle.dump(
                {
                    "entries": self.entries,
                    "dense_vectors": vectors,
                    "tokenized_corpus": [tokenize(text) for text in texts],
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        (self.index_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "dimension": 128,
                    "document_count": len(self.entries),
                    "embedding_backend": "hashing",
                    "model_name": "test-hashing",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _entry(self, chunk_id: str, text: str, topic_tags: list[str]) -> dict:
        return {
            "chunk_id": chunk_id,
            "text": text,
            "source_kind": "test_fixture",
            "review_status": "reference_only",
            "topic_tags": topic_tags,
            "needs_review": False,
            "metadata": {"section": chunk_id, "source_title": "unit-test"},
        }

    def test_hybrid_search_covers_core_topics(self) -> None:
        retriever = RagRetriever(self.index_dir)
        cases = [
            ("代谢综合征 胰岛素抵抗 血糖", {"代谢", "血糖", "胰岛素"}),
            ("甲状腺功能减退 饮食建议 TSH", {"甲状腺", "TSH"}),
            ("肠道菌群 肠漏 营养吸收", {"肠道", "菌群"}),
            ("慢性炎症 CRP 氧化应激", {"炎症", "CRP"}),
            ("睡眠不足 疲劳 压力 HPA轴", {"睡眠", "疲劳", "HPA"}),
        ]
        for query, expected_terms in cases:
            with self.subTest(query=query):
                hits = retriever.hybrid_search(query, top_k=3)
                joined = " ".join(hit.text for hit in hits)
                self.assertTrue(any(term in joined for term in expected_terms), joined)

    def test_search_result_does_not_expose_page_or_source_path_contract(self) -> None:
        retriever = RagRetriever(self.index_dir)
        hit = retriever.hybrid_search("甲状腺 TSH", top_k=1)[0]
        serialized = json.dumps(hit.__dict__, ensure_ascii=False)
        self.assertNotIn("C:\\", serialized)
        self.assertNotIn("ISBN", serialized)


class RealRagIndexSmokeTests(unittest.TestCase):
    @unittest.skipUnless((DEFAULT_INDEX_DIR / "manifest.json").exists(), "default RAG index has not been built")
    def test_real_index_dense_retrieval_is_active(self) -> None:
        manifest = json.loads((DEFAULT_INDEX_DIR / "manifest.json").read_text(encoding="utf-8"))
        retriever = RagRetriever(DEFAULT_INDEX_DIR, strict_dense=True)
        dense_ranked = retriever._dense_search("甲状腺 TSH T3 T4", pool_size=5)
        self.assertEqual(manifest.get("embedding_backend"), "sentence_transformers")
        self.assertGreaterEqual(len(dense_ranked), 5)
        self.assertGreater(max(score for _index, score in dense_ranked), 0.0)

    @unittest.skipUnless((DEFAULT_INDEX_DIR / "manifest.json").exists(), "default RAG index has not been built")
    def test_real_index_returns_relevant_terms_for_core_topics(self) -> None:
        retriever = RagRetriever(DEFAULT_INDEX_DIR, strict_dense=True)
        cases = [
            ("代谢综合征 胰岛素抵抗 血糖管理", ("代谢", "胰岛素", "血糖", "metabolic", "insulin")),
            ("甲状腺功能减退 桥本 TSH", ("甲状腺", "TSH", "thyroid", "桥本")),
            ("肠道菌群 肠漏 消化吸收", ("肠道", "菌群", "肠漏", "intestinal", "gut")),
            ("慢性炎症 CRP 氧化应激", ("炎症", "CRP", "氧化应激", "inflammation")),
            ("睡眠不足 慢性疲劳 压力 HPA轴", ("睡眠", "疲劳", "压力", "HPA", "sleep", "fatigue")),
        ]
        passed = 0
        diagnostics: list[str] = []
        for query, expected_terms in cases:
            hits = retriever.hybrid_search(query, top_k=3)
            joined = " ".join(hit.text for hit in hits)
            matched = any(term.lower() in joined.lower() for term in expected_terms)
            diagnostics.append(f"{query}: {[hit.chunk_id for hit in hits]}")
            passed += int(matched)
        self.assertGreaterEqual(passed, 4, "\n".join(diagnostics))


if __name__ == "__main__":
    unittest.main()
