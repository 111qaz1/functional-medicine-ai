from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.settings import AppSettings, load_settings
from app.core.llm_rate_limiter import LLMRateLimiter
from app.core.llm_request_control import LLMRequestController
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
from app.services.case_analysis import CaseAnalysisService, OpenAICompatibleCaseAnalysisProvider
from app.services.document_intake import DocumentIntakeService
from app.services.indicator_extraction import CaseIndicatorService
from app.services.ingestion import KnowledgeIngestionService
from app.services.parsing import DocumentParsingService, LabNormalizationService
from app.services.finding_standardization import FindingStandardizationService
from app.services.pdf_export import PdfReportExporter
from app.services.prescription_advice import PrescriptionAdviceService
from app.services.questionnaire_import import QuestionnaireImportService
from app.services.recommendation_local import RecommendationService
from app.services.review_local import ReviewService
from app.services.semantic_support import SemanticSupportService


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


def apply_product_catalog_migrations(repository: LocalRepository, products: list[ProductRule]) -> None:
    """Apply explicit SKU identity corrections without overwriting unrelated clinician edits."""
    products_by_id = {product.sku_id: product for product in products}
    sku_migrations = {
        "sku_liposomal_vitamin_c_300": "sku_liposomal_vitamin_c_500",
        "sku_probiotics": "sku_probiotic_complex",
    }
    for legacy_sku_id, canonical_sku_id in sku_migrations.items():
        canonical_product = products_by_id.get(canonical_sku_id)
        if not canonical_product:
            raise ValueError(
                f"Product migration {legacy_sku_id!r} references unknown canonical SKU {canonical_sku_id!r}"
            )
        repository.save_product(canonical_product)
        repository.migrate_product_sku(legacy_sku_id, canonical_sku_id)


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


def build_llm_provider(
    settings: AppSettings,
    request_controller: LLMRequestController | None = None,
):
    local_fallback = GroundedDraftComposer()
    if not settings.llm_draft_composer_enabled:
        return local_fallback, "local-structured-v1", "local-report-v5-priority-referral"
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return local_fallback, "local-structured-v1", "local-report-v5-priority-referral"

    remote_provider = OpenAICompatibleGroundedComposer(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        api_style=settings.llm_api_style,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        fallback=local_fallback,
        request_controller=request_controller,
    )
    return remote_provider, f"remote:{settings.llm_model}", "grounded-remote-report-v5-priority-referral"


def build_follow_up_provider(
    settings: AppSettings,
    request_controller: LLMRequestController | None = None,
):
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    return OpenAICompatibleGroundedComposer(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        api_style=settings.llm_api_style,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=min(settings.llm_temperature, 0.1),
        fallback=GroundedDraftComposer(),
        request_controller=request_controller,
    )


def build_rag_fusion_provider(
    settings: AppSettings,
    request_controller: LLMRequestController | None = None,
):
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
        request_controller=request_controller,
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
    document_intake_service: DocumentIntakeService
    case_analysis_service: CaseAnalysisService
    indicator_service: CaseIndicatorService
    parsing_service: DocumentParsingService
    questionnaire_import_service: QuestionnaireImportService
    recommendation_service: RecommendationService
    review_service: ReviewService
    ingestion_service: KnowledgeIngestionService
    auth_service: AuthService
    assistant_rule_service: ClinicianRuleService
    assistant_chat_service: CaseAssistantService
    llm_rate_limiter: LLMRateLimiter
    llm_request_controller: LLMRequestController


