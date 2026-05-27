from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


ROOT = project_root()
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.rag_retriever import DEFAULT_MODEL_NAME, hashing_embedding, tokenize  # noqa: E402


def optional_import(module_name: str):
    try:
        return __import__(module_name)
    except Exception:
        return None


def load_corpus(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            row = json.loads(line)
            if not row.get("chunk_id") or not row.get("text"):
                raise ValueError(f"Invalid corpus row at line {line_number}: chunk_id/text required")
            rows.append(row)
    if not rows:
        raise ValueError(f"Corpus is empty: {path}")
    return rows


def encode_dense(
    texts: list[str],
    *,
    model_name: str,
    batch_size: int,
    backend: str,
    hashing_dimension: int,
) -> tuple[np.ndarray, str, str | None]:
    if backend == "hashing":
        return hashing_embedding(texts, dimension=hashing_dimension), "hashing", None

    sentence_transformers = optional_import("sentence_transformers")
    if sentence_transformers is None:
        if backend == "sentence-transformers":
            raise RuntimeError("sentence-transformers is not installed")
        return hashing_embedding(texts, dimension=hashing_dimension), "hashing", "sentence-transformers not installed"

    try:
        model = sentence_transformers.SentenceTransformer(model_name)
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return np.asarray(vectors, dtype="float32"), "sentence_transformers", None
    except Exception as exc:
        if backend == "sentence-transformers":
            raise
        return hashing_embedding(texts, dimension=hashing_dimension), "hashing", f"{type(exc).__name__}: {exc}"


def build_faiss_index(vectors: np.ndarray):
    faiss = optional_import("faiss")
    if faiss is None or not hasattr(faiss, "IndexFlatIP"):
        raise RuntimeError("faiss-cpu is not installed")
    vectors = np.asarray(vectors, dtype="float32")
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return faiss, index


def write_outputs(
    *,
    output_dir: Path,
    entries: list[dict[str, Any]],
    tokenized_corpus: list[list[str]],
    vectors: np.ndarray,
    model_name: str,
    embedding_backend: str,
    embedding_warning: str | None,
    elapsed_seconds: float,
    corpus_path: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    faiss, index = build_faiss_index(vectors)
    faiss.write_index(index, str(output_dir / "index.faiss"))

    metadata = {
        "entries": entries,
        "tokenized_corpus": tokenized_corpus,
    }
    with (output_dir / "metadata.pkl").open("wb") as handle:
        pickle.dump(metadata, handle, protocol=pickle.HIGHEST_PROTOCOL)

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "corpus_path": "backend/app/data/rag_corpus.jsonl",
        "corpus_sha1": corpus_sha1(corpus_path),
        "document_count": len(entries),
        "dimension": int(vectors.shape[1]),
        "embedding_backend": embedding_backend,
        "embedding_warning": embedding_warning,
        "faiss_index_type": "IndexFlatIP",
        "model_name": model_name,
        "normalized_embeddings": True,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def corpus_sha1(path: Path) -> str:
    import hashlib

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the local RAG FAISS/BM25 index.")
    parser.add_argument("--corpus-path", type=Path, default=ROOT / "backend" / "app" / "data" / "rag_corpus.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "backend" / "app" / "data" / "rag_index")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--embedding-backend",
        choices=("auto", "sentence-transformers", "hashing"),
        default="auto",
        help="Use auto for BAAI/bge-m3 when available, with hashing fallback for offline local validation.",
    )
    parser.add_argument("--hashing-dimension", type=int, default=384)
    args = parser.parse_args()

    started = time.perf_counter()
    entries = load_corpus(args.corpus_path)
    texts = [entry["text"] for entry in entries]
    vectors, backend, warning = encode_dense(
        texts,
        model_name=args.model_name,
        batch_size=args.batch_size,
        backend=args.embedding_backend,
        hashing_dimension=args.hashing_dimension,
    )
    tokenized_corpus = [tokenize(text) for text in texts]
    elapsed = time.perf_counter() - started
    manifest = write_outputs(
        output_dir=args.output_dir,
        entries=entries,
        tokenized_corpus=tokenized_corpus,
        vectors=vectors,
        model_name=args.model_name,
        embedding_backend=backend,
        embedding_warning=warning,
        elapsed_seconds=elapsed,
        corpus_path=args.corpus_path,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
