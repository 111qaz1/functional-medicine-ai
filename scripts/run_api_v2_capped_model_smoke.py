from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
import uvicorn
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the capped model smoke test")
    return value


class CappedHTTPClient:
    """Forward model requests while enforcing a process-wide hard request cap."""

    def __init__(self, *, upstream_base_url: str, limit: int, count_file: Path) -> None:
        self._upstream = urlparse(upstream_base_url)
        self._limit = limit
        self._count_file = count_file
        self._count = 0
        self._lock = threading.Lock()
        self._client = httpx.Client(follow_redirects=False, trust_env=False)
        self._write_count()

    def post(self, url: str, **kwargs) -> httpx.Response:
        target = urlparse(str(url))
        if (target.scheme, target.netloc) != (self._upstream.scheme, self._upstream.netloc):
            raise RuntimeError("Refused a model request outside the configured upstream")
        with self._lock:
            if self._count >= self._limit:
                raise RuntimeError("Model request limit reached")
            self._count += 1
            current = self._count
            self._write_count()
        print(f"MODEL_REQUEST {current}/{self._limit}", flush=True)
        return self._client.post(url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def _write_count(self) -> None:
        self._count_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._count_file.with_suffix(self._count_file.suffix + ".tmp")
        temporary.write_text(str(self._count), encoding="ascii")
        os.replace(temporary, self._count_file)


def main() -> None:
    env_path = Path(_required_env("FM_MODEL_ENV_FILE")).resolve()
    runtime_root = Path(_required_env("FM_MODEL_SMOKE_RUNTIME_ROOT")).resolve()
    data_dir = Path(_required_env("FM_MODEL_SMOKE_DATA_DIR")).resolve()
    trust_secret = _required_env("FM_MODEL_SMOKE_TRUST_SECRET")
    if not env_path.is_file():
        raise RuntimeError("FM_MODEL_ENV_FILE does not point to a file")
    if not data_dir.is_dir():
        raise RuntimeError("FM_MODEL_SMOKE_DATA_DIR does not point to a directory")
    load_dotenv(env_path, override=True)

    # The live smoke test uses the remote case-analysis provider only. These
    # overrides make the approved call budget deterministic and avoid RAG or
    # remote draft-composer calls expanding the external payload or quota.
    os.environ["FM_LLM_RETRY_ATTEMPTS"] = "0"
    os.environ["FM_LLM_DRAFT_COMPOSER_ENABLED"] = "0"
    os.environ["FM_RAG_ENABLED"] = "0"
    os.environ["FM_RAG_LLM_FUSION_ENABLED"] = "0"
    os.environ["FM_ANALYSIS_WORKERS"] = "1"
    os.environ["FM_CASE_DOCUMENT_WORKERS"] = "1"
    os.environ["FM_PROJECT_ROOT"] = str(runtime_root / "project")
    os.environ["FM_DATA_DIR"] = str(data_dir)
    os.environ["FM_RUNTIME_DIR"] = str(runtime_root)
    os.environ["FM_UPLOAD_DIR"] = str(runtime_root / "uploads")
    os.environ["FM_REPORT_EXPORT_DIR"] = str(runtime_root / "reports")
    os.environ["FM_SQLITE_PATH"] = str(runtime_root / "model-e2e.sqlite3")
    os.environ["FM_KNOWLEDGE_ROOT"] = str(runtime_root / "knowledge")
    os.environ["FM_REPORT_REFERENCE_PATH"] = str(runtime_root / "project" / "report-reference.pdf")
    os.environ["FM_EXTERNAL_TRUST_SHARED_SECRET"] = trust_secret

    upstream = _required_env("LLM_BASE_URL").rstrip("/")
    _required_env("LLM_API_KEY")
    model = _required_env("LLM_MODEL")
    limit = int(os.getenv("FM_MODEL_REQUEST_LIMIT", "12"))
    if limit < 1 or limit > 12:
        raise RuntimeError("FM_MODEL_REQUEST_LIMIT must be between 1 and 12")
    count_file = Path(_required_env("FM_MODEL_REQUEST_COUNT_FILE")).resolve()

    from app.main import app

    container = app.state.container
    provider = container.case_analysis_service.provider
    if provider is None:
        raise RuntimeError("The case-analysis model provider is not configured")

    client = CappedHTTPClient(
        upstream_base_url=upstream,
        limit=limit,
        count_file=count_file,
    )
    # Keep every configured remote-model entry point behind the same counter.
    # In particular, follow-up plan generation is a separate provider from the
    # case-analysis provider and must not bypass the approved request budget.
    capped_providers = (
        provider,
        container.parsing_service.ocr_provider,
        container.recommendation_service.llm_provider,
        container.recommendation_service.follow_up_provider,
        container.review_service.rag_fusion_provider,
    )
    for capped_provider in capped_providers:
        if capped_provider is not None and hasattr(capped_provider, "http_client"):
            capped_provider.http_client = client

    host = os.getenv("FM_MODEL_SMOKE_HOST", "127.0.0.1")
    port = int(os.getenv("FM_MODEL_SMOKE_PORT", "18120"))
    print(
        f"MODEL_SMOKE_SERVER_READY model={model} request_limit={limit} host={host} port={port}",
        flush=True,
    )
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        client.close()


if __name__ == "__main__":
    main()
