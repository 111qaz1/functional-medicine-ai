from __future__ import annotations

import uuid

from app.domain.models import AuditLog, DraftStatus, ReviewDecision
from app.repositories.in_memory import InMemoryRepository
from app.services.case_service import CaseService


class ReviewService:
    def __init__(self, repository: InMemoryRepository, case_service: CaseService) -> None:
        self.repository = repository
        self.case_service = case_service

    def approve(self, draft_id: str, *, reviewer_id: str, publishable_summary: str | None, edits: dict[str, str]):
        draft = self.repository.get_draft(draft_id)
        if not draft:
            raise KeyError(f"Draft {draft_id} not found")

        draft.status = DraftStatus.approved
        report = publishable_summary or self._render_report(draft)
        audit_log = self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="draft",
                entity_id=draft_id,
                action="draft_approved",
                actor_id=reviewer_id,
                payload={"edits": edits, "publishable_summary": report},
            )
        )
        review = ReviewDecision(
            draft_id=draft_id,
            reviewer_id=reviewer_id,
            edits=edits,
            final_status=DraftStatus.approved,
            publishable_report=report,
            audit_log_id=audit_log.id,
        )
        self.repository.save_draft(draft)
        self.repository.save_review_decision(review)
        self.case_service.mark_approved(draft.case_id)
        return review

    def _render_report(self, draft) -> str:
        lines = ["# 功能医学营养干预建议", ""]
        if draft.abstain_reason:
            lines.extend(["## 自动生成状态", draft.abstain_reason, ""])
        if draft.recommended_skus:
            lines.append("## 推荐营养素")
            for item in draft.recommended_skus:
                lines.append(f"- {item.display_name}（{item.dosage}）")
                lines.append(f"  理由：{item.reason}")
                if item.warnings:
                    lines.append(f"  注意/禁忌：{'；'.join(item.warnings[:3])}")
            lines.append("")
        if draft.lifestyle_actions:
            lines.append("## 生活方式建议")
            for action in draft.lifestyle_actions:
                lines.append(f"- {action}")
            lines.append("")
        if draft.red_flags:
            lines.append("## 人工审核提示")
            for flag in draft.red_flags:
                lines.append(f"- {flag}")
            lines.append("")
        lines.append(f"## 审计信息\n- 模型版本：{draft.model_version}\n- 规则版本：{draft.rule_version}")
        return "\n".join(lines)
