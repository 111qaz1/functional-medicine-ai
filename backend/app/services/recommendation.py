from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.models import (
    AuditLog,
    DraftRecommendationItem,
    DraftStatus,
    ProductRule,
    RecommendationDraft,
)
from app.providers.base import DraftCompositionInput, LLMProvider, VectorStoreProvider
from app.repositories.in_memory import InMemoryRepository
from app.services.case_service import CaseService


@dataclass
class RecommendationContext:
    markers_by_code: dict[str, list]
    goals: set[str]
    symptoms: set[str]
    conditions: set[str]
    medications: set[str]
    allergies: set[str]
    pregnancy: bool
    lifestyle_tags: set[str]


class RecommendationService:
    def __init__(
        self,
        *,
        repository: InMemoryRepository,
        case_service: CaseService,
        vector_store: VectorStoreProvider,
        llm_provider: LLMProvider,
        model_version: str = "grounded-deterministic-v1",
        prompt_version: str = "clinical-grounding-v1",
        rule_version: str = "fm-rules-v1",
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.vector_store = vector_store
        self.llm_provider = llm_provider
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.rule_version = rule_version
        self.object_store = None

    def generate(self, case_id: str, requested_by: str) -> RecommendationDraft:
        case = self.case_service.get_case(case_id)
        context = self._build_context(case)
        red_flags = self._evaluate_red_flags(context, case.questionnaire.age if case.questionnaire else None)
        missing_info = self._collect_missing_info(case)
        retrieval_query = self._build_query(case, context)
        knowledge_hits = self.vector_store.search(retrieval_query, top_k=8)
        ranked_products, product_evidence_map, contraindications = self._rank_products(context, knowledge_hits)

        composition = self.llm_provider.compose(
            DraftCompositionInput(
                customer_name=case.customer_name,
                candidate_products=ranked_products,
                knowledge_hits=knowledge_hits,
                product_evidence_map=product_evidence_map,
                red_flags=red_flags,
                contraindications=contraindications,
                missing_info=missing_info,
            )
        )

        recommended_items: list[DraftRecommendationItem] = []
        if not composition.abstain_reason:
            for product in ranked_products[:5]:
                evidence_ids = product_evidence_map.get(product.sku_id, [])[:4]
                recommended_items.append(
                    DraftRecommendationItem(
                        sku_id=product.sku_id,
                        display_name=product.display_name,
                        dosage=product.dosage_rule,
                        reason=self._build_product_reason(product, case.questionnaire.goals if case.questionnaire else [], evidence_ids),
                        evidence_ids=evidence_ids,
                        warnings=list(
                            dict.fromkeys(product.warning_text + product.interaction_rule + product.contraindications)
                        )[:4],
                    )
                )

        draft = RecommendationDraft(
            id=f"draft_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            status=DraftStatus.abstained if composition.abstain_reason else DraftStatus.pending_review,
            recommended_skus=recommended_items,
            lifestyle_actions=composition.lifestyle_actions,
            rationale=composition.rationale,
            evidence_ids=list(dict.fromkeys([e for ids in product_evidence_map.values() for e in ids]))[:12],
            contraindications=list(dict.fromkeys(contraindications)),
            missing_info=missing_info,
            confidence=composition.confidence,
            abstain_reason=composition.abstain_reason,
            manual_review_required=True,
            red_flags=red_flags,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            rule_version=self.rule_version,
        )
        self.repository.save_draft(draft)
        self.case_service.append_draft(case_id, draft.id)
        self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="draft",
                entity_id=draft.id,
                action="draft_generated",
                actor_id=requested_by,
                payload=draft.model_dump(mode="json"),
            )
        )
        return draft

    def _build_context(self, case) -> RecommendationContext:
        markers_by_code: dict[str, list] = {}
        for item in case.extracted_lab_items:
            markers_by_code.setdefault(item.marker_code, []).append(item)

        questionnaire = case.questionnaire
        symptoms = {self._normalize(text) for text in (questionnaire.symptoms if questionnaire else [])}
        conditions = {self._normalize(text) for text in (questionnaire.known_conditions if questionnaire else [])}
        goals = {self._normalize(text) for text in (questionnaire.goals if questionnaire else [])}
        medications = {self._normalize(text) for text in (questionnaire.medications if questionnaire else [])}
        allergies = {self._normalize(text) for text in (questionnaire.allergies if questionnaire else [])}
        lifestyle_tags: set[str] = set()
        if questionnaire:
            if (questionnaire.sleep_hours or 0) < 6 or self._normalize(questionnaire.sleep_quality or "") in {"差", "poor"}:
                lifestyle_tags.add("sleep_recovery")
            if questionnaire.stress_level == "high":
                lifestyle_tags.add("stress_support")
            if self._normalize(questionnaire.exercise_frequency or "") in {"rare", "none", "很少", "无"}:
                lifestyle_tags.add("movement")
            if self._normalize(questionnaire.bowel_habits or "") in {"constipation", "便秘"}:
                lifestyle_tags.add("gut_support")

        return RecommendationContext(
            markers_by_code=markers_by_code,
            goals=goals,
            symptoms=symptoms,
            conditions=conditions,
            medications=medications,
            allergies=allergies,
            pregnancy=bool(questionnaire and questionnaire.pregnant_or_lactating),
            lifestyle_tags=lifestyle_tags,
        )

    def _build_query(self, case, context: RecommendationContext) -> str:
        marker_terms = []
        for items in context.markers_by_code.values():
            for item in items:
                marker_terms.append(item.marker_name)
                marker_terms.append(item.abnormal_flag.value)
        questionnaire = case.questionnaire
        parts = [
            case.customer_name,
            " ".join(marker_terms),
            " ".join(questionnaire.goals if questionnaire else []),
            " ".join(questionnaire.symptoms if questionnaire else []),
            " ".join(questionnaire.known_conditions if questionnaire else []),
        ]
        return " ".join(part for part in parts if part).strip()

    def _evaluate_red_flags(self, context: RecommendationContext, age: int | None) -> list[str]:
        red_flags: list[str] = []
        if age is not None and age < 18:
            red_flags.append("未成年人案例需要人工审核。")
        if context.pregnancy:
            red_flags.append("妊娠或哺乳状态需要人工审核。")
        if any(term in condition for condition in context.conditions for term in ("肿瘤", "肾衰", "肝硬化", "癌", "renal", "cancer")):
            red_flags.append("存在高风险既往病史，需要人工审核。")
        if any(term in medication for medication in context.medications for term in ("华法林", "warfarin", "胰岛素", "insulin")):
            red_flags.append("当前用药与营养素联用存在交互风险。")

        for items in context.markers_by_code.values():
            for item in items:
                value = item.normalized_value or 0
                if item.marker_code == "fasting_glucose" and value >= 7.0:
                    red_flags.append("空腹血糖达到高风险阈值，需优先人工评估。")
                if item.marker_code == "hba1c" and value >= 6.5:
                    red_flags.append("糖化血红蛋白达到高风险阈值，需优先人工评估。")
                if item.marker_code == "hs_crp" and value >= 10:
                    red_flags.append("炎症指标显著升高，需先排查急性风险。")
                if item.marker_code == "alt" and value >= 120:
                    red_flags.append("肝功能指标明显异常，需人工审核。")
        return list(dict.fromkeys(red_flags))

    def _collect_missing_info(self, case) -> list[str]:
        missing: list[str] = []
        questionnaire = case.questionnaire
        if not questionnaire:
            return ["尚未填写问卷。"]
        if not questionnaire.medications:
            missing.append("尚未确认当前用药。")
        if not questionnaire.allergies:
            missing.append("尚未确认过敏史。")
        if not questionnaire.goals:
            missing.append("尚未明确主要健康目标。")
        return missing

    def _rank_products(self, context: RecommendationContext, knowledge_hits):
        ranked: list[tuple[float, ProductRule, list[str]]] = []
        contraindications: list[str] = []
        products = self.repository.list_products()

        for product in products:
            exclusion_matches = [rule for rule in product.exclusions if self._matches_rule(rule, context)]
            if exclusion_matches:
                contraindications.extend(
                    [f"{product.display_name} 被排除：{rule}" for rule in exclusion_matches]
                )
                continue

            score = max(0.05, (100 - product.priority) / 100)
            evidence_ids: list[str] = []
            direct_hits = 0
            for indication in product.indications:
                if self._matches_rule(indication, context):
                    direct_hits += 1
                    score += 0.9

            for hit in knowledge_hits:
                statement = hit.statement
                if product.sku_id in statement.related_skus:
                    score += 0.7 + hit.score
                    evidence_ids.append(statement.statement_id)
                elif self._statement_supports_product(statement, product):
                    score += hit.score * 0.35
                    evidence_ids.append(statement.statement_id)

            if direct_hits == 0 and not evidence_ids:
                continue
            ranked.append((round(score, 3), product, list(dict.fromkeys(evidence_ids))))

        ranked.sort(key=lambda item: item[0], reverse=True)
        product_evidence_map = {product.sku_id: evidence for _, product, evidence in ranked}
        return [product for _, product, _ in ranked[:6]], product_evidence_map, list(dict.fromkeys(contraindications))

    def _matches_rule(self, rule: str, context: RecommendationContext) -> bool:
        parts = rule.split(":")
        kind = parts[0].strip().lower()
        value = self._normalize(parts[1]) if len(parts) >= 2 else ""
        extra = self._normalize(parts[2]) if len(parts) >= 3 else ""

        if kind == "marker":
            observations = context.markers_by_code.get(value, [])
            if not observations:
                return False
            if not extra:
                return True
            return any(item.abnormal_flag.value == extra for item in observations)
        if kind == "goal":
            return value in context.goals
        if kind == "symptom":
            return value in context.symptoms
        if kind == "condition":
            return value in context.conditions
        if kind == "med":
            return value in context.medications
        if kind == "allergy":
            return value in context.allergies
        if kind == "lifestyle":
            return value in context.lifestyle_tags
        if kind == "pregnancy":
            return context.pregnancy
        return False

    def _build_product_reason(self, product: ProductRule, goals: list[str], evidence_ids: list[str]) -> str:
        goals_text = ", ".join(goals[:2]) if goals else "当前症状与指标"
        evidence_text = "、".join(evidence_ids[:3]) if evidence_ids else "产品规则"
        return f"结合 {goals_text} 与内部知识证据 {evidence_text}，该产品进入候选推荐。"

    def _statement_supports_product(self, statement, product: ProductRule) -> bool:
        product_marker_rules = {
            parts[1]
            for parts in (rule.split(":") for rule in product.indications)
            if parts and parts[0] == "marker" and len(parts) >= 2
        }
        product_goal_rules = {
            self._normalize(parts[1])
            for parts in (rule.split(":") for rule in product.indications)
            if parts and parts[0] == "goal" and len(parts) >= 2
        }
        product_symptom_rules = {
            self._normalize(parts[1])
            for parts in (rule.split(":") for rule in product.indications)
            if parts and parts[0] == "symptom" and len(parts) >= 2
        }
        statement_goals = {self._normalize(item) for item in statement.related_goals}
        statement_tags = {self._normalize(item) for item in statement.tags}

        return bool(
            product_marker_rules & set(statement.related_markers)
            or product_goal_rules & statement_goals
            or product_goal_rules & statement_tags
            or product_symptom_rules & statement_tags
        )

    def _normalize(self, value: str) -> str:
        return "".join(value.lower().split())
