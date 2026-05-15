from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock

from app.domain.models import (
    AuditLog,
    CaseRecord,
    ClinicianRule,
    DoctorAccount,
    KnowledgeManifestEntry,
    KnowledgeStatement,
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
                """
            )
            self._ensure_column(connection, "cases", "workspace_scope", "TEXT NOT NULL DEFAULT 'public'")
            self._ensure_column(connection, "cases", "owner_doctor_id", "TEXT")
            self._ensure_column(connection, "clinician_rules", "scope", "TEXT NOT NULL DEFAULT 'public'")
            self._ensure_column(connection, "clinician_rules", "owner_doctor_id", "TEXT")
            self._ensure_column(connection, "clinician_rules", "created_by_doctor_id", "TEXT")

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
        with self._lock, closing(self._connect()) as connection, connection:
            connection.executemany(
                "INSERT OR REPLACE INTO knowledge (statement_id, review_status, payload) VALUES (?, ?, ?)",
                [
                    (item.statement_id, item.review_status.value, item.model_dump_json())
                    for item in knowledge
                ],
            )
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
        return [CaseRecord.model_validate_json(row["payload"]) for row in rows]

    def get_case(self, case_id: str) -> CaseRecord | None:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
        return CaseRecord.model_validate_json(row["payload"]) if row else None

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
