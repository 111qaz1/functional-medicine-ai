from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.settings import AppSettings, load_settings
from app.domain.models import KnowledgeStatement, ProductRule
from app.providers.local import (
    DocumentOCRProvider,
    GroundedDraftComposer,
    InMemoryVectorStore,
    JsonKnowledgeImporter,
    LocalObjectStore,
)
from app.providers.remote import OpenAICompatibleGroundedComposer, OpenAICompatibleRagReportFusion
from app.repositories.in_memory import LocalRepository
from app.services.assistant_rules import ClinicianRuleService
from app.services.assistant_chat import CaseAssistantService
from app.services.auth import AuthService
from app.services.case_service import CaseService
from app.services.indicator_extraction import CaseIndicatorService
from app.services.ingestion import KnowledgeIngestionService
from app.services.parsing import DocumentParsingService, LabNormalizationService
from app.services.pdf_export import PdfReportExporter
from app.services.questionnaire_import import QuestionnaireImportService
from app.services.recommendation_local import RecommendationService
from app.services.review_local import ReviewService


def _data_path(settings: AppSettings, *parts: str) -> Path:
    return settings.data_dir / Path(*parts)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_products(settings: AppSettings) -> list[ProductRule]:
    payload = _load_json(_data_path(settings, "product_catalog.json"))
    products = [ProductRule.model_validate(item) for item in payload]
    safety_path = _data_path(settings, "product_safety_matrix.json")
    if not safety_path.exists():
        return products
    try:
        safety_payload = _load_json(safety_path)
    except (OSError, json.JSONDecodeError):
        return products

    safety_profiles = {
        str(item.get("sku_id") or "").strip(): item
        for item in safety_payload.get("products", [])
        if str(item.get("sku_id") or "").strip()
    }

    def merged(existing: list[str], additions) -> list[str]:
        if not isinstance(additions, list):
            additions = []
        return list(dict.fromkeys([*existing, *(str(item).strip() for item in additions if str(item).strip())]))

    enriched: list[ProductRule] = []
    for product in products:
        profile = safety_profiles.get(product.sku_id)
        if not profile:
            enriched.append(product)
            continue
        enriched.append(
            product.model_copy(
                update={
                    "contraindications": merged(product.contraindications, profile.get("contraindications")),
                    "warning_text": merged(product.warning_text, profile.get("cautions")),
                    "interaction_rule": merged(product.interaction_rule, profile.get("interaction_warnings")),
                    "exclusions": merged(product.exclusions, profile.get("exclusion_rules")),
                }
            )
        )
    return enriched


def load_knowledge(settings: AppSettings) -> list[KnowledgeStatement]:
    knowledge_paths = [
        _data_path(settings, "knowledge_statements.json"),
        *sorted(settings.data_dir.glob("knowledge_statements_*.json")),
    ]
    statements: dict[str, KnowledgeStatement] = {}
    for path in knowledge_paths:
        if not path.exists():
            continue
        payload = _load_json(path)
        for item in payload:
            statement = KnowledgeStatement.model_validate(item)
            if statement.statement_id in statements:
                raise ValueError(f"Duplicate knowledge statement_id {statement.statement_id!r} in {path}")
            statements[statement.statement_id] = statement
    return list(statements.values())


def build_llm_provider(settings: AppSettings):
    local_fallback = GroundedDraftComposer()
    if not settings.llm_draft_composer_enabled:
        return local_fallback, "local-structured-v1", "local-report-v1"
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return local_fallback, "local-structured-v1", "local-report-v1"

    remote_provider = OpenAICompatibleGroundedComposer(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        api_style=settings.llm_api_style,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        fallback=local_fallback,
    )
    return remote_provider, f"remote:{settings.llm_model}", "grounded-remote-report-v1"


def build_rag_fusion_provider(settings: AppSettings):
    if not settings.rag_llm_fusion_enabled:
        return None
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    return OpenAICompatibleRagReportFusion(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        api_style=settings.llm_api_style,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=min(settings.llm_temperature, 0.2),
    )


def build_rag_retriever(settings: AppSettings):
    if not settings.rag_enabled or not settings.rag_index_dir:
        return None
    if not (settings.rag_index_dir / "manifest.json").exists():
        return None
    try:
        from app.services.rag_retriever import RagRetriever

        return RagRetriever(settings.rag_index_dir)
    except Exception:
        return None


@dataclass
class ApplicationContainer:
    settings: AppSettings
    repository: LocalRepository
    case_service: CaseService
    indicator_service: CaseIndicatorService
    parsing_service: DocumentParsingService
    questionnaire_import_service: QuestionnaireImportService
    recommendation_service: RecommendationService
    review_service: ReviewService
    ingestion_service: KnowledgeIngestionService
    auth_service: AuthService
    assistant_rule_service: ClinicianRuleService
    assistant_chat_service: CaseAssistantService


def build_container(settings: AppSettings | None = None) -> ApplicationContainer:
    settings = settings or load_settings()
    repository = LocalRepository(settings.sqlite_path)
    ingestion_service = KnowledgeIngestionService(JsonKnowledgeImporter())
    knowledge = load_knowledge(settings)
    products = load_products(settings)
    manifest_entries = ingestion_service.build_manifest(settings.knowledge_root)
    repository.seed(knowledge=knowledge, products=products, manifest_entries=manifest_entries)

    vector_store = InMemoryVectorStore()
    vector_store.index(repository.list_knowledge(reviewed_only=True))
    llm_provider, model_version, prompt_version = build_llm_provider(settings)
    rag_fusion_provider = build_rag_fusion_provider(settings)
    rag_retriever = build_rag_retriever(settings)

    auth_service = AuthService(repository)
    case_service = CaseService(repository)
    indicator_service = CaseIndicatorService()
    parsing_service = DocumentParsingService(
        ocr_provider=DocumentOCRProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            api_style=settings.llm_api_style,
            timeout_seconds=max(settings.llm_timeout_seconds, 90.0),
        ),
        normalization_service=LabNormalizationService(_data_path(settings, "marker_dictionary.json")),
    )
    questionnaire_import_service = QuestionnaireImportService()
    recommendation_service = RecommendationService(
        repository=repository,
        case_service=case_service,
        indicator_service=indicator_service,
        vector_store=vector_store,
        llm_provider=llm_provider,
        parsing_service=parsing_service,
        rag_retriever=rag_retriever,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    recommendation_service.object_store = LocalObjectStore(settings.upload_dir)
    review_service = ReviewService(
        repository,
        case_service,
        indicator_service,
        PdfReportExporter(settings.report_export_dir),
        rag_fusion_provider=rag_fusion_provider,
    )
    assistant_rule_service = ClinicianRuleService(
        repository=repository,
        case_service=case_service,
        recommendation_service=recommendation_service,
    )
    assistant_chat_service = CaseAssistantService(
        settings=settings,
        repository=repository,
        case_service=case_service,
        indicator_service=indicator_service,
        assistant_rule_service=assistant_rule_service,
    )

    return ApplicationContainer(
        settings=settings,
        repository=repository,
        case_service=case_service,
        indicator_service=indicator_service,
        parsing_service=parsing_service,
        questionnaire_import_service=questionnaire_import_service,
        recommendation_service=recommendation_service,
        review_service=review_service,
        ingestion_service=ingestion_service,
        auth_service=auth_service,
        assistant_rule_service=assistant_rule_service,
        assistant_chat_service=assistant_chat_service,
    )
