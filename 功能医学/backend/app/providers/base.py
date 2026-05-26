from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Protocol

from pydantic import BaseModel, Field

from app.domain.models import KnowledgeStatement, ProductRule, SourceSpan


class OCRExtraction(BaseModel):
    text: str
    spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    error_message: str | None = None


class KnowledgeHit(BaseModel):
    statement: KnowledgeStatement
    score: float


class DraftCompositionInput(BaseModel):
    customer_name: str
    analysis_mode: str = "llm_primary"
    case_summary: list[str] = Field(default_factory=list)
    key_lab_highlights: list[str] = Field(default_factory=list)
    candidate_products: list[ProductRule]
    knowledge_hits: list[KnowledgeHit]
    product_evidence_map: dict[str, list[str]]
    red_flags: list[str]
    contraindications: list[str]
    missing_info: list[str]
    reviewed_report_text: str | None = None
    structured_case_context: dict[str, Any] = Field(default_factory=dict)


class DraftCompositionResult(BaseModel):
    rationale: list[str] = Field(default_factory=list)
    lifestyle_actions: list[str] = Field(default_factory=list)
    selected_sku_ids: list[str] = Field(default_factory=list)
    product_reason_overrides: dict[str, str] = Field(default_factory=dict)
    section_overrides: dict[str, list[str]] = Field(default_factory=dict)
    confidence: float = 0.0
    abstain_reason: str | None = None


class OCRProvider(Protocol):
    def extract(self, filename: str, content_type: str, content: bytes) -> OCRExtraction:
        ...


class ObjectStoreProvider(Protocol):
    def save(self, filename: str, content: bytes) -> str:
        ...


class VectorStoreProvider(Protocol):
    def index(self, documents: list[KnowledgeStatement]) -> None:
        ...

    def search(self, query: str, *, top_k: int = 8) -> list[KnowledgeHit]:
        ...


class LLMProvider(Protocol):
    def compose(self, draft_input: DraftCompositionInput) -> DraftCompositionResult:
        ...


class KnowledgeImporter(Protocol):
    def load(self, path: Path) -> list[KnowledgeStatement]:
        ...