def build_container(settings: AppSettings | None = None) -> ApplicationContainer:
    settings = settings or load_settings()
    repository = LocalRepository(settings.sqlite_path)
    repository.mark_active_analyses_interrupted()
    ingestion_service = KnowledgeIngestionService(JsonKnowledgeImporter())
    knowledge = load_knowledge(settings)
    products = load_products(settings)
    manifest_entries = ingestion_service.build_manifest(settings.knowledge_root)
    repository.seed(knowledge=knowledge, products=products, manifest_entries=manifest_entries)
    apply_product_catalog_migrations(repository, products)

    llm_rate_limiter = LLMRateLimiter(
        max_concurrency=settings.llm_max_concurrency,
        requests_per_minute=settings.llm_rpm_soft_limit,
        tokens_per_minute=settings.llm_tpm_soft_limit,
        window_seconds=settings.llm_rate_limit_window_seconds,
        default_completion_reservation=(
            settings.llm_default_completion_reservation
        ),
        history=repository.list_llm_request_usage(limit=1000),
    )
    llm_request_controller = LLMRequestController(
        model=settings.llm_model or "unconfigured",
        rate_limiter=llm_rate_limiter,
        usage_recorder=repository.save_llm_request_usage,
    )

    vector_store = InMemoryVectorStore()
    vector_store.index([item for item in knowledge if item.review_status.value == "reviewed"])
    llm_provider, model_version, prompt_version = build_llm_provider(
        settings,
        llm_request_controller,
    )
    follow_up_provider = build_follow_up_provider(
        settings,
        llm_request_controller,
    )
    rag_fusion_provider = build_rag_fusion_provider(
        settings,
        llm_request_controller,
    )
    rag_retriever = build_rag_retriever(settings)

    auth_service = AuthService(repository)
    case_service = CaseService(repository)
    indicator_service = CaseIndicatorService()
    finding_standardization_service = FindingStandardizationService(
        _data_path(settings, "marker_dictionary.json"),
        _data_path(settings, "clinical_finding_dictionary.json"),
        _data_path(settings, "product_tag_matrix.json"),
    )
    semantic_support_service = SemanticSupportService(
        _data_path(settings, "support_goal_catalog.json")
    )
    parsing_service = DocumentParsingService(
        ocr_provider=DocumentOCRProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            api_style=settings.llm_api_style,
            timeout_seconds=max(settings.llm_timeout_seconds, 90.0),
            request_controller=llm_request_controller,
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
        follow_up_provider=follow_up_provider,
        parsing_service=parsing_service,
        standardization_service=finding_standardization_service,
        rag_retriever=rag_retriever,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    recommendation_service.object_store = LocalObjectStore(settings.upload_dir)
    document_intake_service = DocumentIntakeService(
        max_upload_bytes=settings.max_upload_bytes,
        max_pdf_pages=settings.max_pdf_pages,
    )
    case_analysis_provider = None
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        case_analysis_provider = OpenAICompatibleCaseAnalysisProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            api_style=settings.llm_api_style,
            timeout_seconds=max(settings.llm_timeout_seconds, 90.0),
            thinking_timeout_seconds=max(settings.llm_thinking_timeout_seconds, 90.0),
            temperature=min(settings.llm_temperature, 0.1),
            marker_codes=finding_standardization_service.marker_codes,
            finding_codes=finding_standardization_service.finding_codes,
            system_codes=finding_standardization_service.system_codes,
            support_goal_codes=semantic_support_service.goal_codes,
            support_goal_definitions=semantic_support_service.prompt_catalog(),
            retry_attempts=settings.llm_retry_attempts,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
            usage_recorder=repository.save_llm_request_usage,
            rate_limiter=llm_rate_limiter,
        )
    case_analysis_service = CaseAnalysisService(
        repository=repository,
        case_service=case_service,
        recommendation_service=recommendation_service,
        provider=case_analysis_provider,
        model_version=settings.llm_model or "unconfigured",
        prompt_version="case-analysis-v11-deterministic-document-family",
        standardization_service=finding_standardization_service,
        semantic_support_service=semantic_support_service,
        questionnaire_import_service=questionnaire_import_service,
        worker_count=settings.analysis_worker_count,
        document_worker_count=settings.case_document_worker_count,
    )
    review_service = ReviewService(
        repository,
        case_service,
        indicator_service,
        PdfReportExporter(settings.report_export_dir),
        rag_fusion_provider=rag_fusion_provider,
        prescription_advice_service=PrescriptionAdviceService(
            settings,
            request_controller=llm_request_controller,
        ),
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
        request_controller=llm_request_controller,
    )

    return ApplicationContainer(
        settings=settings,
        repository=repository,
        case_service=case_service,
        document_intake_service=document_intake_service,
        case_analysis_service=case_analysis_service,
        indicator_service=indicator_service,
        parsing_service=parsing_service,
        questionnaire_import_service=questionnaire_import_service,
        recommendation_service=recommendation_service,
        review_service=review_service,
        ingestion_service=ingestion_service,
        auth_service=auth_service,
        assistant_rule_service=assistant_rule_service,
        assistant_chat_service=assistant_chat_service,
        llm_rate_limiter=llm_rate_limiter,
        llm_request_controller=llm_request_controller,
    )
