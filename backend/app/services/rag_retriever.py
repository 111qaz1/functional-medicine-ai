from __future__ import annotations

import json
import math
import os
import pickle
import re
import importlib
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Any, Iterable

import numpy as np


DEFAULT_MODEL_NAME = "BAAI/bge-m3"
DEFAULT_INDEX_DIR = Path(__file__).resolve().parents[1] / "data" / "rag_index"
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")
QUERY_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "代谢": ("代谢", "血糖", "胰岛素", "肥胖", "体重", "血脂", "metabolic", "insulin", "glucose"),
    "甲状腺": ("甲状腺", "甲减", "甲亢", "桥本", "tsh", "t3", "t4", "thyroid", "hashimoto"),
    "肠道": ("肠道", "肠漏", "菌群", "消化", "胃肠", "gut", "intestinal", "microbiome"),
    "炎症": ("炎症", "抗炎", "crp", "氧化应激", "inflammation", "oxidative"),
    "睡眠/疲劳": ("睡眠", "疲劳", "压力", "hpa", "皮质醇", "sleep", "fatigue", "stress"),
    "免疫": ("免疫", "抗体", "过敏", "immune", "allergy"),
    "解毒": ("解毒", "毒素", "重金属", "肝", "detox", "toxin"),
    "激素": ("激素", "雌激素", "孕酮", "睾酮", "hormone", "estrogen"),
    "营养": ("营养", "饮食", "膳食", "维生素", "矿物质", "nutrition", "diet"),
    "生活方式": ("生活方式", "运动", "冥想", "作息", "lifestyle", "exercise"),
}


@dataclass(frozen=True)
class RagHit:
    chunk_id: str
    text: str
    score: float
    source_kind: str
    topic_tags: list[str]
    metadata: dict[str, Any]
    dense_score: float = 0.0
    sparse_score: float = 0.0
    needs_review: bool = False


class DenseRetrievalUnavailable(RuntimeError):
    """Raised when a production dense index cannot be queried safely."""


def optional_import(module_name: str):
    try:
        return __import__(module_name)
    except Exception:
        return None


def load_sentence_transformer_class():
    package = optional_import("sentence_transformers")
    if package is not None and hasattr(package, "SentenceTransformer"):
        return package.SentenceTransformer
    try:
        module = importlib.import_module("sentence_transformers.SentenceTransformer")
    except Exception:
        return None
    return getattr(module, "SentenceTransformer", None)


def tokenize(text: str) -> list[str]:
    jieba = optional_import("jieba")
    if jieba is not None and hasattr(jieba, "cut"):
        tokens = [token.strip().lower() for token in jieba.cut(text) if token.strip()]
    else:
        tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    return [token for token in tokens if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]


def hashing_embedding(texts: Iterable[str], *, dimension: int = 384) -> np.ndarray:
    if not isinstance(texts, list):
        texts = list(texts)
    vectors = np.zeros((len(texts), dimension), dtype="float32")
    for row_index, text in enumerate(texts):
        tokens = tokenize(text)
        for token in tokens:
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little", signed=False)
            column = value % dimension
            sign = 1.0 if (value >> 63) == 0 else -1.0
            vectors[row_index, column] += sign
        norm = float(np.linalg.norm(vectors[row_index]))
        if norm > 0:
            vectors[row_index] /= norm
    return vectors


