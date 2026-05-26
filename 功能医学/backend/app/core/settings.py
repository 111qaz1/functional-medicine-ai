from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, set_key, unset_key


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AppSettings:
    project_root: Path
    data_dir: Path
    runtime_dir: Path
    upload_dir: Path
    report_export_dir: Path
    sqlite_path: Path
    knowledge_root: Path
    report_reference_path: Path
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_api_style: str = "auto"
    llm_timeout_seconds: float = 45.0
    llm_temperature: float = 0.1


@dataclass(frozen=True)
class LLMConfig:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    api_style: str = "auto"
    timeout_seconds: float = 45.0
    temperature: float = 0.1


def _resolve_path(env_name: str, default: Path) -> Path:
    value = os.getenv(env_name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def normalize_llm_api_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip("\"'")
    if normalized.lower().startswith("bearer "):
        normalized = normalized[7:].strip()
    return normalized or None


def llm_config_validation_error(config: LLMConfig) -> str | None:
    api_key = normalize_llm_api_key(config.api_key)
    base_url = (config.base_url or "").strip().rstrip("/")
    if api_key and base_url and api_key.rstrip("/") == base_url:
        return "API Key 不能填写 Base URL。请在 API Key 栏粘贴模型服务控制台生成的鉴权 Key。"
    if api_key and api_key.lower().startswith(("http://", "https://")):
        return "API Key 看起来像一个网址，请检查是否把 Base URL 填到了 API Key 栏。"
    return None


def load_settings() -> AppSettings:
    project_root = _resolve_path("FM_PROJECT_ROOT", _project_root())
    load_dotenv(project_root / ".env", override=False)
    data_dir = _resolve_path("FM_DATA_DIR", project_root / "backend" / "app" / "data")
    runtime_dir = _resolve_path("FM_RUNTIME_DIR", project_root / ".runtime")
    upload_dir = _resolve_path("FM_UPLOAD_DIR", runtime_dir / "uploads")
    report_export_dir = _resolve_path("FM_REPORT_EXPORT_DIR", runtime_dir / "reports")
    sqlite_path = _resolve_path("FM_SQLITE_PATH", runtime_dir / "app.sqlite3")
    knowledge_root = _resolve_path("FM_KNOWLEDGE_ROOT", project_root / "knowledge")
    report_reference_path = _resolve_path("FM_REPORT_REFERENCE_PATH", project_root / "report-reference.pdf")
    llm_base_url = os.getenv("LLM_BASE_URL") or None
    llm_api_key = normalize_llm_api_key(os.getenv("LLM_API_KEY"))
    llm_model = os.getenv("LLM_MODEL") or None
    llm_api_style = os.getenv("LLM_API_STYLE", "auto").strip().lower() or "auto"
    llm_timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.1"))

    return AppSettings(
        project_root=project_root,
        data_dir=data_dir,
        runtime_dir=runtime_dir,
        upload_dir=upload_dir,
        report_export_dir=report_export_dir,
        sqlite_path=sqlite_path,
        knowledge_root=knowledge_root,
        report_reference_path=report_reference_path,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_api_style=llm_api_style,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_temperature=llm_temperature,
    )


def llm_config_from_settings(settings: AppSettings) -> LLMConfig:
    return LLMConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        api_style=settings.llm_api_style,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
    )


def save_llm_config(project_root: Path, config: LLMConfig) -> None:
    env_path = project_root / ".env"
    env_path.touch(exist_ok=True)

    value_map = {
        "LLM_BASE_URL": config.base_url,
        "LLM_API_KEY": config.api_key,
        "LLM_MODEL": config.model,
        "LLM_API_STYLE": config.api_style,
        "LLM_TIMEOUT_SECONDS": str(config.timeout_seconds),
        "LLM_TEMPERATURE": str(config.temperature),
    }

    for key, value in value_map.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            unset_key(env_path, key)
            os.environ.pop(key, None)
            continue
        normalized = normalize_llm_api_key(value) if key == "LLM_API_KEY" else (value.strip() if isinstance(value, str) else str(value))
        if not normalized:
            unset_key(env_path, key)
            os.environ.pop(key, None)
            continue
        set_key(env_path, key, normalized)
        os.environ[key] = normalized
