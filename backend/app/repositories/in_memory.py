from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.domain.models import (
    AuditLog,
    AnalysisStatus,
    FinalGenerationStatus,
    CaseAnalysis,
    CaseRecord,
    ClinicianRule,
    DoctorAccount,
    KnowledgeManifestEntry,
    KnowledgeStatement,
    LLMRequestUsage,
    ProductRule,
    RecommendationDraft,
    ReviewDecision,
    SessionRecord,
)


class LocalRepository:
    def __init__(self, database_path: Path) -> None:
        self._lock = Lock()
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    workspace_scope TEXT NOT NULL DEFAULT 'public',
                    owner_doctor_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_analyses (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_case_analyses_case_updated
                    ON case_analyses(case_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS document_analysis_cache (
                    cache_key TEXT PRIMARY KEY,
                    owner_scope TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    draft_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_request_usage (
                    id TEXT PRIMARY KEY,
                    request_group_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    case_id TEXT,
                    analysis_id TEXT,
                    file_id TEXT,
                    draft_id TEXT,
                    operation TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_style TEXT NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    cached_tokens INTEGER,
                    total_tokens INTEGER,
                    reserved_tokens INTEGER NOT NULL DEFAULT 0,
                    queue_duration_ms INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_llm_request_usage_started
                    ON llm_request_usage(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_llm_request_usage_analysis_started
                    ON llm_request_usage(analysis_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_llm_request_usage_case_started
                    ON llm_request_usage(case_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge (
                    statement_id TEXT PRIMARY KEY,
                    review_status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS products (
                    sku_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_manifest (
                    entry_id TEXT PRIMARY KEY,
                    review_status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clinician_rules (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'public',
                    owner_doctor_id TEXT,
                    created_by_doctor_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS doctors (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    doctor_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seed_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_column(connection, "cases", "workspace_scope", "TEXT NOT NULL DEFAULT 'public'")
            self._ensure_column(connection, "cases", "owner_doctor_id", "TEXT")
            self._ensure_column(connection, "clinician_rules", "scope", "TEXT NOT NULL DEFAULT 'public'")
            self._ensure_column(connection, "clinician_rules", "owner_doctor_id", "TEXT")
            self._ensure_column(connection, "clinician_rules", "created_by_doctor_id", "TEXT")
            self._ensure_column(
                connection,
                "llm_request_usage",
                "reserved_tokens",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "llm_request_usage",
                "queue_duration_ms",
                "INTEGER NOT NULL DEFAULT 0",
            )

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def seed(
        self,
        *,
        knowledge: list[KnowledgeStatement],
        products: list[ProductRule],
        manifest_entries: list[KnowledgeManifestEntry],
    ) -> None:
        knowledge_fingerprint = self._knowledge_fingerprint(knowledge)
        with self._lock, closing(self._connect()) as connection, connection:
            if self._should_seed_knowledge(connection, knowledge, knowledge_fingerprint):
                connection.executemany(
                    "INSERT OR REPLACE INTO knowledge (statement_id, review_status, payload) VALUES (?, ?, ?)",
                    [
                        (item.statement_id, item.review_status.value, item.model_dump_json())
                        for item in knowledge
                    ],
                )
                self._set_seed_metadata(connection, "knowledge_fingerprint_v1", knowledge_fingerprint)
                self._set_seed_metadata(connection, "knowledge_count_v1", str(len(knowledge)))
            connection.executemany(
                "INSERT OR IGNORE INTO products (sku_id, enabled, payload) VALUES (?, ?, ?)",
                [(item.sku_id, 1 if item.enabled else 0, item.model_dump_json()) for item in products],
            )
            connection.executemany(
                "INSERT OR REPLACE INTO knowledge_manifest (entry_id, review_status, payload) VALUES (?, ?, ?)",
                [
                    (item.entry_id, item.review_status.value, item.model_dump_json())
                    for item in manifest_entries
                ],
            )

    def migrate_product_sku(self, legacy_sku_id: str, canonical_sku_id: str) -> int:
        """Replace a retired SKU in persisted JSON records, then remove its catalog row."""
        if not legacy_sku_id or not canonical_sku_id or legacy_sku_id == canonical_sku_id:
            return 0

        payload_tables = (
            "cases",
            "drafts",
            "case_analyses",
            "document_analysis_cache",
            "review_decisions",
            "audit_logs",
            "knowledge",
            "clinician_rules",
        )
        changed = 0
        with self._lock, closing(self._connect()) as connection, connection:
            for table in payload_tables:
                rows = connection.execute(
                    f"SELECT rowid, payload FROM {table} WHERE instr(payload, ?) > 0",
                    (legacy_sku_id,),
                ).fetchall()
                for row in rows:
                    migrated_payload = str(row["payload"]).replace(legacy_sku_id, canonical_sku_id)
                    connection.execute(
                        f"UPDATE {table} SET payload = ? WHERE rowid = ?",
                        (migrated_payload, row["rowid"]),
                    )
                    changed += 1
            connection.execute("DELETE FROM products WHERE sku_id = ?", (legacy_sku_id,))
        return changed

    def _knowledge_fingerprint(self, knowledge: list[KnowledgeStatement]) -> str:
        digest = hashlib.sha256()
        for item in sorted(knowledge, key=lambda statement: statement.statement_id):
            self._update_knowledge_digest(
                digest,
                statement_id=item.statement_id,
                review_status=item.review_status.value,
                version=item.version,
                normalized_text=item.normalized_text,
            )
        return digest.hexdigest()

    def _knowledge_fingerprint_from_db(self, connection: sqlite3.Connection) -> str | None:
        rows = connection.execute("SELECT payload FROM knowledge ORDER BY statement_id").fetchall()
        digest = hashlib.sha256()
        try:
            payloads = [json.loads(row["payload"]) for row in rows]
        except (TypeError, json.JSONDecodeError):
            return None
        for payload in sorted(payloads, key=lambda item: str(item.get("statement_id") or "")):
            self._update_knowledge_digest(
                digest,
                statement_id=str(payload.get("statement_id") or ""),
                review_status=str(payload.get("review_status") or ""),
                version=str(payload.get("version") or ""),
                normalized_text=str(payload.get("normalized_text") or ""),
            )
        return digest.hexdigest()

    def _update_knowledge_digest(
        self,
        digest: "hashlib._Hash",
        *,
        statement_id: str,
        review_status: str,
        version: str,
        normalized_text: str,
    ) -> None:
        for value in (statement_id, review_status, version, normalized_text):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")

    def _should_seed_knowledge(
        self,
        connection: sqlite3.Connection,
        knowledge: list[KnowledgeStatement],
        fingerprint: str,
    ) -> bool:
        row = connection.execute("SELECT COUNT(*) AS total FROM knowledge").fetchone()
        existing_count = int(row["total"] if row else 0)
        expected_count = len(knowledge)
        stored_fingerprint = self._seed_metadata(connection, "knowledge_fingerprint_v1")
        stored_count = self._seed_metadata(connection, "knowledge_count_v1")
        if (
            existing_count == expected_count
            and stored_fingerprint == fingerprint
            and stored_count == str(expected_count)
        ):
            return False
        if existing_count == expected_count and stored_fingerprint is None:
            existing_fingerprint = self._knowledge_fingerprint_from_db(connection)
            if existing_fingerprint != fingerprint:
                return True
            self._set_seed_metadata(connection, "knowledge_fingerprint_v1", fingerprint)
            self._set_seed_metadata(connection, "knowledge_count_v1", str(expected_count))
            return False
        return True

    def _seed_metadata(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM seed_metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def _set_seed_metadata(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO seed_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )

    def list_cases(
        self,
        *,
        workspace_scope: str | None = None,
        owner_doctor_id: str | None = None,
    ) -> list[CaseRecord]:
        sql = "SELECT payload FROM cases"
        clauses: list[str] = []
        params: list[str] = []
        if workspace_scope:
            clauses.append("workspace_scope = ?")
            params.append(workspace_scope)
        if owner_doctor_id is not None:
            clauses.append("owner_doctor_id = ?")
            params.append(owner_doctor_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._load_case_record(row["payload"]) for row in rows]

    def get_case(self, case_id: str) -> CaseRecord | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
        return self._load_case_record(row["payload"]) if row else None

    @staticmethod
    def _load_case_record(payload: str) -> CaseRecord:
        data = json.loads(payload)
        # Historical records may contain the removed analysis-mode selector.
        # All cases now use the single LLM-primary workflow.
        data.pop("analysis_mode", None)
        return CaseRecord.model_validate(data)

    def save_case(self, record: CaseRecord) -> CaseRecord:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO cases (id, workspace_scope, owner_doctor_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.id,
                    getattr(record.workspace_scope, "value", str(record.workspace_scope)),
                    record.owner_doctor_id,
                    record.model_dump_json(),
                ),
            )
        return record

    def delete_case_bundle(self, case_id: str, draft_ids: list[str]) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM cases WHERE id = ?", (case_id,))
            connection.execute("DELETE FROM audit_logs WHERE entity_id = ?", (case_id,))
            connection.execute("DELETE FROM case_analyses WHERE case_id = ?", (case_id,))
            connection.execute("DELETE FROM llm_request_usage WHERE case_id = ?", (case_id,))
            for draft_id in draft_ids:
                connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
                connection.execute("DELETE FROM review_decisions WHERE draft_id = ?", (draft_id,))
                connection.execute("DELETE FROM audit_logs WHERE entity_id = ?", (draft_id,))

    def get_draft(self, draft_id: str) -> RecommendationDraft | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return RecommendationDraft.model_validate_json(row["payload"]) if row else None

    def save_draft(self, draft: RecommendationDraft) -> RecommendationDraft:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO drafts (id, case_id, payload) VALUES (?, ?, ?)",
                (draft.id, draft.case_id, draft.model_dump_json()),
            )
        return draft

    def list_case_analyses(self, case_id: str) -> list[CaseAnalysis]:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT payload FROM case_analyses WHERE case_id = ? ORDER BY updated_at DESC",
                (case_id,),
            ).fetchall()
        return [CaseAnalysis.model_validate_json(row["payload"]) for row in rows]

    def get_case_analysis(self, analysis_id: str) -> CaseAnalysis | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM case_analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()
        return CaseAnalysis.model_validate_json(row["payload"]) if row else None

    def get_latest_case_analysis(self, case_id: str) -> CaseAnalysis | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM case_analyses WHERE case_id = ? ORDER BY updated_at DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return CaseAnalysis.model_validate_json(row["payload"]) if row else None

    def save_case_analysis(self, analysis: CaseAnalysis) -> CaseAnalysis:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO case_analyses (id, case_id, status, updated_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    analysis.id,
                    analysis.case_id,
                    analysis.status.value,
                    analysis.updated_at.isoformat(),
                    analysis.model_dump_json(),
                ),
            )
        return analysis

    def mark_active_analyses_interrupted(self) -> int:
        active = {
            AnalysisStatus.queued.value,
            AnalysisStatus.preparing.value,
            AnalysisStatus.analyzing_documents.value,
            AnalysisStatus.synthesizing.value,
            AnalysisStatus.validating.value,
        }
        changed = 0
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute("SELECT id, payload FROM case_analyses").fetchall()
            for row in rows:
                analysis = CaseAnalysis.model_validate_json(row["payload"])
                initial_active = analysis.status.value in active
                final_active = analysis.final_generation_status in {
                    FinalGenerationStatus.queued,
                    FinalGenerationStatus.final_synthesizing,
                    FinalGenerationStatus.validating_support_needs,
                    FinalGenerationStatus.mapping_products,
                    FinalGenerationStatus.checking_safety,
                    FinalGenerationStatus.generating_draft,
                }
                if not initial_active and not final_active:
                    continue
                if initial_active:
                    analysis.status = AnalysisStatus.failed
                    analysis.error_code = "interrupted_by_restart"
                    analysis.error_message = "后端服务重启导致分析中断，请手动重试。"
                if final_active:
                    analysis.final_generation_status = FinalGenerationStatus.failed
                    analysis.final_generation_error = "后端服务重启导致草案生成中断，请手动重试。"
                analysis.updated_at = datetime.now(timezone.utc)
                connection.execute(
                    "UPDATE case_analyses SET status = ?, updated_at = ?, payload = ? WHERE id = ?",
                    (
                        analysis.status.value,
                        analysis.updated_at.isoformat(),
                        analysis.model_dump_json(),
                        analysis.id,
                    ),
                )
                changed += 1
        return changed

    def get_document_analysis_cache(self, cache_key: str, owner_scope: str) -> dict | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM document_analysis_cache WHERE cache_key = ? AND owner_scope = ?",
                (cache_key, owner_scope),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_document_analysis_cache(self, cache_key: str, owner_scope: str, payload: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO document_analysis_cache (cache_key, owner_scope, updated_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, owner_scope, now, json.dumps(payload, ensure_ascii=False)),
            )

    def save_review_decision(self, review: ReviewDecision) -> ReviewDecision:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO review_decisions (draft_id, payload) VALUES (?, ?)",
                (review.draft_id, review.model_dump_json()),
            )
        return review

    def get_review_decision(self, draft_id: str) -> ReviewDecision | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload FROM review_decisions WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        return ReviewDecision.model_validate_json(row["payload"]) if row else None

    def add_audit_log(self, audit_log: AuditLog) -> AuditLog:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_logs (id, entity_type, entity_id, action, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_log.id,
                    audit_log.entity_type,
                    audit_log.entity_id,
                    audit_log.action,
                    audit_log.created_at.isoformat(),
                    audit_log.model_dump_json(),
                ),
            )
        return audit_log

    def list_audit_logs(self, entity_id: str) -> list[AuditLog]:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT payload FROM audit_logs WHERE entity_id = ? ORDER BY created_at ASC",
                (entity_id,),
            ).fetchall()
        return [AuditLog.model_validate_json(row["payload"]) for row in rows]

    def save_llm_request_usage(self, usage: LLMRequestUsage) -> LLMRequestUsage:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO llm_request_usage (
                    id, request_group_id, attempt, case_id, analysis_id, file_id,
                    draft_id, operation, schema_name, model, api_style, status,
                    http_status, prompt_tokens, completion_tokens, cached_tokens,
                    total_tokens, reserved_tokens, queue_duration_ms, started_at,
                    completed_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.id,
                    usage.request_group_id,
                    usage.attempt,
                    usage.case_id,
                    usage.analysis_id,
                    usage.file_id,
                    usage.draft_id,
                    usage.operation,
                    usage.schema_name,
                    usage.model,
                    usage.api_style,
                    usage.status,
                    usage.http_status,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.cached_tokens,
                    usage.total_tokens,
                    usage.reserved_tokens,
                    usage.queue_duration_ms,
                    usage.started_at.isoformat(),
                    usage.completed_at.isoformat(),
                    usage.model_dump_json(),
                ),
            )
        return usage

    def list_llm_request_usage(
        self,
        *,
        case_id: str | None = None,
        analysis_id: str | None = None,
        limit: int = 100,
    ) -> list[LLMRequestUsage]:
        clauses: list[str] = []
        params: list[str | int] = []
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        if analysis_id:
            clauses.append("analysis_id = ?")
            params.append(analysis_id)
        sql = "SELECT payload FROM llm_request_usage"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [LLMRequestUsage.model_validate_json(row["payload"]) for row in rows]

    def summarize_llm_request_usage(
        self,
        *,
        since: datetime,
        case_id: str | None = None,
        analysis_id: str | None = None,
    ) -> dict[str, int]:
        clauses = ["started_at >= ?"]
        params: list[str] = [since.isoformat()]
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        if analysis_id:
            clauses.append("analysis_id = ?")
            params.append(analysis_id)
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS request_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
                    SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
                    SUM(COALESCE(cached_tokens, 0)) AS cached_tokens,
                    SUM(COALESCE(total_tokens, 0)) AS total_tokens
                FROM llm_request_usage
                WHERE {" AND ".join(clauses)}
                """,
                tuple(params),
            ).fetchone()
        return {
            key: int(row[key] or 0)
            for key in (
                "request_count",
                "failed_count",
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "total_tokens",
            )
        }

    def list_knowledge(self, *, reviewed_only: bool = False) -> list[KnowledgeStatement]:
        sql = "SELECT payload FROM knowledge"
        params: tuple[str, ...] = ()
        if reviewed_only:
            sql += " WHERE review_status = ?"
            params = ("reviewed",)
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(sql, params).fetchall()
        return [KnowledgeStatement.model_validate_json(row["payload"]) for row in rows]

    def list_products(self, *, enabled_only: bool = True) -> list[ProductRule]:
        sql = "SELECT payload FROM products"
        params: tuple[int, ...] = ()
        if enabled_only:
            sql += " WHERE enabled = ?"
            params = (1,)
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(sql, params).fetchall()
        return [ProductRule.model_validate_json(row["payload"]) for row in rows]

    def get_product(self, sku_id: str) -> ProductRule | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM products WHERE sku_id = ?", (sku_id,)).fetchone()
        return ProductRule.model_validate_json(row["payload"]) if row else None

    def save_product(self, product: ProductRule) -> ProductRule:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO products (sku_id, enabled, payload) VALUES (?, ?, ?)",
                (product.sku_id, 1 if product.enabled else 0, product.model_dump_json()),
            )
        return product

    def delete_product(self, sku_id: str) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM products WHERE sku_id = ?", (sku_id,))

    def list_knowledge_manifest(self) -> list[KnowledgeManifestEntry]:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT payload FROM knowledge_manifest ORDER BY review_status, entry_id"
            ).fetchall()
        return [KnowledgeManifestEntry.model_validate_json(row["payload"]) for row in rows]

    def list_clinician_rules(self, *, enabled_only: bool = False) -> list[ClinicianRule]:
        sql = "SELECT payload FROM clinician_rules"
        params: tuple[int, ...] = ()
        if enabled_only:
            sql += " WHERE enabled = ?"
            params = (1,)
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(sql, params).fetchall()
        return [ClinicianRule.model_validate_json(row["payload"]) for row in rows]

    def get_clinician_rule(self, rule_id: str) -> ClinicianRule | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM clinician_rules WHERE id = ?", (rule_id,)).fetchone()
        return ClinicianRule.model_validate_json(row["payload"]) if row else None

    def save_clinician_rule(self, rule: ClinicianRule) -> ClinicianRule:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO clinician_rules
                    (id, enabled, scope, owner_doctor_id, created_by_doctor_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    1 if rule.enabled else 0,
                    getattr(rule.scope, "value", str(rule.scope)),
                    rule.owner_doctor_id,
                    rule.created_by_doctor_id,
                    rule.model_dump_json(),
                ),
            )
        return rule

    def delete_clinician_rule(self, rule_id: str) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM clinician_rules WHERE id = ?", (rule_id,))

    def count_doctors(self) -> int:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM doctors").fetchone()
        return int(row["total"] if row else 0)

    def list_doctors(self) -> list[DoctorAccount]:
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute("SELECT payload FROM doctors ORDER BY username ASC").fetchall()
        return [DoctorAccount.model_validate_json(row["payload"]) for row in rows]

    def get_doctor(self, doctor_id: str) -> DoctorAccount | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        return DoctorAccount.model_validate_json(row["payload"]) if row else None

    def get_doctor_by_username(self, username: str) -> DoctorAccount | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM doctors WHERE username = ?", (username,)).fetchone()
        return DoctorAccount.model_validate_json(row["payload"]) if row else None

    def save_doctor(self, doctor: DoctorAccount) -> DoctorAccount:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO doctors (id, username, role, enabled, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    doctor.id,
                    doctor.username,
                    getattr(doctor.role, "value", str(doctor.role)),
                    1 if doctor.enabled else 0,
                    doctor.model_dump_json(),
                ),
            )
        return doctor

    def save_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO sessions (id, doctor_id, expires_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.doctor_id,
                    session.expires_at.isoformat(),
                    session.model_dump_json(),
                ),
            )
        return session

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return SessionRecord.model_validate_json(row["payload"]) if row else None

    def delete_session(self, session_id: str) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def delete_expired_sessions(self, now_iso: str) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso,))


InMemoryRepository = LocalRepository