class DenseEncoder:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.backend = manifest.get("embedding_backend", "sentence_transformers")
        self.dimension = int(manifest.get("dimension") or 384)
        self.model_name = manifest.get("model_name") or DEFAULT_MODEL_NAME
        model_path = os.getenv("FM_RAG_MODEL_PATH", "").strip()
        self.model_path = Path(model_path).expanduser().resolve() if model_path else None
        self.local_files_only = os.getenv("FM_RAG_LOCAL_FILES_ONLY", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self._model = None

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.backend == "hashing":
            return hashing_embedding(texts, dimension=self.dimension)

        sentence_transformer_class = load_sentence_transformer_class()
        if sentence_transformer_class is None:
            raise RuntimeError("sentence-transformers is not installed or cannot load SentenceTransformer")
        if self._model is None:
            model_ref = str(self.model_path) if self.model_path else self.model_name
            self._model = sentence_transformer_class(
                model_ref,
                local_files_only=self.local_files_only,
            )
        vectors = self._model.encode(
            texts,
            batch_size=min(32, max(1, len(texts))),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype="float32")

    @property
    def model_ref(self) -> str:
        return str(self.model_path) if self.model_path else self.model_name


class RagRetriever:
    def __init__(
        self,
        index_dir: Path | str = DEFAULT_INDEX_DIR,
        *,
        strict_dense: bool | None = None,
        topic_boost_weight: float = 0.0,
    ) -> None:
        self.index_dir = Path(index_dir)
        self.manifest = self._load_manifest()
        self.metadata = self._load_metadata()
        self.entries: list[dict[str, Any]] = self.metadata["entries"]
        self.tokenized_corpus: list[list[str]] = self.metadata.get("tokenized_corpus") or [
            tokenize(entry["text"]) for entry in self.entries
        ]
        self.encoder = DenseEncoder(self.manifest)
        self.strict_dense = (
            bool(strict_dense)
            if strict_dense is not None
            else self.encoder.backend == "sentence_transformers"
        )
        self.topic_boost_weight = max(0.0, float(topic_boost_weight))
        self._faiss = optional_import("faiss")
        self._index = self._load_faiss_index()
        if self.strict_dense and self.encoder.backend == "sentence_transformers" and self._index is None:
            raise DenseRetrievalUnavailable(
                f"FAISS index is required for sentence-transformers RAG index: {self.index_dir}"
            )
        self._bm25 = self._build_bm25()

    def _load_manifest(self) -> dict[str, Any]:
        manifest_path = self.index_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"RAG manifest not found: {manifest_path}")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _load_metadata(self) -> dict[str, Any]:
        metadata_path = self.index_dir / "metadata.pkl"
        if not metadata_path.exists():
            raise FileNotFoundError(f"RAG metadata not found: {metadata_path}")
        with metadata_path.open("rb") as handle:
            return pickle.load(handle)

    def _load_faiss_index(self):
        if self._faiss is None or not hasattr(self._faiss, "read_index"):
            return None
        index_path = self.index_dir / "index.faiss"
        if not index_path.exists():
            return None
        return self._faiss.read_index(str(index_path))

    def _build_bm25(self):
        rank_bm25 = optional_import("rank_bm25")
        if rank_bm25 is None:
            return None
        return rank_bm25.BM25Okapi(self.tokenized_corpus)

    def hybrid_search(self, query: str, top_k: int = 5) -> list[RagHit]:
        query = (query or "").strip()
        if not query:
            return []
        top_k = max(1, top_k)
        dense_ranked = self._dense_search(query, pool_size=max(top_k * 8, 40))
        sparse_ranked = self._sparse_search(query, pool_size=max(top_k * 8, 40))
        fused = self._rrf_fuse(dense_ranked, sparse_ranked)
        fused = self._apply_query_topic_boost(query, fused)
        dense_scores = dict(dense_ranked)
        sparse_scores = dict(sparse_ranked)
        hits: list[RagHit] = []
        for index, score in fused[:top_k]:
            entry = self.entries[index]
            hits.append(
                RagHit(
                    chunk_id=entry["chunk_id"],
                    text=entry["text"],
                    score=round(score, 6),
                    source_kind=entry["source_kind"],
                    topic_tags=list(entry.get("topic_tags") or []),
                    metadata=dict(entry.get("metadata") or {}),
                    dense_score=round(dense_scores.get(index, 0.0), 6),
                    sparse_score=round(sparse_scores.get(index, 0.0), 6),
                    needs_review=bool(entry.get("needs_review")),
                )
            )
        return hits

    def readiness_check(self, query: str = "甲状腺功能") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dense_backend": self.encoder.backend,
            "model_ref": self.encoder.model_ref,
            "local_files_only": self.encoder.local_files_only,
            "faiss_loaded": self._index is not None,
            "bm25_loaded": self._bm25 is not None,
        }
        if self.encoder.backend == "sentence_transformers":
            try:
                vector = self.encoder.encode([query])
            except Exception as exc:
                payload.update(
                    {
                        "dense_ready": False,
                        "dense_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                return payload
            payload.update(
                {
                    "dense_ready": True,
                    "dense_dimension": int(vector.shape[1]) if len(vector.shape) == 2 else None,
                }
            )
        else:
            payload["dense_ready"] = True
        return payload

    def rank_debug(self, query: str, pool_size: int = 40) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"dense_ranked": [], "sparse_ranked": [], "fused_ranked": []}
        dense_ranked = self._dense_search(query, pool_size=pool_size)
        sparse_ranked = self._sparse_search(query, pool_size=pool_size)
        fused_ranked = self._apply_query_topic_boost(query, self._rrf_fuse(dense_ranked, sparse_ranked))
        return {
            "dense_ranked": dense_ranked,
            "sparse_ranked": sparse_ranked,
            "fused_ranked": fused_ranked,
        }

    def _dense_search(self, query: str, pool_size: int) -> list[tuple[int, float]]:
        try:
            query_vector = self.encoder.encode([query]).astype("float32")
        except Exception as exc:
            if self.strict_dense:
                raise DenseRetrievalUnavailable(
                    f"Dense query encoding failed for {self.encoder.model_name}"
                ) from exc
            return []
        if self._index is not None:
            try:
                scores, indices = self._index.search(query_vector, min(pool_size, len(self.entries)))
            except Exception as exc:
                if self.strict_dense:
                    raise DenseRetrievalUnavailable("FAISS dense search failed") from exc
                return []
            return [
                (int(index), float(score))
                for index, score in zip(indices[0], scores[0], strict=False)
                if int(index) >= 0
            ]

        vectors = self.metadata.get("dense_vectors")
        if vectors is None:
            if self.strict_dense:
                raise DenseRetrievalUnavailable(
                    f"No dense vectors are available for strict RAG index: {self.index_dir}"
                )
            return []
        raw_scores = np.asarray(vectors, dtype="float32") @ query_vector[0]
        best = np.argsort(-raw_scores)[: min(pool_size, len(raw_scores))]
        return [(int(index), float(raw_scores[index])) for index in best]

    def _sparse_search(self, query: str, pool_size: int) -> list[tuple[int, float]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        if self._bm25 is not None and hasattr(self._bm25, "get_scores"):
            scores = self._bm25.get_scores(query_tokens)
            best = np.argsort(-scores)[: min(pool_size, len(scores))]
            return [(int(index), float(scores[index])) for index in best if scores[index] > 0]
        query_set = set(query_tokens)
        scored = []
        for index, tokens in enumerate(self.tokenized_corpus):
            overlap = len(query_set.intersection(tokens))
            if overlap:
                scored.append((index, overlap / math.sqrt(len(tokens) + 1)))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:pool_size]

    def _rrf_fuse(
        self,
        dense_ranked: list[tuple[int, float]],
        sparse_ranked: list[tuple[int, float]],
        *,
        rrf_k: int = 60,
    ) -> list[tuple[int, float]]:
        fused: dict[int, float] = {}
        for weight, ranked in ((0.55, dense_ranked), (0.45, sparse_ranked)):
            for rank, (index, _score) in enumerate(ranked, start=1):
                fused[index] = fused.get(index, 0.0) + weight / (rrf_k + rank)
        return sorted(fused.items(), key=lambda item: item[1], reverse=True)

    def _apply_query_topic_boost(self, query: str, fused: list[tuple[int, float]]) -> list[tuple[int, float]]:
        if self.topic_boost_weight <= 0:
            return fused
        query_lower = query.lower()
        query_topics = [
            topic
            for topic, keywords in QUERY_TOPIC_KEYWORDS.items()
            if any(keyword.lower() in query_lower for keyword in keywords)
        ]
        if not query_topics:
            return fused
        boosted: list[tuple[int, float]] = []
        for index, score in fused:
            entry = self.entries[index]
            entry_topics = set(entry.get("topic_tags") or [])
            topic_matches = len(entry_topics.intersection(query_topics))
            text = entry.get("text", "").lower()
            exact_matches = sum(1 for topic in query_topics if any(keyword.lower() in text for keyword in QUERY_TOPIC_KEYWORDS[topic]))
            boosted_score = score + self.topic_boost_weight * ((0.004 * topic_matches) + (0.001 * min(exact_matches, 3)))
            boosted.append((index, boosted_score))
        return sorted(boosted, key=lambda item: item[1], reverse=True)


def build_search_query(context: Any) -> str:
    if context is None:
        return ""
    if isinstance(context, str):
        return context.strip()
    if isinstance(context, dict):
        parts: list[str] = []
        for key in ("goals", "symptoms", "conditions", "markers", "abnormal_markers", "lifestyle", "summary"):
            value = context.get(key)
            if isinstance(value, (list, tuple, set)):
                parts.extend(str(item) for item in value)
            elif value:
                parts.append(str(value))
        return " ".join(part for part in parts if part).strip()
    values = []
    for name in ("goals", "symptoms", "conditions", "markers", "abnormal_markers", "lifestyle_tags"):
        value = getattr(context, name, None)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values).strip()


def hybrid_search(query: str, top_k: int = 5, index_dir: Path | str | None = None) -> list[RagHit]:
    retriever = RagRetriever(index_dir or os.getenv("FM_RAG_INDEX_DIR") or DEFAULT_INDEX_DIR)
    return retriever.hybrid_search(query, top_k=top_k)
