from __future__ import annotations

import json
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from app.domain.models import KnowledgeStatement, SourceSpan
from app.providers.base import (
    DraftCompositionInput,
    DraftCompositionResult,
    KnowledgeHit,
    OCRExtraction,
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _tokenize(value: str) -> set[str]:
    tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", value.lower()))
    return {token for token in tokens if len(token) > 1}


class DemoOCRProvider:
    """Developer-friendly parser with docx support and text-first fallbacks."""

    def extract(self, filename: str, content_type: str, content: bytes) -> OCRExtraction:
        suffix = Path(filename).suffix.lower()
        text = ""

        if suffix == ".docx":
            text = self._extract_docx_text(content)
        elif content_type.startswith("text/") or suffix in {".txt", ".md", ".csv"}:
            text = content.decode("utf-8", errors="ignore")
        else:
            text = self._extract_visible_text(content)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        spans = [
            SourceSpan(file_name=filename, page=1, line_number=index + 1, snippet=line)
            for index, line in enumerate(lines)
        ]

        confidence = 0.88 if text else 0.15
        return OCRExtraction(text="\n".join(lines), spans=spans, confidence=confidence)

    def _extract_docx_text(self, content: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        except Exception:
            return self._extract_visible_text(content)

        cleaned = re.sub(r"</w:p>", "\n", xml)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        return cleaned

    def _extract_visible_text(self, content: bytes) -> str:
        decoded = content.decode("utf-8", errors="ignore")
        if decoded.strip():
            return decoded

        chunks = re.findall(rb"[A-Za-z0-9_\-\s:/.%()]{8,}", content)
        return "\n".join(chunk.decode("utf-8", errors="ignore") for chunk in chunks)


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, content: bytes) -> str:
        target = self.root / f"{uuid.uuid4().hex}-{Path(filename).name}"
        target.write_bytes(content)
        return str(target)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._documents: list[KnowledgeStatement] = []

    def index(self, documents: list[KnowledgeStatement]) -> None:
        self._documents = list(documents)

    def search(self, query: str, *, top_k: int = 8) -> list[KnowledgeHit]:
        query_tokens = _tokenize(query)
        hits: list[KnowledgeHit] = []

        for statement in self._documents:
            haystack = " ".join(
                [
                    statement.topic,
                    statement.normalized_text,
                    " ".join(statement.tags),
                    " ".join(statement.related_markers),
                    " ".join(statement.related_goals),
                    " ".join(statement.related_skus),
                ]
            )
            doc_tokens = _tokenize(haystack)
            overlap = len(query_tokens & doc_tokens)
            if not overlap:
                continue

            score = overlap / max(len(query_tokens), 1)
            if any(sku in _normalize_text(query) for sku in statement.related_skus):
                score += 0.2
            hits.append(KnowledgeHit(statement=statement, score=round(score, 3)))

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


class GroundedDraftComposer:
    """Deterministic draft composer used until a production LLM is wired in."""

    def compose(self, draft_input: DraftCompositionInput) -> DraftCompositionResult:
        if draft_input.red_flags:
            return DraftCompositionResult(
                rationale=[
                    f"案例触发人工升级规则：{flag}" for flag in draft_input.red_flags
                ],
                lifestyle_actions=[
                    "在人工审核前暂停自动营养素推荐，先确认高风险指标与既往病史。"
                ],
                confidence=0.12,
                abstain_reason="触发红旗风险，系统已切换到严格拒答模式。",
            )

        if not draft_input.knowledge_hits or not draft_input.candidate_products:
            return DraftCompositionResult(
                rationale=["当前知识库证据不足，无法形成受控推荐草案。"],
                lifestyle_actions=["补充更完整的指标、用药、过敏和健康目标后重新生成。"],
                confidence=0.08,
                abstain_reason="知识库缺少足够证据，已转人工复核。",
            )

        rationale = []
        lifestyle_actions: list[str] = []
        top_hits = draft_input.knowledge_hits[:4]
        for hit in top_hits:
            rationale.append(
                f"{hit.statement.topic}：{hit.statement.normalized_text}（证据 {hit.statement.statement_id}）"
            )
            lifestyle_actions.extend(hit.statement.lifestyle_actions)

        lifestyle_actions = list(dict.fromkeys(lifestyle_actions))[:6]
        confidence = min(
            0.94,
            0.55 + len(top_hits) * 0.07 + min(len(draft_input.candidate_products), 3) * 0.04,
        )

        return DraftCompositionResult(
            rationale=rationale,
            lifestyle_actions=lifestyle_actions,
            confidence=round(confidence, 2),
        )


class JsonKnowledgeImporter:
    def load(self, path: Path) -> list[KnowledgeStatement]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [KnowledgeStatement.model_validate(item) for item in payload]
