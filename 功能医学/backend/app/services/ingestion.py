from __future__ import annotations

import csv
from hashlib import sha1
from pathlib import Path

from app.domain.models import ExtractStatus, KnowledgeManifestEntry, KnowledgeStatement, ReviewStatus
from app.providers.base import KnowledgeImporter


class KnowledgeIngestionService:
    """Loads reviewed local knowledge and inventories the local material library."""

    def __init__(self, importer: KnowledgeImporter) -> None:
        self.importer = importer

    def import_file(self, path: Path) -> list[KnowledgeStatement]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self.importer.load(path)
        if suffix == ".csv":
            return self._load_csv(path)
        if suffix in {".md", ".txt"}:
            return self._load_markdown(path)
        raise ValueError(f"Unsupported knowledge source: {path}")

    def build_manifest(self, root: Path) -> list[KnowledgeManifestEntry]:
        if not root.exists():
            return []

        entries: list[KnowledgeManifestEntry] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative_path = path.relative_to(root).as_posix()
            source_type = self._source_type(path)
            tags = self._infer_tags(relative_path)
            extract_status = (
                ExtractStatus.not_started
                if source_type == "image"
                else ExtractStatus.partial
            )
            review_status = ReviewStatus.reference_only
            entries.append(
                KnowledgeManifestEntry(
                    entry_id=f"manifest_{sha1(relative_path.encode('utf-8')).hexdigest()[:16]}",
                    relative_path=relative_path,
                    source_type=source_type,
                    topic=path.parent.name if path.parent != root else path.stem,
                    extract_status=extract_status,
                    review_status=review_status,
                    tags=tags,
                )
            )
        return entries

    def _load_csv(self, path: Path) -> list[KnowledgeStatement]:
        rows = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(
                    KnowledgeStatement(
                        statement_id=row["statement_id"],
                        topic=row["topic"],
                        normalized_text=row["normalized_text"],
                        evidence_level=row.get("evidence_level", "expert_consensus"),
                        source_doc_id=row.get("source_doc_id", path.name),
                        reviewed_by=row.get("reviewed_by", "pending-review"),
                        version=row.get("version", "draft"),
                        tags=self._split_csv_value(row.get("tags")),
                        related_markers=self._split_csv_value(row.get("related_markers")),
                        related_goals=self._split_csv_value(row.get("related_goals")),
                        related_skus=self._split_csv_value(row.get("related_skus")),
                        lifestyle_actions=self._split_csv_value(row.get("lifestyle_actions")),
                        contraindications=self._split_csv_value(row.get("contraindications")),
                    )
                )
        return rows

    def _load_markdown(self, path: Path) -> list[KnowledgeStatement]:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        items: list[KnowledgeStatement] = []
        for index, line in enumerate(lines, start=1):
            items.append(
                KnowledgeStatement(
                    statement_id=f"{path.stem}-{index:03d}",
                    topic=path.stem,
                    normalized_text=line,
                    evidence_level="expert_consensus",
                    source_doc_id=path.name,
                    reviewed_by="pending-review",
                    version="draft",
                )
            )
        return items

    def _split_csv_value(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.split("|") if item.strip()]

    def _source_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}:
            return "image"
        if suffix in {".pdf"}:
            return "pdf"
        if suffix in {".doc", ".docx"}:
            return "document"
        return "text"

    def _infer_tags(self, relative_path: str) -> list[str]:
        parts = [part for part in Path(relative_path).parts[:-1] if part]
        tags = []
        for part in parts:
            cleaned = part.replace("_", " ").replace("-", " ").strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
        return tags[:6]
