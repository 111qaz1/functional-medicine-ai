from __future__ import annotations

import re
import uuid

from app.domain.models import AuditLog, ClinicianRule, ClinicianRuleAction, DoctorAccount, DoctorRole, RuleScope
from app.repositories.in_memory import LocalRepository
from app.services.case_service import CaseService
from app.services.recommendation_local import RecommendationService


class ClinicianRuleService:
    def __init__(
        self,
        *,
        repository: LocalRepository,
        case_service: CaseService,
        recommendation_service: RecommendationService,
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.recommendation_service = recommendation_service

    def list_rules(
        self,
        *,
        enabled_only: bool = False,
        doctor: DoctorAccount | None = None,
    ) -> list[ClinicianRule]:
        rules = self.repository.list_clinician_rules(enabled_only=enabled_only)
        rules = [rule for rule in rules if self._rule_visible_to_doctor(rule, doctor)]
        return sorted(rules, key=lambda item: item.updated_at, reverse=True)

    def get_rule(self, rule_id: str, *, doctor: DoctorAccount | None = None, require_write: bool = False) -> ClinicianRule:
        rule = self.repository.get_clinician_rule(rule_id)
        if not rule:
            raise KeyError(f"Rule not found: {rule_id}")
        if require_write:
            if not doctor or not self._rule_writable_by_doctor(rule, doctor):
                raise PermissionError("没有权限修改这条规则。")
        elif not self._rule_visible_to_doctor(rule, doctor):
            raise PermissionError("没有权限查看这条规则。")
        return rule

    def create_from_case(
        self,
        *,
        case_id: str,
        author_id: str,
        instruction_text: str,
        scope: RuleScope = RuleScope.public,
        owner_doctor_id: str | None = None,
        created_by_doctor_id: str | None = None,
    ) -> ClinicianRule:
        case = self.case_service.get_case(case_id)
        instruction = instruction_text.strip()
        if not instruction:
            raise ValueError("instruction_text is required")
        if scope == RuleScope.private and not owner_doctor_id:
            raise ValueError("私人规则需要绑定医生账号。")

        context = self.recommendation_service._build_context(case)
        support_profiles = self.recommendation_service._build_support_profiles(context)
        target_sku_ids = self._infer_target_sku_ids(instruction, case_id=case_id)
        if not target_sku_ids:
            raise ValueError("未能从指令里识别到可落地的产品，请至少提到一个现有产品名称。")

        action = self._infer_action(instruction)
        rule = ClinicianRule(
            id=f"rule_{uuid.uuid4().hex[:12]}",
            title=self._build_title(target_sku_ids, action, support_profiles),
            instruction_text=instruction,
            source_case_id=case.id,
            created_by=author_id,
            scope=scope,
            owner_doctor_id=owner_doctor_id if scope == RuleScope.private else None,
            created_by_doctor_id=created_by_doctor_id,
            enabled=True,
            action=action,
            strength=1.0,
            target_sku_ids=target_sku_ids,
            trigger_marker_rules=self._build_trigger_marker_rules(context),
            trigger_support_profiles=[profile.profile_id for profile in support_profiles[:4]],
            trigger_goals=sorted(context.goals)[:4],
            trigger_symptoms=sorted(context.symptoms)[:4],
            trigger_chief_concerns=sorted(context.chief_concerns)[:3],
            trigger_conditions=sorted(context.conditions)[:3],
            notes=self._build_notes(case.customer_name, support_profiles),
        )
        self.repository.save_clinician_rule(rule)
        self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="clinician_rule",
                entity_id=rule.id,
                action="clinician_rule_created",
                actor_id=author_id,
                payload={
                    **rule.model_dump(mode="json"),
                    "scope": scope.value,
                    "owner_doctor_id": owner_doctor_id if scope == RuleScope.private else None,
                    "created_by_doctor_id": created_by_doctor_id,
                },
            )
        )
        return rule

    def update_rule(self, rule_id: str, *, doctor: DoctorAccount | None = None, **updates) -> ClinicianRule:
        current = self.get_rule(rule_id, doctor=doctor, require_write=True)
        updates = {
            key: value
            for key, value in updates.items()
            if not (key in {"scope", "owner_doctor_id", "created_by_doctor_id"} and value is None)
        }
        if updates.get("scope") == RuleScope.private and not updates.get("owner_doctor_id", current.owner_doctor_id):
            raise ValueError("私人规则需要绑定医生账号。")
        next_rule = current.model_copy(update=updates)
        self.repository.save_clinician_rule(next_rule)
        self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="clinician_rule",
                entity_id=next_rule.id,
                action="clinician_rule_updated",
                actor_id=doctor.username if doctor else "clinician-rule-ui",
                payload=next_rule.model_dump(mode="json"),
            )
        )
        return next_rule

    def delete_rule(self, rule_id: str, *, doctor: DoctorAccount | None = None) -> None:
        current = self.get_rule(rule_id, doctor=doctor, require_write=True)
        self.repository.delete_clinician_rule(rule_id)
        self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="clinician_rule",
                entity_id=rule_id,
                action="clinician_rule_deleted",
                actor_id=doctor.username if doctor else "clinician-rule-ui",
                payload={"id": rule_id, "title": current.title},
            )
        )

    def match_rules_for_case(self, case) -> list[ClinicianRule]:
        return self.recommendation_service.list_matched_clinician_rules(case)

    def _infer_target_sku_ids(self, instruction_text: str, *, case_id: str) -> list[str]:
        normalized_instruction = self._normalize(instruction_text)
        products = self.repository.list_products(enabled_only=False)
        by_id = {product.sku_id: product for product in products}
        scored: list[tuple[float, str]] = []

        case = self.case_service.get_case(case_id)
        draft = self.repository.get_draft(case.draft_ids[-1]) if case.draft_ids else None
        preferred_skus = {item.sku_id for item in draft.recommended_skus} if draft else set()

        for product in products:
            score = 0.0
            if product.sku_id and self._normalize(product.sku_id) in normalized_instruction:
                score += 5.0
            if product.display_name and self._normalize(product.display_name) in normalized_instruction:
                score += 4.0

            searchable_parts = [
                product.display_name,
                product.formula_summary,
                " ".join(product.core_ingredients),
                " ".join(product.candidate_use_cases),
            ]
            searchable_tokens = {
                token for token in re.findall(r"[\w\u4e00-\u9fff]+", self._normalize(" ".join(searchable_parts))) if len(token) > 1
            }
            instruction_tokens = {
                token for token in re.findall(r"[\w\u4e00-\u9fff]+", normalized_instruction) if len(token) > 1
            }
            overlap = len(searchable_tokens & instruction_tokens)
            if overlap:
                score += min(overlap, 5) * 0.45
            if product.sku_id in preferred_skus:
                score += 0.3
            if score > 0:
                scored.append((score, product.sku_id))

        scored.sort(key=lambda item: item[0], reverse=True)
        target_sku_ids: list[str] = []
        for _, sku_id in scored[:3]:
            if sku_id in by_id and sku_id not in target_sku_ids:
                target_sku_ids.append(sku_id)
        return target_sku_ids

    def _infer_action(self, instruction_text: str) -> ClinicianRuleAction:
        normalized = self._normalize(instruction_text)
        if any(token in normalized for token in ("不要推荐", "不推荐", "移除", "排除", "避免", "不再加入", "先不要")):
            return ClinicianRuleAction.avoid
        return ClinicianRuleAction.boost

    def _build_trigger_marker_rules(self, context) -> list[str]:
        marker_rules: list[str] = []
        for marker_code, items in context.markers_by_code.items():
            if not items:
                continue
            flags = {getattr(item.abnormal_flag, "value", "unknown") for item in items}
            prioritized_flags = [flag for flag in ("high", "low", "positive", "unknown", "normal") if flag in flags]
            chosen_flag = prioritized_flags[0] if prioritized_flags else "unknown"
            if chosen_flag != "normal":
                marker_rules.append(f"marker:{marker_code}:{chosen_flag}")
        return marker_rules[:6]

    def _build_title(
        self,
        target_sku_ids: list[str],
        action: ClinicianRuleAction,
        support_profiles,
    ) -> str:
        products = [self.repository.get_product(sku_id) for sku_id in target_sku_ids]
        product_names = "、".join(product.display_name for product in products if product) or "医生产品规则"
        if support_profiles:
            return f"{support_profiles[0].title} - {'增加' if action == ClinicianRuleAction.boost else '谨慎处理'} {product_names}"
        return f"{'增加' if action == ClinicianRuleAction.boost else '谨慎处理'} {product_names}"

    def _build_notes(self, customer_name: str, support_profiles) -> str:
        profile_text = "、".join(profile.title for profile in support_profiles[:3]) if support_profiles else "当前病例信号"
        return f"基于 {customer_name} 当前病例沉淀，后续匹配到 {profile_text} 的相似病例时自动参与推荐。"

    def _normalize(self, value: str) -> str:
        return "".join((value or "").lower().split())

    def _rule_visible_to_doctor(self, rule: ClinicianRule, doctor: DoctorAccount | None) -> bool:
        if rule.scope == RuleScope.public:
            return True
        if not doctor:
            return False
        if doctor.role == DoctorRole.admin:
            return True
        return rule.owner_doctor_id == doctor.id

    def _rule_writable_by_doctor(self, rule: ClinicianRule, doctor: DoctorAccount) -> bool:
        if doctor.role == DoctorRole.admin:
            return True
        if rule.scope == RuleScope.public:
            return True
        return rule.owner_doctor_id == doctor.id
