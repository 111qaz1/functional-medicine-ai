from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from app.domain.models import (
    AuditLog,
    ClinicalEvidenceClass,
    ClinicianRule,
    ClinicianRuleAction,
    DraftRecommendationItem,
    DraftStatus,
    ProductRule,
    RecommendationDraft,
    SafetyDecision,
    SafetyRuleAction,
    SourceSpan,
    StructuredSystemFinding,
)
from app.providers.base import LLMProvider, VectorStoreProvider
from app.repositories.in_memory import LocalRepository
from app.services.case_service import CaseService
from app.services.body_systems import (
    AXIS_SYSTEM_MAP,
    SYSTEM_NAMES,
    build_system_summary,
    classify_text_to_system_ids,
    priority_level,
    system_ids_for_axes,
)
from app.services.indicator_extraction import CaseIndicatorService
from app.services.evidence_policy import classify_confirmed_evidence
from app.services.rag_safety import CUSTOMER_RAG_PREFIX, RagSafetyFilter, SafeRagHit


@dataclass
class RecommendationContext:
    markers_by_code: dict[str, list]
    clinical_findings: list
    clinical_findings_by_code: dict[str, list]
    clinical_findings_by_system: dict[str, list]
    support_goal_findings: dict[str, list]
    goals: set[str]
    chief_concerns: set[str]
    symptoms: set[str]
    conditions: set[str]
    family_history: set[str]
    medications: set[str]
    allergies: set[str]
    food_sensitivities: set[str]
    pregnancy: bool
    age: int | None
    lifestyle_tags: set[str]
    msq_system_scores: dict[str, int]
    clinical_summary_text: str
    summary_nutrient_hints: list[str]
    structured_system_findings: list[StructuredSystemFinding] = field(default_factory=list)


@dataclass
class SupportProfile:
    profile_id: str
    title: str
    weight: float
    preferred_categories: tuple[str, ...]
    match_terms: tuple[str, ...]
    query_terms: tuple[str, ...]
    marker_codes: tuple[str, ...]


@dataclass(frozen=True)
class ProductTagProfile:
    sku_id: str
    sequence: str
    product_name: str
    precision_level: str
    primary_axes: tuple[str, ...]
    secondary_axes: tuple[str, ...]
    marker_tags: tuple[str, ...]
    symptom_tags: tuple[str, ...]
    condition_tags: tuple[str, ...]
    goal_tags: tuple[str, ...]
    lifestyle_tags: tuple[str, ...]
    mechanism_tags: tuple[str, ...]
    primary_system_ids: tuple[str, ...]
    secondary_system_ids: tuple[str, ...]


@dataclass(frozen=True)
class SystemPriority:
    system_id: str
    title: str
    body: str
    score: float
    axes: tuple[str, ...]
    priority_level: str
    finding_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnifiedSafetyRule:
    rule_id: str
    sku_ids: tuple[str, ...]
    action: SafetyRuleAction
    all_conditions: tuple[str, ...]
    any_conditions: tuple[str, ...]
    message: str
    source_ref: str | None
    enabled: bool
    version: str


class RecommendationService:
    _ADMIN_METADATA_PREFIXES = (
        "医嘱名",
        "姓名",
        "姓 名",
        "性别",
        "性 别",
        "年龄",
        "年 龄",
        "登记号",
        "采集时间",
        "接收时间",
        "标本类型",
        "标本号",
        "床号",
        "科室",
        "诊断",
        "服务热线",
        "热线",
        "联系电话",
        "电话",
    )
    _PARSE_WARNING_RE = re.compile(r"^\s*疑似未识别指标[:：]\s*(?P<line>.+?)\s*$")

    def __init__(
        self,
        *,
        repository: LocalRepository,
        case_service: CaseService,
        indicator_service: CaseIndicatorService,
        vector_store: VectorStoreProvider,
        llm_provider: LLMProvider,
        parsing_service=None,
        standardization_service=None,
        rag_retriever=None,
        model_version: str = "local-structured-v1",
        prompt_version: str = "local-report-v1",
        rule_version: str = "local-rules-v1",
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.indicator_service = indicator_service
        self.vector_store = vector_store
        self.llm_provider = llm_provider
        self.parsing_service = parsing_service
        self.standardization_service = standardization_service
        self.rag_retriever = rag_retriever
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.rule_version = rule_version
        self.object_store = None
        self.product_dosage_mapping = self._load_product_dosage_mapping()
        self.product_tag_profiles = self._load_product_tag_profiles()
        (
            self.support_goal_version,
            self.support_goals,
            self.product_capabilities,
        ) = self._load_product_capabilities()
        self.product_report_profiles = self._load_product_report_profiles()
        self.product_safety_profiles = self._load_product_safety_profiles()
        self.unified_safety_rules = self._load_unified_safety_rules()
        self._validate_product_marker_codes()

    def _load_product_capabilities(self) -> tuple[str, dict[str, dict], dict[str, dict]]:
        path = Path(__file__).resolve().parents[1] / "data" / "support_goal_catalog.json"
        if not path.exists():
            return "legacy", {}, {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return "legacy", {}, {}
        goals = {
            str(item.get("code")): item
            for item in payload.get("goals", [])
            if isinstance(item, dict) and item.get("code")
        }
        products = {
            str(item.get("sku_id")): item
            for item in payload.get("products", [])
            if isinstance(item, dict) and item.get("sku_id") and item.get("enabled", True)
        }
        return str(payload.get("version") or "legacy"), goals, products

    def _validate_product_marker_codes(self) -> None:
        if not self.standardization_service:
            return
        valid_codes = set(self.standardization_service.marker_codes)
        invalid: list[str] = []
        for product in self._list_products(enabled_only=False):
            for rule in [*product.indications, *product.exclusions]:
                if not rule.startswith("marker:"):
                    continue
                marker_code = rule.split(":", 2)[1]
                if marker_code not in valid_codes:
                    invalid.append(f"{product.sku_id}:{marker_code}")
        for profile in self.product_tag_profiles.values():
            for marker_tag in profile.marker_tags:
                marker_code = marker_tag.split(":", 1)[0]
                if marker_code and marker_code not in valid_codes:
                    invalid.append(f"{profile.sku_id}:{marker_code}")
        if invalid:
            raise ValueError("产品规则引用了不存在的标准指标代码：" + "、".join(sorted(set(invalid))))

    def _load_product_dosage_mapping(self) -> dict[str, dict]:
        """Load the versioned Excel import once when the service starts."""
        mapping_path = Path(__file__).resolve().parents[1] / "data" / "product_dosage_mapping.json"
        if not mapping_path.exists():
            return {}
        try:
            payload = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = payload.get("products", payload) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return {}
        return {
            str(item.get("sku_id")): item
            for item in entries
            if isinstance(item, dict) and item.get("sku_id")
        }

    def _load_product_tag_profiles(self) -> dict[str, ProductTagProfile]:
        matrix_path = Path(__file__).resolve().parents[1] / "data" / "product_tag_matrix.json"
        if not matrix_path.exists():
            return {}
        try:
            payload = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}

        def as_tuple(value) -> tuple[str, ...]:
            if not isinstance(value, list):
                return ()
            return tuple(str(item).strip() for item in value if str(item).strip())

        profiles: dict[str, ProductTagProfile] = {}
        configured_axis_map = payload.get("axis_system_map") if isinstance(payload.get("axis_system_map"), dict) else {}

        def mapped_systems(axes: tuple[str, ...]) -> tuple[str, ...]:
            result: list[str] = []
            for axis in axes:
                configured = configured_axis_map.get(axis)
                if isinstance(configured, list):
                    result.extend(str(item).strip() for item in configured if str(item).strip() in SYSTEM_NAMES)
                else:
                    result.extend(AXIS_SYSTEM_MAP.get(axis, ()))
            return tuple(dict.fromkeys(result))

        for item in payload.get("products", []):
            sku_id = str(item.get("sku_id") or "").strip()
            if not sku_id:
                continue
            primary_axes = as_tuple(item.get("primary_axes"))
            secondary_axes = as_tuple(item.get("secondary_axes"))
            profiles[sku_id] = ProductTagProfile(
                sku_id=sku_id,
                sequence=str(item.get("sequence") or "").strip(),
                product_name=str(item.get("product_name") or "").strip(),
                precision_level=str(item.get("precision_level") or "adjunct").strip(),
                primary_axes=primary_axes,
                secondary_axes=secondary_axes,
                marker_tags=as_tuple(item.get("marker_tags")),
                symptom_tags=as_tuple(item.get("symptom_tags")),
                condition_tags=as_tuple(item.get("condition_tags")),
                goal_tags=as_tuple(item.get("goal_tags")),
                lifestyle_tags=as_tuple(item.get("lifestyle_tags")),
                mechanism_tags=as_tuple(item.get("mechanism_tags")),
                primary_system_ids=mapped_systems(primary_axes),
                secondary_system_ids=mapped_systems(secondary_axes),
            )
        return profiles

    def _load_product_report_profiles(self) -> dict[str, dict[str, str]]:
        catalog_path = Path(__file__).resolve().parents[1] / "data" / "product_report_catalog.json"
        if not catalog_path.exists():
            return {}
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        products = payload.get("products")
        if not isinstance(products, dict):
            return {}
        return {
            str(sku_id): {str(key): str(value or "").strip() for key, value in profile.items()}
            for sku_id, profile in products.items()
            if isinstance(profile, dict)
        }

    def _load_product_safety_profiles(self) -> dict[str, dict[str, tuple[str, ...]]]:
        matrix_path = Path(__file__).resolve().parents[1] / "data" / "product_safety_matrix.json"
        if not matrix_path.exists():
            return {}
        try:
            payload = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}

        def as_tuple(value) -> tuple[str, ...]:
            if not isinstance(value, list):
                return ()
            return tuple(str(item).strip() for item in value if str(item).strip())

        profiles: dict[str, dict[str, tuple[str, ...]]] = {}
        for item in payload.get("products", []):
            sku_id = str(item.get("sku_id") or "").strip()
            if not sku_id:
                continue
            profiles[sku_id] = {
                "contraindications": as_tuple(item.get("contraindications")),
                "cautions": as_tuple(item.get("cautions")),
                "interaction_warnings": as_tuple(item.get("interaction_warnings")),
                "exclusion_rules": as_tuple(item.get("exclusion_rules")),
            }
        return profiles

    def _load_unified_safety_rules(self) -> tuple[UnifiedSafetyRule, ...]:
        matrix_path = Path(__file__).resolve().parents[1] / "data" / "product_safety_matrix.json"
        if not matrix_path.exists():
            return ()
        try:
            payload = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return ()

        rules: list[UnifiedSafetyRule] = []
        for item in payload.get("rules", []):
            if not isinstance(item, dict):
                continue
            try:
                action = SafetyRuleAction(str(item.get("action") or "warn"))
            except ValueError:
                continue

            def tuple_value(key: str) -> tuple[str, ...]:
                value = item.get(key)
                if not isinstance(value, list):
                    return ()
                return tuple(str(entry).strip() for entry in value if str(entry).strip())

            rule_id = str(item.get("rule_id") or "").strip()
            if not rule_id:
                continue
            rules.append(
                UnifiedSafetyRule(
                    rule_id=rule_id,
                    sku_ids=tuple_value("sku_ids"),
                    action=action,
                    all_conditions=tuple_value("all_conditions"),
                    any_conditions=tuple_value("any_conditions"),
                    message=str(item.get("message") or "").strip(),
                    source_ref=str(item.get("source_ref") or "").strip() or None,
                    enabled=bool(item.get("enabled", True)),
                    version=str(item.get("version") or payload.get("version") or "").strip(),
                )
            )
        return tuple(rules)

    def _list_products(self, *, enabled_only: bool = True) -> list[ProductRule]:
        return [
            self._apply_product_safety_profile(product)
            for product in self.repository.list_products(enabled_only=enabled_only)
        ]

    def _apply_product_safety_profile(self, product: ProductRule) -> ProductRule:
        profile = self.product_safety_profiles.get(product.sku_id)
        if not profile:
            return product

        def merged(existing: list[str], additions: tuple[str, ...]) -> list[str]:
            return list(dict.fromkeys([*existing, *additions]))

        return product.model_copy(
            update={
                "contraindications": merged(product.contraindications, profile.get("contraindications", ())),
                "warning_text": merged(product.warning_text, profile.get("cautions", ())),
                "interaction_rule": merged(product.interaction_rule, profile.get("interaction_warnings", ())),
                "exclusions": merged(product.exclusions, profile.get("exclusion_rules", ())),
            }
        )

    def _canonical_product_name(self, product: ProductRule) -> str:
        profile = self.product_report_profiles.get(product.sku_id, {})
        return profile.get("product_name") or product.display_name

    def _evaluate_product_safety(
        self,
        product: ProductRule,
        context: RecommendationContext,
    ) -> list[SafetyDecision]:
        decisions: list[SafetyDecision] = []
        for rule in product.exclusions:
            if not self._matches_rule(rule, context):
                continue
            rule_slug = re.sub(r"[^a-z0-9]+", "_", rule.lower()).strip("_") or "condition"
            decisions.append(
                SafetyDecision(
                    rule_id=f"legacy_exclude_{product.sku_id}_{rule_slug}",
                    sku_id=product.sku_id,
                    action=SafetyRuleAction.exclude,
                    message=f"病例已命中产品禁忌条件：{rule}",
                    source_ref="product_catalog.exclusions",
                )
            )

        for rule in self.unified_safety_rules:
            if not rule.enabled or product.sku_id not in rule.sku_ids:
                continue
            if rule.all_conditions and not all(self._matches_rule(condition, context) for condition in rule.all_conditions):
                continue
            if rule.any_conditions and not any(self._matches_rule(condition, context) for condition in rule.any_conditions):
                continue
            if not rule.all_conditions and not rule.any_conditions:
                continue
            decisions.append(
                SafetyDecision(
                    rule_id=rule.rule_id,
                    sku_id=product.sku_id,
                    action=rule.action,
                    message=rule.message,
                    source_ref=rule.source_ref,
                )
            )

        precedence = {
            SafetyRuleAction.exclude: 0,
            SafetyRuleAction.requires_review: 1,
            SafetyRuleAction.warn: 2,
        }
        unique: dict[tuple[str, SafetyRuleAction], SafetyDecision] = {}
        for decision in decisions:
            unique[(decision.rule_id, decision.action)] = decision
        return sorted(unique.values(), key=lambda item: (precedence[item.action], item.rule_id))

    def evaluate_safety_rules(
        self,
        context: RecommendationContext,
        products: list[ProductRule] | None = None,
    ) -> tuple[dict[str, list[SafetyDecision]], float]:
        started = time.perf_counter()
        evaluated = {
            product.sku_id: self._evaluate_product_safety(product, context)
            for product in (products if products is not None else self._list_products(enabled_only=True))
        }
        return evaluated, time.perf_counter() - started

    def _primary_system_for_product(
        self,
        product: ProductRule,
        priority_findings: list[SystemPriority] | None,
    ) -> str | None:
        profile = self.product_tag_profiles.get(product.sku_id)
        if not profile:
            return None
        ordered_system_ids = [finding.system_id for finding in priority_findings or []]
        for candidates in (profile.primary_system_ids, profile.secondary_system_ids):
            for system_id in ordered_system_ids:
                if system_id in candidates:
                    return system_id
        return next(iter(profile.primary_system_ids or profile.secondary_system_ids), None)

    def _system_priority_rank_for_product(
        self,
        product: ProductRule,
        priority_findings: list[SystemPriority] | None,
    ) -> int | None:
        system_id = self._primary_system_for_product(product, priority_findings)
        if not system_id:
            return None
        for index, finding in enumerate(priority_findings or [], start=1):
            if finding.system_id == system_id:
                return index
        return None

    @staticmethod
    def _matched_finding_signals(evidence_ids: list[str]) -> list[str]:
        return [
            evidence_id.split(":", 1)[1]
            for evidence_id in evidence_ids
            if evidence_id.startswith("finding:")
        ]

    def build_total_advice_items(
        self,
        recommended_items: list[DraftRecommendationItem],
        *,
        structured_system_findings: list[StructuredSystemFinding] | None = None,
        finding_names_by_id: dict[str, str] | None = None,
    ) -> list[str]:
        systems = {item.system_id: item for item in structured_system_findings or []}
        finding_names = finding_names_by_id or {}
        products = {product.sku_id: product for product in self._list_products(enabled_only=False)}
        advice_items: list[str] = []
        for item in recommended_items:
            system = systems.get(item.primary_system_id or "")
            system_name = system.system_name if system else SYSTEM_NAMES.get(item.primary_system_id or "", "相关身体系统")
            matched_names = [finding_names[finding_id] for finding_id in item.matched_finding_ids if finding_id in finding_names]
            evidence = "、".join(list(dict.fromkeys(matched_names))[:2])
            if not evidence:
                evidence = f"已确认的{system_name}异常"
            product = products.get(item.sku_id)
            target = self._product_support_target(product, item.primary_system_id)
            reason = self._fit_total_advice_reason(evidence=evidence, system_name=system_name, target=target)
            if 50 <= self._han_count(reason) <= 80:
                advice_items.append(f"{item.display_name}：{reason}")
        return advice_items

    def _product_support_target(self, product: ProductRule | None, system_id: str | None) -> str:
        profile = self.product_tag_profiles.get(product.sku_id) if product else None
        axis_labels = {
            "gut_bile": "肝胆与脂肪消化支持",
            "gut_microbiome": "菌群结构与肠道生态支持",
            "gut_mucosa": "胃肠黏膜与屏障支持",
            "digestive_enzyme": "消化吸收支持",
            "liver_detox": "肝胆代谢与抗氧化支持",
            "immune": "免疫调节支持",
            "inflammation": "炎症平衡支持",
            "antioxidant": "抗氧化与恢复支持",
            "thyroid_axis": "甲状腺营养与代谢支持",
            "glycemic_balance": "血糖稳定与代谢支持",
            "weight_metabolism": "体重与脂肪代谢支持",
            "nutrition_repletion": "体重过轻与营养恢复支持",
            "vitamin_d_repletion": "维生素D与骨骼免疫支持",
            "cardiovascular": "心血管与循环支持",
            "lipid_balance": "血脂代谢支持",
            "sleep_stress": "睡眠与压力恢复支持",
            "neuro_cognitive": "神经认知支持",
            "energy_mitochondria": "细胞能量与疲劳恢复支持",
            "bone_metabolism": "骨代谢支持",
            "iron_repletion": "铁利用与营养恢复支持",
            "female_hormone": "女性激素节律支持",
            "hormone_axis": "内分泌节律支持",
        }
        for axis in (profile.primary_axes if profile else ()):
            if axis in axis_labels:
                return axis_labels[axis]
        if product and product.candidate_use_cases:
            return f"{product.candidate_use_cases[0]}相关营养支持"
        return f"{SYSTEM_NAMES.get(system_id or '', '整体')}功能支持"

    @staticmethod
    def _han_count(value: str) -> int:
        return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", value or ""))

    def _fit_total_advice_reason(self, *, evidence: str, system_name: str, target: str) -> str:
        candidates = [
            f"针对{evidence}等已确认问题，本阶段用于支持{system_name}的{target}，并结合症状变化、耐受情况及后续复查趋势评估实际支持效果和下一阶段调整方向。",
            f"针对{evidence}等已确认问题，本阶段用于支持{system_name}的{target}，建议结合症状变化、耐受情况和后续复查趋势评估效果，再由医生决定后续调整方向。",
            f"针对{evidence}，本阶段用于支持{system_name}的{target}，首月以稳妥执行和连续观察为主，并结合症状、耐受及复查趋势评估后续调整方向。",
        ]
        for candidate in candidates:
            if 50 <= self._han_count(candidate) <= 80:
                return candidate
        compact_evidence = re.split(r"[、，；]", evidence)[0][:12]
        fallback = (
            f"针对{compact_evidence}等已确认问题，本阶段用于支持{system_name}相关功能与整体恢复，"
            "首月以稳妥执行和连续观察为主，并结合症状变化、耐受情况及复查趋势评估后续调整方向。"
        )
        return fallback

    def generate(self, case_id: str, requested_by: str) -> RecommendationDraft:
        case = self.case_service.get_case(case_id)
        latest_analysis = self.repository.get_latest_case_analysis(case_id)
        support_need_by_id = {
            need.id: need
            for need in (latest_analysis.support_needs if latest_analysis else [])
            if getattr(need.eligibility_status, "value", need.eligibility_status) == "eligible"
        }
        customer_name = self._resolve_customer_name(case)
        analysis_mode = getattr(case.analysis_mode, "value", str(case.analysis_mode))
        context = self._build_context(case)
        support_profiles = self._build_support_profiles(context)
        case_summary = self._build_case_summary(case, customer_name=customer_name)
        key_lab_highlights = self._build_key_lab_highlights(case)
        report_guidance = self._extract_report_guidance(case)
        anti_aging_findings = self._extract_anti_aging_findings(case, context)
        priority_findings = self._prioritized_system_findings(
            context,
            report_guidance=report_guidance,
            anti_aging_findings=anti_aging_findings,
        )
        risk_notices = self._evaluate_risk_notices(context, case.questionnaire.age if case.questionnaire else None)
        missing_info = self._collect_missing_info(case)
        reviewed_knowledge = self.repository.list_knowledge(reviewed_only=True)
        knowledge_hits = []
        if reviewed_knowledge:
            retrieval_query = self._build_query(case, context, support_profiles)
            knowledge_hits = self.vector_store.search(retrieval_query, top_k=12 if analysis_mode == "llm_primary" else 8)

        knowledge_by_id = {item.statement_id: item for item in reviewed_knowledge}
        product_by_id = {item.sku_id: item for item in self._list_products(enabled_only=True)}
        matched_clinician_rules = self.list_matched_clinician_rules(case, context=context, support_profiles=support_profiles)
        clinician_rule_by_id = {item.id: item for item in matched_clinician_rules}

        if analysis_mode == "llm_primary":
            ranked_products, product_evidence_map, contraindications, safety_decisions_by_sku = self._rank_products_for_llm_primary(
                case,
                context,
                knowledge_hits,
                support_profiles,
                matched_clinician_rules,
                priority_findings=priority_findings,
            )
        else:
            ranked_products, product_evidence_map, contraindications, safety_decisions_by_sku = self._rank_products(
                context,
                knowledge_hits,
                support_profiles,
                matched_clinician_rules,
                priority_findings=priority_findings,
            )
        rag_hits, rag_audit = self._retrieve_safe_rag_hits(
            case,
            context=context,
            support_profiles=support_profiles,
            key_lab_highlights=key_lab_highlights,
            report_guidance=report_guidance,
            red_flags=[],
            contraindications=contraindications,
        )
        # Product selection is deliberately local. The second-stage synthesis model
        # receives support-goal definitions only and never sees product names or SKU
        # identifiers; safety and dosage logic therefore cannot be bypassed by model
        # output. Existing local report assembly consumes the deterministic result.
        composition = SimpleNamespace(
            selected_sku_ids=[product.sku_id for product in ranked_products],
            lifestyle_actions=[],
            product_reason_overrides={},
            section_overrides={},
            rationale=["产品候选由版本化能力映射、医生确认的证据及本地安全规则生成。"],
            confidence=0.86 if ranked_products else 0.35,
            abstain_reason=None if ranked_products else "未找到具备有效病例证据的产品候选。",
        )
        lifestyle_actions = self._finalize_lifestyle_actions(composition.lifestyle_actions, knowledge_hits, context)

        recommended_items: list[DraftRecommendationItem] = []
        selected_products = self._select_products_for_output(
            ranked_products,
            composition.selected_sku_ids,
            product_evidence_map,
        )
        ranked_position = {product.sku_id: index for index, product in enumerate(ranked_products)}
        selected_products.sort(
            key=lambda product: (
                self._system_priority_rank_for_product(product, priority_findings) or 999,
                ranked_position.get(product.sku_id, len(ranked_position)),
            )
        )
        effective_abstain_reason = composition.abstain_reason if not ranked_products else None
        if not effective_abstain_reason:
            for product in selected_products:
                all_evidence_ids = product_evidence_map.get(product.sku_id, [])
                evidence_ids = all_evidence_ids[:4]
                matched_signal_ids = self._matched_finding_signals(all_evidence_ids)
                matched_support_need_ids = [
                    finding_id for finding_id in matched_signal_ids if finding_id in support_need_by_id
                ]
                matched_finding_ids = [
                    finding_id for finding_id in matched_signal_ids if finding_id not in support_need_by_id
                ]
                for support_need_id in matched_support_need_ids:
                    for evidence in support_need_by_id[support_need_id].evidence_refs:
                        if evidence.ref.startswith("finding:"):
                            matched_finding_ids.append(evidence.ref.split(":", 1)[1])
                default_reason = self._build_product_reason(product, evidence_ids)
                final_reason = self._sanitize_reason_text(
                    self._prefer_chinese_text(
                        composition.product_reason_overrides.get(product.sku_id),
                        default_reason,
                    )
                )
                recommended_items.append(
                    DraftRecommendationItem(
                        sku_id=product.sku_id,
                        display_name=self._canonical_product_name(product),
                        dosage=self._resolve_dosage(product, context),
                        reason=final_reason,
                        evidence_ids=evidence_ids,
                        evidence_details=list(
                            dict.fromkeys(
                                [
                                    *self._build_evidence_details(
                                        evidence_ids,
                                        product_by_id=product_by_id,
                                        knowledge_by_id=knowledge_by_id,
                                        clinician_rule_by_id=clinician_rule_by_id,
                                    ),
                                    *[
                                        f"支持需求：{support_need_by_id[need_id].support_need_text}"
                                        for need_id in matched_support_need_ids
                                    ],
                                ]
                            )
                        ),
                        warnings=list(
                            dict.fromkeys(
                                [
                                    *self._product_safety_warnings(product, context),
                                    *[
                                        decision.message
                                        for decision in safety_decisions_by_sku.get(product.sku_id, [])
                                        if decision.action != SafetyRuleAction.exclude
                                    ],
                                ]
                            )
                        )[:6],
                        primary_system_id=self._primary_system_for_product(product, priority_findings),
                        matched_finding_ids=list(dict.fromkeys(matched_finding_ids)),
                        matched_support_need_ids=matched_support_need_ids,
                        system_priority_rank=self._system_priority_rank_for_product(product, priority_findings),
                        safety_decisions=safety_decisions_by_sku.get(product.sku_id, []),
                    )
                )

        selected_evidence = [
            evidence_id
            for item in recommended_items
            for evidence_id in item.evidence_ids
        ]
        all_ranked_evidence = [evidence_id for ids in product_evidence_map.values() for evidence_id in ids]
        evidence_ids = list(dict.fromkeys([*selected_evidence, *all_ranked_evidence]))[:20]
        evidence_details = self._build_evidence_details(
            evidence_ids,
            product_by_id=product_by_id,
            knowledge_by_id=knowledge_by_id,
            clinician_rule_by_id=clinician_rule_by_id,
        )
        health_portrait = self._build_health_portrait(
            case,
            context,
            key_lab_highlights,
            risk_notices,
            report_guidance,
            anti_aging_findings=anti_aging_findings,
        )
        system_analysis = self._build_system_analysis(
            case,
            context,
            key_lab_highlights,
            report_guidance,
            anti_aging_findings=anti_aging_findings,
        )
        first_month_protocol = self._build_first_month_protocol(recommended_items)
        structured_system_findings = [
            StructuredSystemFinding(
                system_id=finding.system_id,
                system_name=SYSTEM_NAMES.get(finding.system_id, finding.title.split("（", 1)[0]),
                priority_level=finding.priority_level,
                priority_score=finding.score,
                summary=finding.body,
                finding_ids=list(finding.finding_ids),
            )
            for finding in priority_findings
        ]
        total_advice = self.build_total_advice_items(
            recommended_items,
            structured_system_findings=structured_system_findings,
        )
        lifestyle_focus = self._build_lifestyle_prescription(case, context, lifestyle_actions)
        test_recommendations = self._build_prioritized_test_recommendations(context, anti_aging_findings)
        supplement_adjustments = self._build_existing_supplement_adjustments(case)
        follow_up_plan = self._build_follow_up_plan(context)
        roadmap = self._build_ninety_day_roadmap(recommended_items, context)
        report_sections = {
            "病例摘要": case_summary,
            "核心结论与健康画像": health_portrait,
            "异常指标汇总": key_lab_highlights,
            "原报告小结与建议": report_guidance,
            "功能医学系统失衡分析": system_analysis,
            "风险提示": list(dict.fromkeys(risk_notices + contraindications)),
            "首月营养素干预方案": first_month_protocol,
            "总医嘱说明": total_advice,
            "生活方式干预处方": lifestyle_focus,
            "后续检查建议": test_recommendations,
            "现有补充剂调整建议": supplement_adjustments,
            "随访计划": follow_up_plan,
            "90天健康路线图": roadmap,
            "待确认项": missing_info,
        }
        if not report_guidance:
            report_sections.pop("原报告小结与建议", None)
        report_sections = self._apply_report_section_overrides(
            report_sections,
            composition.section_overrides,
            analysis_mode=analysis_mode,
        )
        report_sections = self._apply_rag_enhancements(report_sections, rag_hits, rag_audit)
        rag_audit_items = report_sections.pop("RAG内部审查", [])

        draft = RecommendationDraft(
            id=f"draft_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            status=DraftStatus.abstained if effective_abstain_reason else DraftStatus.pending_review,
            case_summary=case_summary,
            key_lab_highlights=key_lab_highlights,
            recommended_skus=recommended_items,
            lifestyle_actions=lifestyle_actions,
            rationale=composition.rationale,
            evidence_ids=evidence_ids,
            evidence_details=evidence_details,
            contraindications=list(dict.fromkeys(contraindications)),
            safety_decisions=[
                decision
                for sku_id in sorted(safety_decisions_by_sku)
                for decision in safety_decisions_by_sku[sku_id]
            ],
            missing_info=missing_info,
            confidence=composition.confidence,
            abstain_reason=effective_abstain_reason,
            manual_review_required=True,
            red_flags=[],
            structured_system_findings=structured_system_findings,
            report_sections=report_sections,
            internal_audit={
                "rag": list(rag_audit_items) if isinstance(rag_audit_items, list) else [],
                "finding_standardization": self._finding_standardization_audit(case),
                "product_selection": self._product_selection_audit(
                    context=context,
                    recommended_items=recommended_items,
                    product_evidence_map=product_evidence_map,
                    safety_decisions_by_sku=safety_decisions_by_sku,
                ),
            },
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            rule_version=self.rule_version,
            support_goal_version=self.support_goal_version,
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
                payload={
                    "case_id": case_id,
                    "draft_id": draft.id,
                    "status": draft.status.value,
                    "recommended_sku_count": len(draft.recommended_skus),
                    "model_version": draft.model_version,
                    "prompt_version": draft.prompt_version,
                    "rule_version": draft.rule_version,
                    "safety_decisions": [
                        {
                            "rule_id": decision.rule_id,
                            "sku_id": decision.sku_id,
                            "action": decision.action.value,
                            "message": decision.message,
                        }
                        for decision in draft.safety_decisions
                    ],
                    "rag_audit_count": len(draft.internal_audit.get("rag", [])),
                },
            )
        )
        return draft

    def _build_context(self, case) -> RecommendationContext:
        markers_by_code: dict[str, list] = {}
        for item in case.extracted_lab_items:
            if self._is_admin_metadata_snippet(getattr(getattr(item, "source_span", None), "snippet", "")):
                continue
            markers_by_code.setdefault(item.marker_code, []).append(item)
        # Do not reconstruct standard markers from arbitrary display strings. Only
        # doctor-confirmed, exactly standardized lab items enter marker rules.
        clinical_findings = list(getattr(case, "confirmed_clinical_findings", []) or [])
        clinical_findings_by_code: dict[str, list] = {}
        clinical_findings_by_system: dict[str, list] = {}
        support_goal_findings: dict[str, list] = {}
        for finding in clinical_findings:
            evidence_class = classify_confirmed_evidence(finding)
            if finding.finding_code:
                clinical_findings_by_code.setdefault(finding.finding_code, []).append(finding)
            for system_id in finding.system_ids:
                if system_id in SYSTEM_NAMES:
                    clinical_findings_by_system.setdefault(system_id, []).append(finding)
            for support_goal in finding.support_goals:
                if support_goal in AXIS_SYSTEM_MAP and evidence_class not in {
                    ClinicalEvidenceClass.genetic_risk,
                    ClinicalEvidenceClass.follow_up_only,
                }:
                    support_goal_findings.setdefault(support_goal, []).append(finding)

        questionnaire = case.questionnaire
        chief_concerns = {self._normalize(text) for text in (questionnaire.chief_concerns if questionnaire else [])}
        symptoms = {self._normalize(text) for text in (questionnaire.symptoms if questionnaire else [])}
        conditions = {self._normalize(text) for text in (questionnaire.known_conditions if questionnaire else [])}
        conditions.update(
            self._normalize(value)
            for finding in clinical_findings
            for value in (finding.finding_code, finding.finding_name)
            if value
        )
        family_history = {self._normalize(text) for text in (questionnaire.family_history if questionnaire else [])}
        goals = {self._normalize(text) for text in (questionnaire.goals if questionnaire else [])}
        medications = {self._normalize(text) for text in (questionnaire.medications if questionnaire else [])}
        allergies = {self._normalize(text) for text in (questionnaire.allergies if questionnaire else [])}
        food_sensitivities = {
            self._normalize(text) for text in (questionnaire.food_sensitivities if questionnaire else [])
        }
        msq_system_scores = dict(questionnaire.msq_system_scores) if questionnaire else {}
        summary_parts = [(case.clinical_summary_text or "").strip()]
        latest_analysis = self.repository.get_latest_case_analysis(case.id)
        structured_system_findings: list[StructuredSystemFinding] = []
        if latest_analysis and getattr(latest_analysis.status, "value", latest_analysis.status) == "reviewed":
            model_summary = (latest_analysis.reviewed_case_summary or "").strip()
            if model_summary:
                summary_parts.append(f"模型综合病例总结：{model_summary}")
            if latest_analysis.reviewed_system_findings:
                summary_parts.append("模型综合系统分析：" + "；".join(latest_analysis.reviewed_system_findings))
            structured_system_findings = list(latest_analysis.final_structured_system_findings)
        clinical_summary_text = "\n".join(item for item in summary_parts if item)
        normalized_clinical_summary = self._normalize(clinical_summary_text)
        summary_nutrient_hints = self._extract_summary_nutrient_hints(clinical_summary_text)

        lifestyle_tags: set[str] = set()
        if questionnaire:
            if (
                questionnaire.sleep_hours is not None
                and questionnaire.sleep_hours < 6
            ) or self._normalize(questionnaire.sleep_quality or "") in {
                "poor",
                "\u5dee",
            }:
                lifestyle_tags.add("sleep_recovery")
            if questionnaire.stress_level == "high":
                lifestyle_tags.add("stress_support")
            if self._normalize(questionnaire.exercise_frequency or "") in {"rare", "none", "很少", "寰堝皯"}:
                lifestyle_tags.add("movement")
            if self._normalize(questionnaire.bowel_habits or "") in {"constipation", "便秘", "渚跨"}:
                lifestyle_tags.add("gut_support")
            if (questionnaire.sitting_hours_per_day or 0) >= 6:
                lifestyle_tags.add("sedentary_risk")
            if questionnaire.dining_out_frequency and any(
                token in questionnaire.dining_out_frequency for token in ("4", "5", "6", "7", "频繁", "经常", "棰戠箒", "缁忓父")
            ):
                lifestyle_tags.add("outside_dining")
            if questionnaire.chemical_sensitivity:
                lifestyle_tags.add("chemical_sensitivity")

            if msq_system_scores.get("\u6d88\u5316\u9053", 0) >= 2:
                lifestyle_tags.add("gut_support")
            if msq_system_scores.get("能量/活动", 0) >= 2 or msq_system_scores.get("鑳介噺/娲诲姩", 0) >= 2:
                lifestyle_tags.add("energy_support")
            if (
                msq_system_scores.get("情绪", 0) >= 2
                or msq_system_scores.get("思维", 0) >= 2
                or msq_system_scores.get("鎯呯华", 0) >= 2
                or msq_system_scores.get("鎬濈淮", 0) >= 2
            ):
                lifestyle_tags.add("stress_support")
            if msq_system_scores.get("体重", 0) >= 2 or msq_system_scores.get("浣撻噸", 0) >= 2:
                lifestyle_tags.add("metabolic_support")

        if any(
            term in normalized_clinical_summary
            for term in (
                self._normalize("细胞能量生成反应不佳"),
                self._normalize("细胞能量生成不佳"),
                self._normalize("线粒体"),
                self._normalize("能量生成"),
                self._normalize("体力不佳"),
                self._normalize("疲劳"),
            )
        ):
            lifestyle_tags.add("energy_support")
        if any(
            term in normalized_clinical_summary
            for term in (
                self._normalize("碳水化合物代谢不佳"),
                self._normalize("糖代谢异常"),
                self._normalize("血糖"),
                self._normalize("胰岛素抵抗"),
                self._normalize("2型糖尿病"),
            )
        ):
            lifestyle_tags.add("metabolic_support")
        if any(
            term in normalized_clinical_summary
            for term in (
                self._normalize("脂肪酸代谢不佳"),
                self._normalize("脂代谢异常"),
                self._normalize("脂肪燃烧"),
                self._normalize("血脂"),
            )
        ):
            lifestyle_tags.add("metabolic_support")

        return RecommendationContext(
            markers_by_code=markers_by_code,
            clinical_findings=clinical_findings,
            clinical_findings_by_code=clinical_findings_by_code,
            clinical_findings_by_system=clinical_findings_by_system,
            support_goal_findings=support_goal_findings,
            goals=goals,
            chief_concerns=chief_concerns,
            symptoms=symptoms,
            conditions=conditions,
            family_history=family_history,
            medications=medications,
            allergies=allergies,
            food_sensitivities=food_sensitivities,
            pregnancy=bool(questionnaire and questionnaire.pregnant_or_lactating),
            age=questionnaire.age if questionnaire else None,
            lifestyle_tags=lifestyle_tags,
            msq_system_scores=msq_system_scores,
            clinical_summary_text=clinical_summary_text,
            summary_nutrient_hints=summary_nutrient_hints,
            structured_system_findings=structured_system_findings,
        )

    def _is_admin_metadata_snippet(self, snippet: str) -> bool:
        normalized = re.sub(r"\s+", "", snippet or "").strip()
        if not normalized:
            return False
        return any(normalized.startswith(prefix.replace(" ", "")) for prefix in self._ADMIN_METADATA_PREFIXES)

    def _augment_markers_from_case_indicators(self, case, markers_by_code: dict[str, list]) -> None:
        marker_aliases = {
            "non_hdl_c": {
                "expected_flag": "high",
                "aliases": ("non_hdl_c", "nonhdlc", "non-hdl-c", "非高密度脂蛋白胆固醇", "非HDL-C"),
            },
            "ldl_c": {
                "expected_flag": "high",
                "aliases": (
                    "ldl_c",
                    "ldlc",
                    "ldl-c",
                    "\u4f4e\u5bc6\u5ea6\u8102\u86cb\u767d\u80c6\u56fa\u9187",
                    "\u4f4e\u5bc6\u5ea6\u80c6\u56fa\u9187",
                ),
            },
            "total_cholesterol": {
                "expected_flag": "high",
                "aliases": ("totalcholesterol", "tc", "总胆固醇"),
            },
            "triglycerides": {
                "expected_flag": "high",
                "aliases": ("triglycerides", "tg", "甘油三酯"),
            },
            "apolipoprotein_b": {
                "expected_flag": "high",
                "aliases": ("apolipoproteinb", "apob", "载脂蛋白B", "载脂蛋白b"),
            },
            "ferritin": {
                "expected_flag": "low",
                "aliases": ("ferritin", "sf", "\u94c1\u86cb\u767d"),
            },
            "serum_iron": {
                "expected_flag": "low",
                "aliases": ("serumiron", "iron", "fe", "血清铁"),
            },
            "hemoglobin": {
                "expected_flag": "low",
                "aliases": ("hemoglobin", "hb", "hgb", "血红蛋白"),
            },
            "hematocrit": {
                "expected_flag": "low",
                "aliases": ("hematocrit", "hct", "红细胞压积"),
            },
            "mcv": {
                "expected_flag": "low",
                "aliases": ("mcv", "mean_corpuscular_volume", "平均红细胞体积"),
            },
        }

        for indicator in self.indicator_service.build(case):
            indicator_name = getattr(indicator, "indicator_name", "") or ""
            source_span = getattr(indicator, "source_span", None)
            snippet = getattr(source_span, "snippet", "") if source_span else ""
            haystack = self._normalize(f"{indicator_name} {snippet}")

            matched_marker_code = None
            expected_flag = "unknown"
            for marker_code, config in marker_aliases.items():
                normalized_aliases = {self._normalize(alias) for alias in config["aliases"]}
                if any(alias and alias in haystack for alias in normalized_aliases):
                    matched_marker_code = marker_code
                    expected_flag = config["expected_flag"]
                    break

            if not matched_marker_code or markers_by_code.get(matched_marker_code):
                continue

            indicator_status = getattr(getattr(indicator, "status", None), "value", "")
            if indicator_status == "normal":
                abnormal_flag = "normal"
            elif indicator_status in {"attention", "positive"}:
                abnormal_flag = expected_flag
            else:
                abnormal_flag = "unknown"

            markers_by_code.setdefault(matched_marker_code, []).append(
                SimpleNamespace(
                    marker_code=matched_marker_code,
                    marker_name=indicator_name or matched_marker_code,
                    value=None,
                    normalized_value=None,
                    abnormal_flag=SimpleNamespace(value=abnormal_flag),
                )
            )

    def _build_query(self, case, context: RecommendationContext, support_profiles: list[SupportProfile]) -> str:
        marker_terms = []
        for items in context.markers_by_code.values():
            for item in items:
                marker_terms.append(item.marker_name)
                marker_terms.append(item.abnormal_flag.value)

        questionnaire = case.questionnaire
        parts = [
            case.customer_name,
            " ".join(marker_terms),
            " ".join(
                finding.finding_name
                for finding in context.clinical_findings
            ),
            " ".join(questionnaire.chief_concerns if questionnaire else []),
            " ".join(questionnaire.goals if questionnaire else []),
            " ".join(questionnaire.symptoms if questionnaire else []),
            " ".join(questionnaire.known_conditions if questionnaire else []),
            " ".join(questionnaire.family_history if questionnaire else []),
            case.clinical_summary_text or "",
            " ".join(context.summary_nutrient_hints),
            " ".join(term for profile in support_profiles for term in profile.query_terms),
        ]
        return " ".join(part for part in parts if part).strip()

    def _evaluate_risk_notices(self, context: RecommendationContext, age: int | None) -> list[str]:
        risk_notices: list[str] = []
        if age is not None and age < 18:
            risk_notices.append("未成年案例需要医生重点审核营养素种类与剂量。")
        if context.pregnancy:
            risk_notices.append("孕期或哺乳期需要医生重点审核营养素种类与剂量。")
        if any(
            term in condition
            for condition in context.conditions
            for term in ("癌", "肾衰", "肝硬化", "renal", "cancer")
        ):
            risk_notices.append("既往疾病提示需要医生重点审核产品适用性。")
        if any(
            term in medication
            for medication in context.medications
            for term in ("华法林", "warfarin", "胰岛素", "insulin")
        ):
            risk_notices.append("当前用药与营养素之间可能存在相互作用，需要医生重点审核。")

        for items in context.markers_by_code.values():
            for item in items:
                value = item.normalized_value or item.value or 0
                if item.marker_code == "fasting_glucose" and value >= 7.0:
                    risk_notices.append("空腹血糖达到重点关注阈值，营养素草案需结合临床情况审核。")
                if item.marker_code == "hba1c" and value >= 6.5:
                    risk_notices.append("糖化血红蛋白达到重点关注阈值，营养素草案需结合临床情况审核。")
                if item.marker_code == "hs_crp" and value >= 10:
                    risk_notices.append("炎症指标显著升高，营养素草案需结合急性风险审核。")
                if item.marker_code == "alt" and value >= 120:
                    risk_notices.append("肝功能指标明显异常，营养素草案需由医生重点审核。")

        return list(dict.fromkeys(risk_notices))

    def _collect_missing_info(self, case) -> list[str]:
        missing: list[str] = []
        questionnaire = case.questionnaire
        has_clinical_summary = bool((case.clinical_summary_text or "").strip())
        if not case.files and not has_clinical_summary:
            missing.append("尚未上传报告文件。")
        if case.files and not case.parsing_review_completed:
            missing.append("尚未完成人工解析校对。")
        if has_clinical_summary and not case.files:
            missing.append("未上传原始报告，当前草案主要基于人工录入的病例总结诊断生成。")
        if questionnaire:
            if not questionnaire.medications:
                missing.append("尚未确认当前用药。")
            if not questionnaire.allergies:
                missing.append("尚未确认过敏史。")
            if not questionnaire.goals:
                missing.append("尚未明确主要健康目标。")
            if not questionnaire.chief_concerns:
                missing.append("尚未填写主要诉求。")
            if not questionnaire.msq_system_scores:
                missing.append("尚未补充 MSQ 系统负担评分。")
        else:
            missing.append("未填写问卷，当前草案仅依据已上传报告和人工校对结果生成。")
        missing.extend(self._visible_parse_warnings(case.parsing_missing_fields, case=case))
        return list(dict.fromkeys(missing))

    def _visible_parse_warnings(self, warnings: list[str] | None, *, case=None) -> list[str]:
        visible: list[str] = []
        for warning in warnings or []:
            if self._should_keep_parse_warning(warning, case=case):
                visible.append(warning)
        return visible

    def _should_keep_parse_warning(self, warning: str, *, case=None) -> bool:
        match = self._PARSE_WARNING_RE.match(str(warning or ""))
        if not match:
            return bool(str(warning or "").strip())

        line = re.sub(r"\s+", " ", match.group("line")).strip()
        if not line:
            return False
        if self._is_admin_metadata_snippet(line):
            return False
        if re.search(r"(?:电话|热线|手机号|身份证|病历号|条码|申请单|报告单)", line):
            return False
        if case is not None and self._parse_warning_already_has_indicator(line, case):
            return False

        normalization_service = getattr(getattr(self, "parsing_service", None), "normalization_service", None)
        if normalization_service is None:
            return True

        span = SourceSpan(file_name="parse-warning", page=1, line_number=1, snippet=line)
        try:
            lab_items = normalization_service.normalize(spans=[span])
            candidates = normalization_service.find_unknown_lab_candidates(spans=[span], lab_items=lab_items, limit=1)
        except Exception:
            return True

        # If the current dictionary can normalize the row, it belongs in structured indicators,
        # not in the "unrecognized metric" review chips. Keep only warnings that still look unknown.
        if lab_items:
            return False
        return bool(candidates)

    def _parse_warning_already_has_indicator(self, line: str, case) -> bool:
        warning_norm = self._normalize_parse_warning_line(line)
        if not warning_norm:
            return False
        try:
            indicators = self.indicator_service.build(case)
        except Exception:
            return False
        for indicator in indicators:
            snippet = getattr(getattr(indicator, "source_span", None), "snippet", "") or ""
            indicator_text = " ".join(
                str(part or "")
                for part in (
                    getattr(indicator, "indicator_name", ""),
                    getattr(indicator, "result_text", ""),
                    snippet,
                )
            )
            indicator_norm = self._normalize_parse_warning_line(indicator_text)
            if not indicator_norm:
                continue
            if warning_norm in indicator_norm or indicator_norm in warning_norm:
                return True
        return False

    def _normalize_parse_warning_line(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
        normalized = normalized.replace("μ", "u").replace("µ", "u")
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[|｜:：,，;；。()（）\[\]【】]", "", normalized)
        return normalized

    def _build_reviewed_report_text(self, case) -> str:
        chunks: list[str] = []
        for uploaded in case.files:
            text = (uploaded.corrected_text or uploaded.raw_extracted_text or "").strip()
            if text:
                chunks.append(f"[{uploaded.filename}]\n{text}")
        if case.clinical_summary_text:
            chunks.append(f"[病例总结诊断]\n{case.clinical_summary_text.strip()}")
        return "\n\n".join(chunks).strip()[:12000]

    def _build_structured_case_context(
        self,
        case,
        context: RecommendationContext,
        support_profiles: list[SupportProfile],
        report_guidance: list[str] | None = None,
    ) -> dict:
        questionnaire = case.questionnaire
        return {
            "analysis_mode": getattr(case.analysis_mode, "value", str(case.analysis_mode)),
            "support_profiles": [profile.title for profile in support_profiles],
            "markers": {
                marker_code: [
                    {
                        "marker_name": item.marker_name,
                        "value": item.value,
                        "normalized_value": item.normalized_value,
                        "abnormal_flag": getattr(item.abnormal_flag, "value", None),
                    }
                    for item in items
                ]
                for marker_code, items in context.markers_by_code.items()
            },
            "questionnaire": questionnaire.model_dump(mode="json") if questionnaire else None,
            "chief_concerns": list(context.chief_concerns),
            "symptoms": list(context.symptoms),
            "conditions": list(context.conditions),
            "family_history": list(context.family_history),
            "goals": list(context.goals),
            "lifestyle_tags": sorted(context.lifestyle_tags),
            "clinical_summary_text": case.clinical_summary_text,
            "summary_nutrient_hints": context.summary_nutrient_hints,
            "report_guidance": report_guidance or [],
        }

    def _extract_report_guidance(self, case) -> list[str]:
        guidance: list[str] = []
        guidance.extend(self._extract_manual_summary_guidance(case.clinical_summary_text))
        for uploaded in case.files:
            text = (uploaded.corrected_text or uploaded.raw_extracted_text or "").strip()
            if not text:
                continue

            lines = [self._clean_report_source_line(line) for line in text.splitlines()]
            lines = [line for line in lines if line]
            guidance.extend(self._extract_positive_result_summary(lines))
            guidance.extend(self._extract_summary_opinion_lines(lines))
            guidance.extend(self._extract_expert_advice_sections(lines))

        return list(dict.fromkeys(item for item in guidance if item))[:12]

    def _extract_manual_summary_guidance(self, summary_text: str | None) -> list[str]:
        if not summary_text:
            return []

        nutrient_hints = self._extract_summary_nutrient_hints(summary_text)
        cleaned_lines = [
            self._clean_report_source_line(line)
            for line in re.split(r"[\r\n]+", summary_text)
            if line and line.strip()
        ]
        cleaned_lines = [line for line in cleaned_lines if line]
        if not cleaned_lines:
            return []

        guidance: list[str] = []
        if nutrient_hints:
            guidance.append(f"病例总结提示的所需营养素：{'、'.join(nutrient_hints[:16])}。")
        for line in cleaned_lines[:8]:
            if self._looks_like_summary_nutrient_section_line(line):
                continue
            guidance.append(f"人工录入评估结论：{line}。")
        return guidance

    def _extract_summary_nutrient_hints(self, summary_text: str | None) -> list[str]:
        if not summary_text:
            return []

        hints: list[str] = []
        in_nutrient_section = False
        for raw_line in re.split(r"[\r\n]+", summary_text):
            line = self._clean_report_source_line(raw_line)
            if not line:
                continue

            normalized_line = self._normalize(line)
            if any(marker in normalized_line for marker in ("所需要的营养素", "所需营养素", "需要的营养素")):
                in_nutrient_section = True
                continue

            if not in_nutrient_section:
                continue

            extracted = self._extract_nutrient_terms_from_line(line)
            if extracted:
                hints.extend(extracted)
                continue

            if hints and self._looks_like_manual_summary_sentence(line):
                break

        return list(dict.fromkeys(hints))[:24]

    def _extract_nutrient_terms_from_line(self, line: str) -> list[str]:
        cleaned = self._clean_report_source_line(line)
        if not cleaned:
            return []

        compact = self._normalize(cleaned)
        if compact in {"分类", "category"}:
            return []
        if compact.startswith("no:") or compact.startswith("no：") or re.fullmatch(r"no[a-z0-9\-]+", compact):
            return []
        if any(marker in compact for marker in ("所需要的营养素", "所需营养素", "需要的营养素")):
            return []
        if self._looks_like_manual_summary_sentence(cleaned):
            return []

        candidate = re.sub(r"^\d+[、.)．]\s*", "", cleaned)
        candidate = re.sub(r"^[A-Za-z]{1,3}\s*[:：]\s*", "", candidate)
        candidate = candidate.strip(" -—:：")
        if not candidate:
            return []

        tokens: list[str] = []
        base = re.sub(r"\([^)]*\)", "", candidate).strip(" -—:：,，")
        if base:
            tokens.append(base)

        for group in re.findall(r"\(([^)]*)\)", candidate):
            for part in re.split(r"[,，/、]+", group):
                part = part.strip(" -—:：,，")
                if not part:
                    continue
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9\s.\-]{2,}", part):
                    continue
                tokens.append(part)

        normalized_tokens: list[str] = []
        for token in tokens:
            if self._looks_like_summary_nutrient_heading(token):
                continue
            if len(token) == 1 and not token.upper().startswith("B"):
                continue
            normalized_tokens.append(token)

        return list(dict.fromkeys(normalized_tokens))

    def _looks_like_summary_nutrient_section_line(self, line: str) -> bool:
        return bool(self._extract_nutrient_terms_from_line(line)) or any(
            marker in self._normalize(line)
            for marker in ("所需要的营养素", "所需营养素", "需要的营养素", "分类")
        )

    def _looks_like_summary_nutrient_heading(self, text: str) -> bool:
        normalized = self._normalize(text)
        return normalized in {
            "分类",
            "category",
            "protein",
            "vitamin",
            "mineral",
        }

    def _looks_like_manual_summary_sentence(self, line: str) -> bool:
        return bool(re.search(r"[。；;]|建议|提示|可能|导致|改善|诊疗|复查|随访|处理", line))

    def _clean_report_source_line(self, line: str) -> str:
        line = unicodedata.normalize("NFKC", line)
        line = line.translate(str.maketrans({"⻬": "齐", "⻅": "见", "⻝": "食"}))
        line = line.replace("|", " ")
        line = re.sub(r"\s+", " ", line)
        return line.strip(" :：,，;；")

    def _extract_positive_result_summary(self, lines: list[str]) -> list[str]:
        findings: list[str] = []
        for line in lines:
            match = re.match(r"^【?\d+】?\s*(?P<finding>[\u4e00-\u9fffA-Za-z0-9()+\-]+)$", line)
            if match:
                finding = match.group("finding").strip()
                if 2 <= len(finding) <= 30 and not finding.startswith(("检查", "项目", "单位")):
                    findings.append(finding)

        if not findings:
            return []
        return [f"原体检报告阳性/异常提示：{'、'.join(list(dict.fromkeys(findings))[:8])}。"]

    def _extract_summary_opinion_lines(self, lines: list[str]) -> list[str]:
        summaries: list[str] = []
        for line in lines:
            match = re.search(r"(小结|初步意见|检查小结|总检结论|专家意见)\s*(?P<value>.+)$", line)
            if not match:
                continue
            value = match.group("value").strip(" :：")
            if value and value not in {"未见明显异常", "无"} and not value.startswith("未见明显异常"):
                summaries.append(f"{match.group(1)}：{value}。")
        return summaries[:8]

    def _extract_expert_advice_sections(self, lines: list[str]) -> list[str]:
        advice: list[str] = []
        in_advice = False
        current_heading = ""
        current_items: list[str] = []

        def flush() -> None:
            nonlocal current_heading, current_items
            if current_heading and current_items:
                joined = "；".join(item.rstrip("。；; ") for item in current_items[:4] if item.strip())
                advice.append(f"专家建议与指导 - {current_heading}：{joined}。")
            current_heading = ""
            current_items = []

        stop_prefixes = ("健康体检结果", "检查项目", "血常规", "尿常规", "生化", "一般检查")
        for line in lines:
            compact_line = line.replace(" ", "")
            if compact_line in {"专家建议与指导", "专家指导建议"}:
                in_advice = True
                continue
            if not in_advice:
                continue
            if line.startswith(stop_prefixes):
                flush()
                break
            if self._looks_like_report_advice_heading(line):
                flush()
                current_heading = line
                continue
            if current_heading:
                current_items.append(line)

        flush()
        return advice[:8]

    def _looks_like_report_advice_heading(self, line: str) -> bool:
        if not re.search(r"[\u4e00-\u9fff]", line):
            return False
        if re.search(r"[。；;,.，:：]|\d+[、.]", line):
            return False
        return 2 <= len(line) <= 24

    def list_matched_clinician_rules(
        self,
        case,
        *,
        context: RecommendationContext | None = None,
        support_profiles: list[SupportProfile] | None = None,
    ) -> list[ClinicianRule]:
        context = context or self._build_context(case)
        support_profiles = support_profiles or self._build_support_profiles(context)
        matched_rules: list[ClinicianRule] = []
        for rule in self.repository.list_clinician_rules(enabled_only=True):
            if not self._clinician_rule_visible_for_case(rule, case):
                continue
            if self._clinician_rule_match_score(rule, context, support_profiles) > 0:
                matched_rules.append(rule)
        return matched_rules

    def _clinician_rule_visible_for_case(self, rule: ClinicianRule, case) -> bool:
        rule_scope = getattr(rule.scope, "value", str(rule.scope))
        case_scope = getattr(case.workspace_scope, "value", str(case.workspace_scope))
        if rule_scope == "public":
            return True
        return case_scope == "doctor" and rule.owner_doctor_id and rule.owner_doctor_id == case.owner_doctor_id

    def _clinician_rule_match_score(
        self,
        rule: ClinicianRule,
        context: RecommendationContext,
        support_profiles: list[SupportProfile],
    ) -> float:
        score = 0.0
        profile_ids = {profile.profile_id for profile in support_profiles}

        for marker_rule in rule.trigger_marker_rules:
            if self._matches_rule(marker_rule, context):
                score += 1.2

        for profile_id in rule.trigger_support_profiles:
            if profile_id in profile_ids:
                score += 1.0

        for goal in rule.trigger_goals:
            if self._normalize(goal) in context.goals:
                score += 0.45

        for symptom in rule.trigger_symptoms:
            if self._normalize(symptom) in context.symptoms:
                score += 0.45

        for concern in rule.trigger_chief_concerns:
            if self._normalize(concern) in context.chief_concerns:
                score += 0.4

        for condition in rule.trigger_conditions:
            if self._normalize(condition) in context.conditions:
                score += 0.5

        return round(score, 3)

    def _score_product_from_clinician_rules(
        self,
        product: ProductRule,
        matched_rules: list[ClinicianRule],
    ) -> tuple[float, list[str], list[str]]:
        score = 0.0
        evidence_ids: list[str] = []
        notes: list[str] = []
        for rule in matched_rules:
            if product.sku_id not in rule.target_sku_ids:
                continue
            evidence_ids.append(f"clinician_rule:{rule.id}")
            if rule.action == ClinicianRuleAction.avoid:
                score -= 1.8 * max(rule.strength, 0.2)
                notes.append(f"医生规则提示当前阶段谨慎处理 {product.display_name}")
            else:
                score += 2.25 * max(rule.strength, 0.2)
                notes.append(f"医生规则建议当前阶段优先考虑 {product.display_name}")
        return round(score, 3), list(dict.fromkeys(evidence_ids)), list(dict.fromkeys(notes))

    def _rank_products_for_llm_primary(
        self,
        case,
        context: RecommendationContext,
        knowledge_hits,
        support_profiles: list[SupportProfile],
        matched_clinician_rules: list[ClinicianRule],
        priority_findings: list[SystemPriority] | None = None,
    ):
        ranked: list[tuple[float, ProductRule, list[str]]] = []
        contraindications: list[str] = []
        safety_decisions_by_sku: dict[str, list[SafetyDecision]] = {}
        case_query = self._normalize(
            " ".join(
                filter(
                    None,
                    [
                        self._build_query(case, context, support_profiles),
                        self._build_reviewed_report_text(case),
                    ],
                )
            )
        )

        for product in self._list_products(enabled_only=True):
            safety_decisions = self._evaluate_product_safety(product, context)
            safety_decisions_by_sku[product.sku_id] = safety_decisions
            exclusions = [item for item in safety_decisions if item.action == SafetyRuleAction.exclude]
            if exclusions:
                contraindications.extend([f"{product.display_name} 被排除：{item.message}" for item in exclusions])
                continue

            score = max(0.08, (100 - product.priority) / 160)
            evidence_ids = [f"product:{product.sku_id}"]
            supportive_evidence = 0

            tag_score, tag_evidence_ids = self._score_product_from_tag_matrix(
                product,
                context,
                priority_findings=priority_findings,
            )
            score += tag_score
            evidence_ids.extend(tag_evidence_ids)
            supportive_evidence += len(tag_evidence_ids)
            product_system_id = self._primary_system_for_product(product, priority_findings)
            product_system_rank = self._system_priority_rank_for_product(product, priority_findings)
            if product_system_id and product_system_rank is not None:
                evidence_ids.extend(
                    [f"signal:body_system_{product_system_id}", f"signal:body_system_rank_{product_system_rank}"]
                )

            for indication in product.indications:
                if self._matches_rule(indication, context):
                    score += 0.7
                    supportive_evidence += 1
                    evidence_ids.append("signal:direct_product_rule")
                    if indication.startswith("marker:"):
                        marker_code = indication.split(":", 2)[1]
                        evidence_ids.append(f"signal:direct_marker_rule_{marker_code}")

            signal_score, signal_evidence_ids = self._score_product_from_profiles(product, support_profiles)
            score += signal_score
            evidence_ids.extend(signal_evidence_ids)
            supportive_evidence += len(signal_evidence_ids)

            clinical_score, clinical_evidence_ids = self._score_product_from_clinical_patterns(product, context)
            score += clinical_score
            evidence_ids.extend(clinical_evidence_ids)
            supportive_evidence += len(clinical_evidence_ids)

            clinician_score, clinician_evidence_ids, clinician_notes = self._score_product_from_clinician_rules(
                product,
                matched_clinician_rules,
            )
            score += clinician_score
            evidence_ids.extend(clinician_evidence_ids)
            supportive_evidence += len(clinician_evidence_ids)
            contraindications.extend(clinician_notes)

            context_fit_score = self._score_product_from_case_context(product, case_query)
            if context_fit_score > 0:
                score += context_fit_score
                evidence_ids.append("signal:case_context_match")
                supportive_evidence += 1

            nutrient_score, nutrient_evidence_ids = self._score_product_from_summary_nutrients(
                product,
                context.summary_nutrient_hints,
            )
            score += nutrient_score
            evidence_ids.extend(nutrient_evidence_ids)
            supportive_evidence += len(nutrient_evidence_ids)

            for hit in knowledge_hits:
                statement = hit.statement
                if product.sku_id in statement.related_skus:
                    score += 0.65 + hit.score
                    evidence_ids.append(statement.statement_id)
                    supportive_evidence += 1
                elif self._statement_supports_product(statement, product):
                    score += hit.score * 0.4
                    evidence_ids.append(statement.statement_id)
                    supportive_evidence += 1

            if supportive_evidence == 0 or not self._has_eligible_product_evidence(evidence_ids):
                continue

            ranked.append((round(score, 3), product, list(dict.fromkeys(evidence_ids))))

        ranked.sort(key=self._ranked_product_sort_key, reverse=True)
        product_evidence_map = {product.sku_id: evidence for _, product, evidence in ranked}
        return (
            [product for _, product, _ in ranked],
            product_evidence_map,
            list(dict.fromkeys(contraindications)),
            safety_decisions_by_sku,
        )

    def _score_product_from_case_context(self, product: ProductRule, case_query: str) -> float:
        if not case_query:
            return 0.0

        searchable_text = self._normalize(
            " ".join(
                [
                    product.display_name,
                    product.category,
                    product.formula_summary,
                    " ".join(product.core_ingredients),
                    " ".join(product.candidate_use_cases),
                    " ".join(product.lifestyle_tags),
                ]
            )
        )
        product_tokens = {token for token in re.findall(r"[\w\u4e00-\u9fff]+", searchable_text) if len(token) > 1}
        query_tokens = {token for token in re.findall(r"[\w\u4e00-\u9fff]+", case_query) if len(token) > 1}
        overlap = len(product_tokens & query_tokens)
        if overlap == 0:
            return 0.0
        return round(min(overlap, 5) * 0.12, 3)

    def _rank_products(
        self,
        context: RecommendationContext,
        knowledge_hits,
        support_profiles: list[SupportProfile],
        matched_clinician_rules: list[ClinicianRule],
        priority_findings: list[SystemPriority] | None = None,
    ):
        ranked: list[tuple[float, ProductRule, list[str]]] = []
        contraindications: list[str] = []
        safety_decisions_by_sku: dict[str, list[SafetyDecision]] = {}
        products = self._list_products(enabled_only=True)

        for product in products:
            safety_decisions = self._evaluate_product_safety(product, context)
            safety_decisions_by_sku[product.sku_id] = safety_decisions
            exclusions = [item for item in safety_decisions if item.action == SafetyRuleAction.exclude]
            if exclusions:
                contraindications.extend([f"{product.display_name} 被排除：{item.message}" for item in exclusions])
                continue

            score = max(0.05, (100 - product.priority) / 100)
            evidence_ids: list[str] = []
            direct_hits = 0
            supportive_evidence = 0

            tag_score, tag_evidence_ids = self._score_product_from_tag_matrix(
                product,
                context,
                priority_findings=priority_findings,
            )
            score += tag_score
            evidence_ids.extend(tag_evidence_ids)
            supportive_evidence += len(tag_evidence_ids)
            product_system_id = self._primary_system_for_product(product, priority_findings)
            product_system_rank = self._system_priority_rank_for_product(product, priority_findings)
            if product_system_id and product_system_rank is not None:
                evidence_ids.extend(
                    [f"signal:body_system_{product_system_id}", f"signal:body_system_rank_{product_system_rank}"]
                )

            for indication in product.indications:
                if self._matches_rule(indication, context):
                    direct_hits += 1
                    supportive_evidence += 1
                    score += 0.9
                    evidence_ids.append("signal:direct_product_rule")
                    if indication.startswith("marker:"):
                        marker_code = indication.split(":", 2)[1]
                        evidence_ids.append(f"signal:direct_marker_rule_{marker_code}")

            signal_score, signal_evidence_ids = self._score_product_from_profiles(product, support_profiles)
            score += signal_score
            evidence_ids.extend(signal_evidence_ids)
            supportive_evidence += len(signal_evidence_ids)

            clinical_score, clinical_evidence_ids = self._score_product_from_clinical_patterns(product, context)
            score += clinical_score
            evidence_ids.extend(clinical_evidence_ids)
            supportive_evidence += len(clinical_evidence_ids)

            clinician_score, clinician_evidence_ids, clinician_notes = self._score_product_from_clinician_rules(
                product,
                matched_clinician_rules,
            )
            score += clinician_score
            evidence_ids.extend(clinician_evidence_ids)
            supportive_evidence += len(clinician_evidence_ids)
            contraindications.extend(clinician_notes)

            nutrient_score, nutrient_evidence_ids = self._score_product_from_summary_nutrients(
                product,
                context.summary_nutrient_hints,
            )
            score += nutrient_score
            evidence_ids.extend(nutrient_evidence_ids)
            supportive_evidence += len(nutrient_evidence_ids)

            for hit in knowledge_hits:
                statement = hit.statement
                if product.sku_id in statement.related_skus:
                    score += 0.7 + hit.score
                    evidence_ids.append(statement.statement_id)
                    supportive_evidence += 1
                elif self._statement_supports_product(statement, product):
                    score += hit.score * 0.35
                    evidence_ids.append(statement.statement_id)
                    supportive_evidence += 1

            if supportive_evidence == 0 or not self._has_eligible_product_evidence(evidence_ids):
                continue

            ranked.append(
                (
                    round(score, 3),
                    product,
                    [f"product:{product.sku_id}", *list(dict.fromkeys(evidence_ids))],
                )
            )

        ranked.sort(key=self._ranked_product_sort_key, reverse=True)
        product_evidence_map = {product.sku_id: evidence for _, product, evidence in ranked}
        return (
            [product for _, product, _ in ranked],
            product_evidence_map,
            list(dict.fromkeys(contraindications)),
            safety_decisions_by_sku,
        )

    @staticmethod
    def _has_eligible_product_evidence(evidence_ids: list[str]) -> bool:
        return any(
            evidence_id.startswith(
                (
                    "signal:model_support_goal_",
                    "signal:tag_marker_",
                    "signal:direct_marker_rule_",
                )
            )
            for evidence_id in evidence_ids
        )

    def _build_support_profiles(self, context: RecommendationContext) -> list[SupportProfile]:
        profiles: list[SupportProfile] = []
        normalized_summary = self._normalize(context.clinical_summary_text)

        if self._matches_pattern("lipid_disorder", context) or any(
            term in normalized_summary
            for term in (
                self._normalize("脂肪酸代谢不佳"),
                self._normalize("脂代谢异常"),
                self._normalize("血脂异常"),
                self._normalize("脂肪燃烧生成能量"),
            )
        ):
            profiles.append(
                SupportProfile(
                    profile_id="lipid_balance",
                    title="血脂代谢支持",
                    weight=1.45,
                    preferred_categories=("omega3_support", "cardiometabolic_support", "methylation_support"),
                    match_terms=("心血管支持", "抗炎支持", "代谢支持", "恢复支持", "鱼油", "EPA", "DHA", "辅酶Q10"),
                    query_terms=("血脂异常", "心血管支持", "抗炎支持", "脂代谢", "鱼油"),
                    marker_codes=("ldl_c", "non_hdl_c", "total_cholesterol", "triglycerides", "apolipoprotein_b"),
                )
            )

        if self._matches_pattern("iron_deficiency", context):
            profiles.append(
                SupportProfile(
                    profile_id="iron_repletion",
                    title="缺铁与造血支持",
                    weight=1.35,
                    preferred_categories=("foundational_support", "antioxidant_support"),
                    match_terms=("基础营养", "微量营养补充", "维生素C补充", "铁代谢支持", "造血支持"),
                    query_terms=("缺铁", "铁蛋白偏低", "血清铁偏低", "造血支持", "维生素C补充"),
                    marker_codes=("ferritin", "serum_iron", "hemoglobin", "hematocrit", "mcv"),
                )
            )

        if (
            self._has_marker(context, "fasting_glucose", "high")
            or self._has_marker(context, "hba1c", "high")
            or any(
                term in normalized_summary
                for term in (
                    self._normalize("碳水化合物代谢不佳"),
                    self._normalize("血糖"),
                    self._normalize("胰岛素抵抗"),
                    self._normalize("2型糖尿病"),
                )
            )
        ):
            profiles.append(
                SupportProfile(
                    profile_id="glycemic_balance",
                    title="血糖与代谢支持",
                    weight=1.35,
                    preferred_categories=("glucose_support", "weight_support", "foundational_support"),
                    match_terms=("血糖平衡", "代谢支持", "血糖管理", "体重管理", "基础微量营养补充"),
                    query_terms=("血糖平衡", "代谢支持", "胰岛素敏感性", "体重管理"),
                    marker_codes=("fasting_glucose", "hba1c"),
                )
            )

        if self._has_marker(context, "hs_crp", "high"):
            profiles.append(
                SupportProfile(
                    profile_id="inflammation_resolution",
                    title="炎症与恢复支持",
                    weight=1.2,
                    preferred_categories=("inflammation", "omega3_support", "antioxidant_support"),
                    match_terms=("抗炎支持", "抗氧化", "恢复支持", "鱼油", "白藜芦醇", "维生素C"),
                    query_terms=("炎症支持", "抗炎支持", "恢复支持", "抗氧化"),
                    marker_codes=("hs_crp",),
                )
            )

        if self._has_marker(context, "vitamin_d", "low"):
            profiles.append(
                SupportProfile(
                    profile_id="vitamin_d_repletion",
                    title="维生素D补充支持",
                    weight=1.25,
                    preferred_categories=("fat_soluble_support", "immune_support", "foundational_support"),
                    match_terms=("维生素D支持", "骨骼支持", "免疫支持", "VD3", "K"),
                    query_terms=("维生素D偏低", "骨骼支持", "免疫支持", "维生素D支持"),
                    marker_codes=("vitamin_d",),
                )
            )

        if self._has_thyroid_pattern(context):
            profiles.append(
                SupportProfile(
                    profile_id="thyroid_axis",
                    title="甲状腺轴支持",
                    weight=1.3,
                    preferred_categories=("thyroid_support", "antioxidant_support"),
                    match_terms=("甲状腺支持", "基础代谢支持", "抗氧化", "硒", "维生素E"),
                    query_terms=("甲状腺支持", "甲状腺抗体", "基础代谢支持", "硒"),
                    marker_codes=("tsh", "thyroglobulin_antibody", "thyroid_peroxidase_antibody"),
                )
            )

        if self._has_marker(context, "homocysteine", "high"):
            profiles.append(
                SupportProfile(
                    profile_id="methylation",
                    title="甲基化与心血管支持",
                    weight=1.15,
                    preferred_categories=("methylation_support", "foundational_support", "energy_support"),
                    match_terms=("甲基化支持", "心血管支持", "基础维生素补充", "B族", "辅酶Q10"),
                    query_terms=("甲基化支持", "HCY管理", "心血管支持", "B族"),
                    marker_codes=("homocysteine",),
                )
            )

        if self._has_liver_metabolic_pattern(context):
            profiles.append(
                SupportProfile(
                    profile_id="liver_detox",
                    title="肝胆与解毒支持",
                    weight=1.3,
                    preferred_categories=("detox_support", "digestive_support"),
                    match_terms=("解毒支持", "肝脏支持", "肝胆支持", "胆汁分泌支持", "谷胱甘肽"),
                    query_terms=("肝脏支持", "肝胆支持", "解毒支持", "胆汁分泌", "谷胱甘肽"),
                    marker_codes=("alt", "ast", "ggt", "uric_acid", "triglycerides"),
                )
            )

        if "sleep_recovery" in context.lifestyle_tags or "stress_support" in context.lifestyle_tags:
            profiles.append(
                SupportProfile(
                    profile_id="sleep_stress",
                    title="睡眠与压力恢复",
                    weight=0.9,
                    preferred_categories=("sleep_support", "stress_support", "mineral_support"),
                    match_terms=("睡眠恢复", "压力管理", "情绪平衡", "入睡困难", "夜间觉醒"),
                    query_terms=("睡眠恢复", "压力管理", "情绪平衡"),
                    marker_codes=(),
                )
            )

        if any(
            term in normalized_summary
            for term in (
                self._normalize("细胞能量生成反应不佳"),
                self._normalize("细胞能量生成不佳"),
                self._normalize("线粒体"),
                self._normalize("认知功能衰退"),
                self._normalize("活动力下降"),
                self._normalize("体力不佳"),
            )
        ):
            profiles.append(
                SupportProfile(
                    profile_id="cellular_energy",
                    title="细胞能量与线粒体支持",
                    weight=1.25,
                    preferred_categories=("energy_support", "foundational_support", "antioxidant_support"),
                    match_terms=("线粒体支持", "能量支持", "辅酶Q10", "B族", "抗氧化支持"),
                    query_terms=("细胞能量支持", "线粒体支持", "疲劳恢复", "认知支持"),
                    marker_codes=(),
                )
            )

        if self._summary_hints_include(
            context.summary_nutrient_hints,
            "蛋白质",
            "氨基酸",
            "肉碱",
            "精氨酸",
            "谷氨酰胺",
            "组氨酸",
            "脯氨酸",
            "谷氨酸",
            "牛磺酸",
            "甘氨酸",
            "半胱氨酸",
        ):
            profiles.append(
                SupportProfile(
                    profile_id="summary_amino_support",
                    title="病例总结提示蛋白质/氨基酸支持",
                    weight=1.05,
                    preferred_categories=(
                        "foundational_support",
                        "energy_support",
                        "gut_mucosal_support",
                        "detox_support",
                        "cardiometabolic_support",
                    ),
                    match_terms=("蛋白质", "氨基酸", "肉碱", "谷氨酰胺", "精氨酸", "牛磺酸"),
                    query_terms=("蛋白质支持", "氨基酸支持", "能量恢复", "黏膜修复"),
                    marker_codes=(),
                )
            )

        if self._summary_hints_include(
            context.summary_nutrient_hints,
            "维生素",
            "B1",
            "B2",
            "B3",
            "B5",
            "B6",
            "B12",
            "硫胺素",
            "核黄素",
            "烟酸",
            "泛酸",
            "吡哆醇",
            "叶酸",
            "生物素",
        ):
            profiles.append(
                SupportProfile(
                    profile_id="summary_vitamin_support",
                    title="病例总结提示维生素/B族支持",
                    weight=1.05,
                    preferred_categories=(
                        "foundational_support",
                        "glucose_support",
                        "energy_support",
                        "methylation_support",
                        "fat_soluble_support",
                        "immune_support",
                        "antioxidant_support",
                        "thyroid_support",
                    ),
                    match_terms=("维生素", "B族", "叶酸", "生物素", "甲基化支持", "能量支持"),
                    query_terms=("维生素支持", "B族支持", "基础微量营养补充", "甲基化支持"),
                    marker_codes=(),
                )
            )

        if self._summary_hints_include(
            context.summary_nutrient_hints,
            "矿物质",
            "镁",
            "锌",
            "硒",
            "铁",
            "钙",
            "钾",
            "铜",
            "锰",
            "钼",
            "碘",
        ):
            profiles.append(
                SupportProfile(
                    profile_id="summary_mineral_support",
                    title="病例总结提示矿物质支持",
                    weight=0.95,
                    preferred_categories=("mineral_support", "foundational_support", "cardiometabolic_support", "thyroid_support"),
                    match_terms=("矿物质", "镁", "锌", "硒", "钙", "钾"),
                    query_terms=("矿物质支持", "基础矿物支持", "多维矿支持"),
                    marker_codes=(),
                )
            )

        deduped: list[SupportProfile] = []
        seen_ids: set[str] = set()
        for profile in profiles:
            if profile.profile_id not in seen_ids:
                deduped.append(profile)
                seen_ids.add(profile.profile_id)
        return deduped

    def _score_product_from_profiles(
        self,
        product: ProductRule,
        support_profiles: list[SupportProfile],
    ) -> tuple[float, list[str]]:
        searchable_text = self._normalize(
            " ".join(
                [
                    product.sku_id,
                    product.display_name,
                    product.category,
                    product.formula_summary,
                    " ".join(product.core_ingredients),
                    " ".join(product.candidate_use_cases),
                    " ".join(product.indications),
                    " ".join(product.lifestyle_tags),
                ]
            )
        )
        score = 0.0
        evidence_ids: list[str] = []

        for profile in support_profiles:
            profile_score = 0.0

            if product.category in profile.preferred_categories:
                profile_score += 0.72 * profile.weight

            use_case_hits = sum(
                1
                for term in profile.match_terms
                if self._normalize(term) in searchable_text
            )
            if use_case_hits:
                profile_score += min(use_case_hits, 4) * 0.18 * profile.weight

            marker_rule_hits = sum(
                1
                for marker_code in profile.marker_codes
                if self._normalize(f"marker:{marker_code}") in searchable_text
            )
            if marker_rule_hits:
                profile_score += min(marker_rule_hits, 2) * 0.16 * profile.weight

            if self._normalize(f"pattern:{profile.profile_id}") in searchable_text:
                profile_score += 0.35 * profile.weight

            if profile_score >= 0.38:
                score += profile_score
                evidence_ids.append(f"signal:{profile.profile_id}")

        return round(score, 3), list(dict.fromkeys(evidence_ids))

    def _active_product_axes(
        self,
        context: RecommendationContext,
        priority_findings: list[SystemPriority] | None = None,
    ) -> dict[str, float]:
        axes: dict[str, float] = {}

        def add(axis: str, weight: float) -> None:
            axes[axis] = max(axes.get(axis, 0.0), weight)

        findings = priority_findings if priority_findings is not None else self._prioritized_system_findings(context)
        for finding in findings:
            # The same priority score drives both report order and nutrient ranking.
            # Low-priority displayed systems should inform ranking without forcing weak product matches.
            weight = min(1.0, max(0.25, finding.score / 100))
            for axis in finding.axes:
                add(axis, weight)

        if context.summary_nutrient_hints:
            add("foundational", 0.56)

        return axes

    def _prioritized_system_findings(
        self,
        context: RecommendationContext,
        report_guidance: list[str] | None = None,
        anti_aging_findings: list[str] | None = None,
    ) -> list[SystemPriority]:
        if context.structured_system_findings:
            structured: list[SystemPriority] = []
            for item in context.structured_system_findings:
                goals = [
                    goal
                    for goal, findings in context.support_goal_findings.items()
                    if any(item.system_id in finding.system_ids for finding in findings)
                ]
                sources = {
                    (finding.source_span.file_id or finding.source_span.file_name)
                    for goal in goals
                    for finding in context.support_goal_findings.get(goal, [])
                }
                # Final synthesis priorities have already been recalculated from
                # evidence classes. Do not re-inflate model labels or source count.
                score = min(100.0, max(0.0, item.priority_score))
                structured.append(
                    SystemPriority(
                        system_id=item.system_id,
                        title=f"{SYSTEM_NAMES.get(item.system_id, item.system_name)}（{item.priority_level}）",
                        body=item.summary,
                        score=score,
                        axes=tuple(dict.fromkeys(goals)),
                        priority_level=item.priority_level,
                        finding_ids=tuple(item.finding_ids),
                    )
                )
            return sorted(structured, key=lambda value: value.score, reverse=True)

        text = self._pattern_text(context)
        guidance_text = self._normalize(" ".join(report_guidance or []))
        anti_aging_text = self._normalize(" ".join(anti_aging_findings or []))
        combined_text = self._normalize(" ".join([text, guidance_text, anti_aging_text]))
        findings: list[SystemPriority] = []

        def add_finding(
            *,
            system_id: str,
            base_title: str,
            body: str,
            score: float,
            axes: tuple[str, ...],
            threshold: float = 8,
        ) -> None:
            if score < threshold:
                return
            findings.append(
                SystemPriority(
                    system_id=system_id,
                    title=f"{SYSTEM_NAMES.get(system_id, base_title)}（{self._priority_label(score)}）",
                    body=build_system_summary(
                        system_id,
                        self._system_evidence_names(context, system_id),
                        score,
                    ),
                    score=round(score, 2),
                    axes=axes,
                    priority_level=self._priority_label(score),
                )
            )

        lipid_marker_count = self._marker_hit_count(
            context,
            ("ldl_c", "non_hdl_c", "total_cholesterol", "triglycerides", "apolipoprotein_b"),
            "high",
        )
        liver_marker_count = self._marker_hit_count(context, ("alt", "ast", "ggt"), "high")
        thyroid_marker_count = self._marker_hit_count(
            context,
            ("tsh", "thyroglobulin_antibody", "thyroid_peroxidase_antibody"),
            "high",
        )

        metabolic_score = 0.0
        if self._has_marker(context, "fasting_glucose", "high") or self._has_marker(context, "hba1c", "high"):
            metabolic_score += 48
        if self._text_has_any(combined_text, "血糖", "糖代谢", "碳水化合物代谢", "胰岛素抵抗", "2型糖尿病"):
            metabolic_score += 28
        if self._has_marker(context, "bmi", "high") or self._has_marker(context, "waist", "high"):
            metabolic_score += 26
        if "metabolic_support" in context.lifestyle_tags or "sedentary_risk" in context.lifestyle_tags:
            metabolic_score += 14
        metabolic_score += min(lipid_marker_count, 3) * 6
        if self._matches_pattern("iron_deficiency", context):
            metabolic_score += 58
        add_finding(
            system_id="endocrine_metabolic",
            base_title="内分泌/代谢系统",
            body="当前证据更偏向血糖稳定、体重/腰围、糖脂代谢或内分泌节律问题；首月应优先用餐盘结构、精制碳水控制、饭后活动和睡眠节律来验证趋势，再配合相应营养支持。",
            score=metabolic_score,
            axes=("glycemic_balance", "weight_metabolism", "thyroid_axis", "iron_repletion", "foundational"),
        )

        liver_score = 0.0
        liver_score += min(liver_marker_count, 3) * 22
        if self._has_marker(context, "uric_acid", "high"):
            liver_score += 30
        if self._text_has_any(combined_text, "脂肪肝", "肝脏", "肝胆", "解毒", "谷胱甘肽", "酒精", "饮酒"):
            liver_score += 30
        if "outside_dining" in context.lifestyle_tags or "chemical_sensitivity" in context.lifestyle_tags:
            liver_score += 12
        liver_score += min(lipid_marker_count, 3) * 6
        add_finding(
            system_id="liver_detox",
            base_title="肝脏/解毒系统",
            body="本次线索提示肝胆代谢、尿酸、脂肪肝/饮酒外食或解毒负担需要排在前面处理；干预重点是先降低输入负担，再考虑肝脏代谢、胆汁分泌和谷胱甘肽相关支持。",
            score=liver_score,
            axes=("liver_detox", "gut_bile", "antioxidant"),
        )

        cardiovascular_score = 0.0
        cardiovascular_score += min(lipid_marker_count, 3) * 16
        if self._has_marker(context, "homocysteine", "high"):
            cardiovascular_score += 36
        if self._has_marker(context, "hs_crp", "high"):
            cardiovascular_score += 24
        if self._text_has_any(combined_text, "血压", "心律", "心血管", "动脉", "卒中", "高血压", "ELOVL2"):
            cardiovascular_score += 24
        add_finding(
            system_id="cardiovascular",
            base_title="心血管系统",
            body="当前血脂、炎症、同型半胱氨酸、血压/心律或家族史线索提示需要管理血管内皮、炎症和脂质代谢压力；建议重点观察油脂质量、精制碳水、运动量、睡眠和家族风险因素。",
            score=cardiovascular_score,
            axes=("cardiovascular", "lipid_balance", "methylation", "inflammation"),
        )

        thyroid_immune_score = 0.0
        thyroid_immune_score += min(thyroid_marker_count, 2) * 26
        if self._has_thyroid_pattern(context):
            thyroid_immune_score += 32
        if self._has_marker(context, "vitamin_d", "low"):
            thyroid_immune_score += 30
        if self._has_marker(context, "hs_crp", "high"):
            thyroid_immune_score += 14
        if self._text_has_any(combined_text, "免疫", "桥本", "甲减", "甲状腺", "过敏", "反复感冒"):
            thyroid_immune_score += 12
        if self._text_has_any(combined_text, "抗衰", "端粒", "DNA甲基化", "甲基化年龄", "氧化压力"):
            thyroid_immune_score += 20
        add_finding(
            system_id="immune_inflammation",
            base_title="免疫/炎症系统",
            body="甲状腺功能、甲状腺抗体、维生素D或炎症线索会影响代谢、情绪和恢复；建议同步关注睡眠压力、炎症触发因素、维生素D状态、碘摄入方式以及后续甲状腺指标趋势。",
            score=thyroid_immune_score,
            axes=("thyroid_axis", "immune", "vitamin_d_repletion", "inflammation"),
        )

        gut_score = 0.0
        if "gut_support" in context.lifestyle_tags:
            gut_score += 28
        if self._text_has_any(combined_text, "腹胀", "腹泻", "便秘", "肠道", "菌群", "油腻不耐受", "胆汁", "胆囊", "胃酸", "消化"):
            gut_score += 28
        if "outside_dining" in context.lifestyle_tags:
            gut_score += 10
        add_finding(
            system_id="digestive_gut",
            base_title="消化系统/肠道",
            body="腹胀、排便波动、油腻不耐受、胃酸/消化或菌群线索提示消化道执行力会影响整体方案效果；建议先观察触发食物、排便规律、膳食纤维、蛋白质消化和油脂耐受。",
            score=gut_score,
            axes=("gut_bile", "gut_microbiome", "gut_mucosa", "digestive_enzyme"),
        )

        neuro_sleep_score = 0.0
        if "sleep_recovery" in context.lifestyle_tags or "stress_support" in context.lifestyle_tags:
            neuro_sleep_score += 30
        if self._text_has_any(combined_text, "睡眠", "早醒", "入睡困难", "压力", "焦虑", "紧张", "注意力", "头痛", "脑雾"):
            neuro_sleep_score += 24
        if "energy_support" in context.lifestyle_tags or self._text_has_any(combined_text, "疲劳", "线粒体", "细胞能量", "活动力下降"):
            neuro_sleep_score += 18
        add_finding(
            system_id="neuro_sleep",
            base_title="神经/认知/睡眠系统",
            body="睡眠不足、压力负荷、脑雾、注意力或疲劳提示恢复节律不足；建议先稳定起床时间、减少夜间刺激、管理咖啡因和工作压力，并观察晨起精力变化。",
            score=neuro_sleep_score,
            axes=("sleep_stress", "neuro_cognitive", "energy_mitochondria"),
        )

        female_score = 42 if self._text_has_any(combined_text, "经前", "潮热", "月经", "女性激素", "围绝经", "卵巢") else 0
        add_finding(
            system_id="reproductive_breast",
            base_title="生殖/妇科/乳腺系统",
            body="女性周期、经前不适、潮热或卵巢相关线索提示需要关注内分泌节律；涉及激素前体或特殊阶段时需医生确认。",
            score=female_score,
            axes=("female_hormone", "hormone_axis", "sleep_stress"),
            threshold=28,
        )

        add_finding(
            system_id="respiratory",
            base_title="呼吸系统",
            body="",
            score=46 if self._text_has_any(combined_text, "肺", "肺结节", "呼吸", "咳嗽", "哮喘", "弥散功能") else 0,
            axes=(),
            threshold=28,
        )
        add_finding(
            system_id="bone_muscle",
            base_title="骨骼/肌肉系统",
            body="",
            score=(38 if self._text_has_any(combined_text, "骨量", "骨质", "骨密度", "肌肉", "关节") else 0)
            + (24 if self._has_marker(context, "vitamin_d", "low") else 0),
            axes=("bone_metabolism", "vitamin_d_repletion", "energy_mitochondria"),
            threshold=28,
        )
        add_finding(
            system_id="urinary_renal",
            base_title="泌尿/肾脏系统",
            body="",
            score=42 if self._text_has_any(combined_text, "泌尿", "肾", "肾囊肿", "尿检", "肌酐", "尿素氮") else 0,
            axes=(),
            threshold=28,
        )
        add_finding(
            system_id="skin_mucosa",
            base_title="皮肤/黏膜系统",
            body="",
            score=38 if self._text_has_any(combined_text, "皮肤", "黏膜", "湿疹", "皮炎", "皮疹", "脱发", "伤口") else 0,
            axes=("gut_mucosa", "immune"),
            threshold=28,
        )

        findings_by_system = {finding.system_id: finding for finding in findings}
        for system_id, mapped_findings in context.clinical_findings_by_system.items():
            existing = findings_by_system.get(system_id)
            score = max(existing.score if existing else 0.0, min(58.0, 42.0 + len(mapped_findings) * 8.0))
            findings_by_system[system_id] = SystemPriority(
                system_id=system_id,
                title=f"{SYSTEM_NAMES[system_id]}（{self._priority_label(score)}）",
                body=build_system_summary(
                    system_id,
                    [finding.finding_name for finding in mapped_findings],
                    score,
                ),
                score=round(score, 2),
                # System-only model classification may affect report ordering, but
                # must not create product axes. Existing evidence-backed axes remain.
                axes=existing.axes if existing else (),
                priority_level=self._priority_label(score),
                finding_ids=tuple(finding.finding_id for finding in mapped_findings),
            )

        return sorted(findings_by_system.values(), key=lambda item: item.score, reverse=True)

    def _priority_label(self, score: float) -> str:
        return priority_level(score)

    def _system_evidence_names(
        self,
        context: RecommendationContext,
        system_id: str,
    ) -> list[str]:
        evidence: list[str] = []
        flag_labels = {"high": "偏高", "low": "偏低", "unknown": "异常"}
        for observations in context.markers_by_code.values():
            for item in observations:
                marker_name = getattr(item, "marker_name", "") or getattr(item, "marker_code", "")
                if system_id not in classify_text_to_system_ids(marker_name, getattr(item, "marker_code", "")):
                    continue
                flag = getattr(getattr(item, "abnormal_flag", None), "value", "unknown")
                evidence.append(f"{marker_name}{flag_labels.get(flag, '异常')}")
        for value in [*context.conditions, *context.symptoms, *context.chief_concerns]:
            if system_id in classify_text_to_system_ids(value):
                evidence.append(value)
        evidence.extend(
            finding.finding_name
            for finding in context.clinical_findings_by_system.get(system_id, [])
        )
        return list(dict.fromkeys(evidence))[:6]

    def _marker_hit_count(
        self,
        context: RecommendationContext,
        marker_codes: tuple[str, ...],
        abnormal_flag: str,
    ) -> int:
        return sum(1 for marker_code in marker_codes if self._has_marker(context, marker_code, abnormal_flag))

    def _score_product_from_tag_matrix(
        self,
        product: ProductRule,
        context: RecommendationContext,
        priority_findings: list[SystemPriority] | None = None,
    ) -> tuple[float, list[str]]:
        profile = self.product_tag_profiles.get(product.sku_id)
        capability = self.product_capabilities.get(product.sku_id)
        if not profile or not capability:
            return 0.0, []
        primary_goals = tuple(capability.get("primary_goal_codes") or ())
        secondary_goals = tuple(capability.get("secondary_goal_codes") or ())
        primary_hits = [
            (
                goal,
                self._directionally_eligible_findings(
                    capability,
                    goal,
                    context.support_goal_findings[goal],
                ),
            )
            for goal in primary_goals
            if goal in context.support_goal_findings
            and self._directionally_eligible_findings(
                capability,
                goal,
                context.support_goal_findings[goal],
            )
        ]
        secondary_hits = [
            (
                goal,
                self._directionally_eligible_findings(
                    capability,
                    goal,
                    context.support_goal_findings[goal],
                ),
            )
            for goal in secondary_goals
            if goal in context.support_goal_findings
            and self._directionally_eligible_findings(
                capability,
                goal,
                context.support_goal_findings[goal],
            )
        ]
        marker_hits = self._matched_marker_tags(profile.marker_tags, context)
        # Secondary capabilities may enrich a reason after a primary/marker match,
        # but must never activate a product by themselves.
        if not primary_hits and not marker_hits:
            return 0.0, []

        association = 0.0
        evidence_ids: list[str] = []
        primary_evidence_class = ClinicalEvidenceClass.symptom
        if primary_hits:
            best_goal, matched_findings = max(
                primary_hits,
                key=lambda item: self._support_finding_group_rank(item[1]),
            )
            confidence = max(finding.mapping_confidence for finding in matched_findings)
            primary_evidence_class = self._strongest_support_finding_class(matched_findings)
            primary_base = {
                ClinicalEvidenceClass.lab_abnormal: 68.0,
                ClinicalEvidenceClass.clinical_confirmed: 66.0,
                ClinicalEvidenceClass.symptom: 52.0,
                ClinicalEvidenceClass.exposure: 38.0,
                ClinicalEvidenceClass.genetic_risk: 0.0,
                ClinicalEvidenceClass.follow_up_only: 0.0,
            }[primary_evidence_class]
            association += primary_base + 6 * confidence
            evidence_ids.append(f"signal:model_support_goal_{best_goal}")
            evidence_ids.append(f"signal:primary_support_goal_{best_goal}")
            evidence_ids.append(f"signal:evidence_class_{primary_evidence_class.value}")
            goal_config = self.support_goals.get(best_goal, {})
            if goal_config.get("must_cover_when_direct") and primary_evidence_class in {
                ClinicalEvidenceClass.lab_abnormal,
                ClinicalEvidenceClass.clinical_confirmed,
            }:
                evidence_ids.append(f"signal:must_cover_goal_{best_goal}")
            evidence_ids.extend(f"finding:{finding.finding_id}" for finding in matched_findings[:3])
        if secondary_hits:
            best_goal, matched_findings = max(
                secondary_hits,
                key=lambda item: self._support_finding_group_rank(item[1]),
            )
            confidence = max(finding.mapping_confidence for finding in matched_findings)
            secondary_class = self._strongest_support_finding_class(matched_findings)
            secondary_bonus = {
                ClinicalEvidenceClass.lab_abnormal: 12.0,
                ClinicalEvidenceClass.clinical_confirmed: 12.0,
                ClinicalEvidenceClass.symptom: 8.0,
                ClinicalEvidenceClass.exposure: 4.0,
                ClinicalEvidenceClass.genetic_risk: 0.0,
                ClinicalEvidenceClass.follow_up_only: 0.0,
            }[secondary_class]
            association += secondary_bonus + 2 * confidence
            evidence_ids.append(f"signal:model_support_goal_{best_goal}")
            evidence_ids.extend(f"finding:{finding.finding_id}" for finding in matched_findings[:2])
        if marker_hits:
            association += min(25, len(marker_hits) * 15)
            for marker_code in marker_hits[:2]:
                evidence_ids.append(f"signal:tag_marker_{marker_code}")
            for goal in primary_goals:
                if self.support_goals.get(goal, {}).get("must_cover_when_direct"):
                    evidence_ids.append(f"signal:primary_support_goal_{goal}")
                    evidence_ids.append(f"signal:must_cover_goal_{goal}")
        if profile.precision_level == "precise":
            association += 8
        elif profile.precision_level == "adjunct":
            association += 2
        elif profile.precision_level == "requires_confirmation":
            association = min(association, 70)
            evidence_ids.append("signal:requires_manual_confirmation")

        if profile.precision_level == "adjunct":
            association = min(association, 88)
        elif profile.precision_level == "general_support":
            association = min(association, 82 if marker_hits else 72)
        if primary_evidence_class == ClinicalEvidenceClass.exposure:
            association = min(association, 65)

        association_percent = max(1, min(95, int(round(association))))

        evidence_ids.insert(0, f"signal:association_{association_percent}")
        matched_goals = {goal for goal, _ in [*primary_hits, *secondary_hits]}
        system_scores = [
            item.score
            for item in (priority_findings or [])
            if matched_goals.intersection(item.axes)
        ]
        if system_scores:
            evidence_ids.insert(1, f"signal:system_priority_{int(max(system_scores))}")
        score = association_percent / 100 * 2.2
        return round(score, 3), list(dict.fromkeys(evidence_ids))

    @staticmethod
    def _strongest_support_finding_class(findings: list) -> ClinicalEvidenceClass:
        priority = {
            ClinicalEvidenceClass.lab_abnormal: 6,
            ClinicalEvidenceClass.clinical_confirmed: 5,
            ClinicalEvidenceClass.symptom: 4,
            ClinicalEvidenceClass.exposure: 3,
            ClinicalEvidenceClass.genetic_risk: 2,
            ClinicalEvidenceClass.follow_up_only: 1,
        }
        classes = [classify_confirmed_evidence(finding) for finding in findings]
        return max(classes, key=lambda item: priority[item], default=ClinicalEvidenceClass.symptom)

    @classmethod
    def _support_finding_group_rank(cls, findings: list) -> tuple[int, float, int]:
        evidence_class = cls._strongest_support_finding_class(findings)
        priority = {
            ClinicalEvidenceClass.lab_abnormal: 6,
            ClinicalEvidenceClass.clinical_confirmed: 5,
            ClinicalEvidenceClass.symptom: 4,
            ClinicalEvidenceClass.exposure: 3,
            ClinicalEvidenceClass.genetic_risk: 2,
            ClinicalEvidenceClass.follow_up_only: 1,
        }
        confidence = max((finding.mapping_confidence for finding in findings), default=0.0)
        return priority[evidence_class], confidence, len(findings)

    @staticmethod
    def _directionally_eligible_findings(
        capability: dict,
        goal: str,
        findings: list,
    ) -> list:
        requirements = capability.get("goal_direction_requirements") or {}
        allowed = {str(value) for value in requirements.get(goal, [])}
        if not allowed:
            return findings
        return [
            finding
            for finding in findings
            if getattr(
                getattr(finding, "support_direction", None),
                "value",
                getattr(finding, "support_direction", "unknown"),
            )
            in allowed
        ]

    def _matched_marker_tags(self, marker_tags: tuple[str, ...], context: RecommendationContext) -> list[str]:
        matched: list[str] = []
        for tag in marker_tags:
            marker_code, _, flag = tag.partition(":")
            marker_code = marker_code.strip()
            flag = flag.strip()
            if marker_code and self._has_marker(context, marker_code, flag or "high"):
                matched.append(marker_code)
        return list(dict.fromkeys(matched))

    def _matched_text_tags(self, tags: tuple[str, ...], normalized_values: set[str], pattern_text: str) -> list[str]:
        matched: list[str] = []
        for tag in tags:
            normalized_tag = self._normalize(tag)
            if not normalized_tag:
                continue
            if normalized_tag in pattern_text or any(
                normalized_tag in value or value in normalized_tag for value in normalized_values if value
            ):
                matched.append(tag)
        return list(dict.fromkeys(matched))

    def _score_product_from_clinical_patterns(
        self,
        product: ProductRule,
        context: RecommendationContext,
    ) -> tuple[float, list[str]]:
        score = 0.0
        evidence_ids: list[str] = []
        sku_id = product.sku_id

        if sku_id in {"sku_liver_detox_support", "sku_amino_acid_detox"} and self._has_liver_metabolic_pattern(context):
            score += 0.82
            evidence_ids.append("signal:clinical_liver_detox_axis")
        if sku_id == "sku_thyroid_support" and self._has_thyroid_pattern(context):
            score += 0.62
            evidence_ids.append("signal:clinical_thyroid_axis")
        if sku_id == "sku_fish_oil_rtg" and self._has_cardiovascular_pattern(context):
            score += 0.58
            evidence_ids.append("signal:clinical_cardiometabolic_axis")
        if sku_id == "sku_magnesium_glycinate" and self._has_neuro_sleep_pattern(context):
            score += 0.64
            evidence_ids.append("signal:clinical_sleep_stress_axis")
        if sku_id == "sku_bile_flow_support" and self._has_gut_or_bile_pattern(context):
            score += 0.46
            evidence_ids.append("signal:clinical_gut_bile_axis")

        return round(score, 3), evidence_ids

    def _score_product_from_summary_nutrients(
        self,
        product: ProductRule,
        nutrient_hints: list[str],
    ) -> tuple[float, list[str]]:
        if not nutrient_hints:
            return 0.0, []

        searchable_text = self._normalize(
            " ".join(
                [
                    product.sku_id,
                    product.display_name,
                    product.category,
                    product.formula_summary,
                    " ".join(product.core_ingredients),
                    " ".join(product.candidate_use_cases),
                    " ".join(product.indications),
                ]
            )
        )
        matched_hint_count = 0
        for hint in nutrient_hints:
            variants = self._expand_summary_nutrient_variants(hint)
            if any(self._normalize(variant) in searchable_text for variant in variants):
                matched_hint_count += 1

        if matched_hint_count == 0:
            return 0.0, []

        score = min(matched_hint_count, 5) * 0.22
        return round(score, 3), ["signal:summary_nutrient_support"]

    def _expand_summary_nutrient_variants(self, hint: str) -> tuple[str, ...]:
        normalized_hint = self._normalize(hint)
        aliases = {
            "蛋白质": ("蛋白质", "蛋白", "氨基酸"),
            "肉碱": ("肉碱", "左旋肉碱", "乙酰左旋肉碱"),
            "精氨酸": ("精氨酸", "l-精氨酸", "锌精氨酸"),
            "谷氨酰胺": ("谷氨酰胺", "l-谷氨酰胺"),
            "组氨酸": ("组氨酸", "l-组氨酸"),
            "脯氨酸": ("脯氨酸", "l-脯氨酸"),
            "谷氨酸": ("谷氨酸",),
            "维生素": ("维生素", "多维", "多维矿", "b族"),
            "b1": ("b1", "维生素b1", "硫胺素"),
            "硫胺素": ("硫胺素", "维生素b1", "b1"),
            "b2": ("b2", "维生素b2", "核黄素"),
            "核黄素": ("核黄素", "维生素b2", "b2"),
            "b3": ("b3", "维生素b3", "烟酸"),
            "烟酸": ("烟酸", "维生素b3", "b3"),
            "b5": ("b5", "维生素b5", "泛酸"),
            "泛酸": ("泛酸", "维生素b5", "b5"),
            "b6": ("b6", "维生素b6", "吡哆醇"),
            "吡哆醇": ("吡哆醇", "维生素b6", "b6"),
            "b12": ("b12", "维生素b12", "钴胺素"),
            "叶酸": ("叶酸", "folate", "甲基叶酸"),
            "生物素": ("生物素", "biotin"),
            "矿物质": ("矿物质", "矿", "镁", "锌", "硒", "钙", "钾", "铜", "锰", "钼", "碘"),
        }
        return aliases.get(normalized_hint, (hint,))

    def _summary_hints_include(self, hints: list[str], *keywords: str) -> bool:
        normalized_hints = [self._normalize(item) for item in hints if item]
        normalized_keywords = [self._normalize(item) for item in keywords if item]
        return any(
            keyword in hint or hint in keyword
            for keyword in normalized_keywords
            for hint in normalized_hints
        )

    def _build_evidence_details(
        self,
        evidence_ids: list[str],
        *,
        product_by_id: dict[str, ProductRule],
        knowledge_by_id: dict[str, object],
        clinician_rule_by_id: dict[str, ClinicianRule],
    ) -> list[str]:
        details: list[str] = []
        for evidence_id in evidence_ids:
            detail = self._format_evidence_detail(
                evidence_id,
                product_by_id=product_by_id,
                knowledge_by_id=knowledge_by_id,
                clinician_rule_by_id=clinician_rule_by_id,
            )
            if detail:
                details.append(detail)
        return list(dict.fromkeys(details))

    def _format_evidence_detail(
        self,
        evidence_id: str,
        *,
        product_by_id: dict[str, ProductRule],
        knowledge_by_id: dict[str, object],
        clinician_rule_by_id: dict[str, ClinicianRule],
    ) -> str:
        if evidence_id.startswith("product:"):
            sku_id = evidence_id.split(":", 1)[1]
            product = product_by_id.get(sku_id)
            if not product:
                return None
            return f"产品规则：{product.display_name}"

        if evidence_id.startswith("signal:"):
            signal_id = evidence_id.split(":", 1)[1]
            return f"信号命中：{self._signal_label(signal_id)}"

        if evidence_id.startswith("clinician_rule:"):
            rule_id = evidence_id.split(":", 1)[1]
            rule = clinician_rule_by_id.get(rule_id)
            if not rule:
                return None
            return f"医生规则：{rule.title}"

        statement = knowledge_by_id.get(evidence_id)
        if not statement:
            return None

        return f"已审核知识：{statement.topic}"

    def _signal_label(self, signal_id: str) -> str:
        axis_labels = {
            "anti_aging": "抗衰/氧化压力轴",
            "antioxidant": "抗氧化轴",
            "bone_metabolism": "骨代谢轴",
            "cardiovascular": "心血管系统",
            "digestive_enzyme": "消化酶/胃酸支持",
            "energy_mitochondria": "细胞能量/线粒体系统",
            "female_hormone": "女性激素系统",
            "foundational": "基础营养支持",
            "glycemic_balance": "血糖/碳水代谢系统",
            "gut_bile": "肠道/胆汁系统",
            "gut_microbiome": "肠道菌群系统",
            "gut_mucosa": "胃肠黏膜屏障",
            "hormone_axis": "内分泌激素轴",
            "immune": "免疫系统",
            "inflammation": "炎症系统",
            "iron_repletion": "缺铁/造血支持",
            "lipid_balance": "血脂代谢系统",
            "liver_detox": "肝脏/解毒系统",
            "methylation": "甲基化/HCY系统",
            "neuro_cognitive": "神经认知系统",
            "sleep_stress": "睡眠/压力系统",
            "thyroid_axis": "甲状腺系统",
            "vitamin_d_repletion": "维生素D补充",
            "weight_metabolism": "体重/脂肪代谢系统",
            "nutrition_repletion": "体重过轻与营养恢复支持",
        }
        if signal_id.startswith("association_"):
            return f"关联度：{signal_id.rsplit('_', 1)[-1]}%"
        if signal_id.startswith("system_priority_"):
            return f"系统优先级：{signal_id.rsplit('_', 1)[-1]}%"
        if signal_id.startswith("tag_axis_"):
            axis = signal_id.replace("tag_axis_", "", 1)
            return f"产品标签命中：{axis_labels.get(axis, axis)}"
        if signal_id.startswith("tag_marker_"):
            marker_code = signal_id.replace("tag_marker_", "", 1)
            return f"产品标签命中：{marker_code} 异常"
        if signal_id.startswith("model_support_goal_"):
            axis = signal_id.replace("model_support_goal_", "", 1)
            return f"医生确认异常的营养支持方向：{axis_labels.get(axis, axis)}"
        if signal_id == "top_system_primary_axis":
            return "最高优先系统主轴匹配"
        if signal_id == "tag_context_match":
            return "产品标签命中：症状/目标/生活方式"
        if signal_id == "tag_symptom_match":
            return "产品标签命中：症状"
        if signal_id == "tag_condition_match":
            return "产品标签命中：既往病史/问题"
        if signal_id == "tag_goal_match":
            return "产品标签命中：健康目标"
        if signal_id == "tag_lifestyle_match":
            return "产品标签命中：生活方式"
        if signal_id == "tag_mechanism_match":
            return "产品标签命中：机制关键词"
        if signal_id == "direct_product_rule":
            return "产品目录规则直接命中"
        if signal_id == "requires_manual_confirmation":
            return "该产品需要医生人工确认后使用"
        labels = {
            "lipid_balance": "血脂与心血管支持",
            "iron_repletion": "缺铁与造血支持",
            "glycemic_balance": "血糖与代谢支持",
            "inflammation_resolution": "炎症与恢复支持",
            "vitamin_d_repletion": "维生素D补充支持",
            "thyroid_axis": "甲状腺轴支持",
            "methylation": "甲基化与心血管支持",
            "liver_detox": "肝胆与解毒支持",
            "sleep_stress": "睡眠与压力恢复",
            "cellular_energy": "细胞能量与线粒体支持",
            "summary_amino_support": "病例总结所需营养素：蛋白质与氨基酸",
            "summary_vitamin_support": "病例总结所需营养素：维生素与B族",
            "summary_mineral_support": "病例总结所需营养素：矿物质",
            "summary_nutrient_support": "病例总结所需营养素提示",
            "case_context_match": "病例上下文匹配",
        }
        return labels.get(signal_id, signal_id)

    def _finalize_lifestyle_actions(self, actions: list[str], knowledge_hits, context: RecommendationContext) -> list[str]:
        chinese_actions = [
            item.strip()
            for item in actions
            if isinstance(item, str) and item.strip() and self._contains_cjk(item)
        ]
        local_actions = self._build_local_lifestyle_actions(knowledge_hits, context)
        return list(dict.fromkeys(chinese_actions + local_actions))[:8]

    def _build_local_lifestyle_actions(self, knowledge_hits, context: RecommendationContext) -> list[str]:
        actions: list[str] = []
        for hit in knowledge_hits[:4]:
            actions.extend(hit.statement.lifestyle_actions)

        if "sleep_recovery" in context.lifestyle_tags:
            actions.append("固定每日入睡和起床时间，睡前 1-2 小时减少咖啡因、酒精和电子屏幕暴露。")

        for items in context.markers_by_code.values():
            for item in items:
                if item.marker_code == "ferritin" and item.abnormal_flag.value == "low":
                    actions.append(
                        "铁蛋白偏低时，建议结合医生评估缺铁风险；在确认适用后增加红肉、贝类、动物肝、豆类和深色叶菜，并搭配维生素 C 促进吸收。"
                    )
                if item.marker_code == "magnesium" and item.abnormal_flag.value == "high":
                    actions.append(
                        "血清镁偏高时，当前阶段避免额外叠加非处方镁补充剂，并结合肾功能、补剂使用史和近期输液情况做人工复核。"
                    )
                if item.marker_code in {"thyroglobulin_antibody", "thyroid_peroxidase_antibody"} and item.abnormal_flag.value == "high":
                    actions.append("甲状腺抗体升高时，优先保持规律作息并减少长期高压暴露，暂不建议自行叠加高碘来源。")

        if not actions:
            actions = [
                "围绕睡眠、压力、运动和饮食一致性先做基础生活方式干预。",
                "若用药或过敏信息尚不明确，先补齐信息后再升级方案。",
            ]

        return list(dict.fromkeys(actions))[:6]

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
            return self._contains_normalized_value(value, context.goals)
        if kind == "symptom":
            return self._contains_normalized_value(value, context.symptoms)
        if kind == "condition":
            return self._contains_normalized_value(value, context.conditions)
        if kind == "finding":
            # Safety facts use exact, locally validated clinical codes. This is a
            # finite product-safety check, not free-text medical keyword matching.
            return value in context.clinical_findings_by_code
        if kind == "med":
            return self._contains_normalized_value(value, context.medications)
        if kind == "allergy":
            return self._contains_normalized_value(value, context.allergies)
        if kind == "lifestyle":
            return value in context.lifestyle_tags
        if kind == "pregnancy":
            return context.pregnancy
        if kind == "pattern":
            return self._matches_pattern(value, context)
        return False

    def _contains_normalized_value(self, expected: str, values: set[str]) -> bool:
        if not expected:
            return False
        return any(
            expected == value or expected in value or value in expected
            for value in values
            if value
        )

    def _matches_pattern(self, pattern_name: str, context: RecommendationContext) -> bool:
        if pattern_name == "iron_deficiency":
            ferritin_low = self._has_marker(context, "ferritin", "low")
            serum_iron_low = self._has_marker(context, "serum_iron", "low")
            hemoglobin_low = self._has_marker(context, "hemoglobin", "low")
            hematocrit_low = self._has_marker(context, "hematocrit", "low")
            mcv_low = self._has_marker(context, "mcv", "low")
            return ferritin_low or (serum_iron_low and (hemoglobin_low or hematocrit_low or mcv_low)) or (
                hemoglobin_low and mcv_low
            )

        if pattern_name == "lipid_disorder":
            return any(
                self._has_marker(context, marker_code, "high")
                for marker_code in ("ldl_c", "non_hdl_c", "total_cholesterol", "triglycerides", "apolipoprotein_b")
            )

        return False

    def _pattern_text(self, context: RecommendationContext) -> str:
        marker_parts = []
        for items in context.markers_by_code.values():
            for item in items:
                marker_parts.extend(
                    [
                        getattr(item, "marker_code", ""),
                        getattr(item, "marker_name", ""),
                        getattr(getattr(item, "abnormal_flag", None), "value", ""),
                    ]
                )
        parts = [
            context.clinical_summary_text,
            " ".join(context.goals),
            " ".join(context.chief_concerns),
            " ".join(context.symptoms),
            " ".join(context.conditions),
            " ".join(context.family_history),
            " ".join(context.lifestyle_tags),
            " ".join(marker_parts),
        ]
        return self._normalize(" ".join(part for part in parts if part))

    def _text_has_any(self, text: str, *terms: str) -> bool:
        return any(self._normalize(term) in text for term in terms if term)

    def _has_liver_metabolic_pattern(self, context: RecommendationContext) -> bool:
        text = self._pattern_text(context)
        return bool(
            self._has_marker(context, "uric_acid", "high")
            or any(self._has_marker(context, marker_code, "high") for marker_code in ("alt", "ast", "ggt"))
            or self._matches_pattern("lipid_disorder", context)
            or self._text_has_any(text, "尿酸", "脂肪肝", "肝脏", "肝胆", "解毒", "谷胱甘肽", "酒精", "饮酒")
        )

    def _has_cardiovascular_pattern(self, context: RecommendationContext) -> bool:
        text = self._pattern_text(context)
        return bool(
            self._matches_pattern("lipid_disorder", context)
            or self._has_marker(context, "homocysteine", "high")
            or self._has_marker(context, "hs_crp", "high")
            or self._text_has_any(text, "血压", "心律", "心血管", "动脉", "卒中", "高血压", "ELOVL2")
        )

    def _has_gut_or_bile_pattern(self, context: RecommendationContext) -> bool:
        text = self._pattern_text(context)
        return bool(
            "gut_support" in context.lifestyle_tags
            or self._text_has_any(text, "腹胀", "腹泻", "便秘", "肠道", "菌群", "油腻不耐受", "胆汁", "胆囊")
        )

    def _has_neuro_sleep_pattern(self, context: RecommendationContext) -> bool:
        text = self._pattern_text(context)
        return bool(
            "sleep_recovery" in context.lifestyle_tags
            or "stress_support" in context.lifestyle_tags
            or self._text_has_any(text, "睡眠", "早醒", "入睡困难", "压力", "焦虑", "紧张", "疲劳", "注意力", "头痛", "HPA")
        )

    def _build_product_reason(self, product: ProductRule, evidence_ids: list[str]) -> str:
        use_case = "、".join(product.candidate_use_cases[:2]) if product.candidate_use_cases else "当前病例目标"
        association_percent = self._association_percent(evidence_ids)
        profile = self.product_tag_profiles.get(product.sku_id)
        system_name = (
            SYSTEM_NAMES.get(profile.primary_system_ids[0], "")
            if profile and profile.primary_system_ids
            else ""
        )
        axis_labels = [
            self._signal_label(evidence_id.split(":", 1)[1])
            for evidence_id in evidence_ids
            if evidence_id.startswith("signal:tag_axis_")
        ]
        if association_percent:
            axis_text = f"，命中{ '、'.join(list(dict.fromkeys(axis_labels))[:2]) }" if axis_labels else ""
            system_text = f"，用于支持{system_name}" if system_name else ""
            return f"关联度约 {association_percent}%：结合 {use_case}{axis_text}{system_text}，作为当前阶段的候选推荐。"
        if evidence_ids:
            return f"结合 {use_case} 与已审核知识命中，作为当前阶段的候选推荐。"
        return f"结合 {use_case} 和现有产品适配规则，作为当前阶段的候选推荐。"

    def _prefer_chinese_text(self, candidate: str | None, fallback: str) -> str:
        if candidate and self._contains_cjk(candidate):
            return candidate.strip()
        return fallback

    def _sanitize_reason_text(self, text: str) -> str:
        cleaned = re.sub(r"product:sku_[a-z0-9_]+", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"statement_[a-z0-9_]+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("与本地证据", "结合已审核知识")
        cleaned = cleaned.replace("本地证据", "已审核知识")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.replace(" ,", "，").replace(" .", "。")
        cleaned = cleaned.strip(" ，。；")
        return cleaned + ("。" if cleaned and cleaned[-1] not in "。！？" else "")

    def _sanitize_report_line(self, text: str) -> str:
        cleaned = re.sub(r"product:sku_[a-z0-9_]+", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"statement_[a-z0-9_]+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("与本地证据", "结合已审核知识")
        cleaned = cleaned.replace("本地证据", "已审核知识")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" ，。；")

    def _retrieve_safe_rag_hits(
        self,
        case,
        *,
        context: RecommendationContext,
        support_profiles: list[SupportProfile],
        key_lab_highlights: list[str],
        report_guidance: list[str],
        red_flags: list[str],
        contraindications: list[str],
    ) -> tuple[list[SafeRagHit], list[str]]:
        if not self.rag_retriever:
            return [], ["rag_unavailable"]

        retrieval_query = " ".join(
            part
            for part in (
                self._build_query(case, context, support_profiles),
                " ".join(key_lab_highlights[:6]),
                " ".join(report_guidance[:4]),
            )
            if part
        ).strip()
        if not retrieval_query:
            return [], ["rag_empty_query"]

        raw_hits = []
        seen_chunk_ids: set[str] = set()
        retrieval_failures: list[str] = []
        for query in self._build_rag_report_queries(case, retrieval_query, key_lab_highlights, report_guidance):
            try:
                for hit in self.rag_retriever.hybrid_search(query, top_k=10):
                    chunk_id = str(getattr(hit, "chunk_id", ""))
                    if chunk_id and chunk_id in seen_chunk_ids:
                        continue
                    if chunk_id:
                        seen_chunk_ids.add(chunk_id)
                    raw_hits.append(hit)
            except Exception as exc:
                cause = getattr(exc, "__cause__", None)
                cause_name = cause.__class__.__name__ if cause is not None else ""
                cause_text = str(cause or "")[:160].replace("\n", " ").replace("\r", " ")
                detail = f":{cause_name}" if cause_name else ""
                if cause_text:
                    detail = f"{detail}:{cause_text}"
                retrieval_failures.append(f"rag_query_failed:{exc.__class__.__name__}{detail}")
                continue
        if not raw_hits and retrieval_failures:
            return [], retrieval_failures[:4]

        safety_filter = RagSafetyFilter(self._list_products(enabled_only=True))
        safe_hits, rejections = safety_filter.filter_hits(
            list(raw_hits),
            context=context,
            red_flags=red_flags,
            contraindications=contraindications,
            max_hits=16,
        )
        audit = [f"rag_accepted:{len(safe_hits)}"]
        audit.extend(retrieval_failures[:4])
        audit.extend(f"rag_rejected:{item.chunk_id}:{item.reason}" for item in rejections[:12])
        return safe_hits, audit

    def _build_rag_report_queries(
        self,
        case,
        base_query: str,
        key_lab_highlights: list[str],
        report_guidance: list[str],
    ) -> list[str]:
        questionnaire = getattr(case, "questionnaire", None)
        symptoms = " ".join(getattr(questionnaire, "symptoms", []) or []) if questionnaire else ""
        goals = " ".join(getattr(questionnaire, "goals", []) or []) if questionnaire else ""
        conditions = " ".join(getattr(questionnaire, "known_conditions", []) or []) if questionnaire else ""
        lifestyle_context = " ".join(part for part in (symptoms, goals, conditions) if part).strip()
        lab_context = " ".join(key_lab_highlights[:6])
        guidance_context = " ".join(report_guidance[:4])

        queries = [base_query]
        if lifestyle_context:
            queries.append(
                f"{lifestyle_context} 睡眠 压力 运动 饮食 作息 生活方式 功能医学"
            )
        if lab_context:
            queries.append(f"{lab_context} 复查 趋势 随访 功能医学 指标解释")
            queries.append(f"{lab_context} 总体健康画像 系统关联 功能医学 代谢 免疫 炎症")
            queries.append(f"{lab_context} 甲状腺 HPT轴 抗体 TSH FT3 FT4 症状 趋势")
            queries.append(f"{lab_context} 生活方式 睡眠 压力 运动 饮食 久坐 恢复")
        if guidance_context and guidance_context not in base_query:
            queries.append(f"{guidance_context} 功能医学 报告解释")

        deduped = []
        seen = set()
        for query in queries:
            cleaned = re.sub(r"\s+", " ", query).strip()
            if len(cleaned) > 700:
                cleaned = cleaned[:700].rstrip(" ，。；")
            if not cleaned or cleaned in seen:
                continue
            deduped.append(cleaned)
            seen.add(cleaned)
        return deduped[:8]

    def _apply_rag_enhancements(
        self,
        report_sections: dict[str, list[str] | str],
        rag_hits: list[SafeRagHit],
        rag_audit: list[str],
    ) -> dict[str, list[str] | str]:
        merged = dict(report_sections)
        merged["RAG内部审查"] = rag_audit

        if not rag_hits:
            return merged

        used_chunk_ids: set[str] = set()
        used_signatures: list[str] = []

        def add_unique_rag_items(section: str, predicate, formatter, *, limit: int = 3) -> int:
            added = 0
            for hit in rag_hits:
                if added >= limit:
                    break
                if not predicate(hit):
                    continue
                line = formatter(hit.excerpt)
                if not line:
                    continue
                if self._rag_hit_already_used(hit, line, used_chunk_ids, used_signatures):
                    continue
                self._remember_rag_hit(hit, line, used_chunk_ids, used_signatures)
                self._append_report_items(merged, section, [line])
                added += 1
            return added

        # First reserve at least one suitable item for each report target. This avoids
        # a broad section such as health portrait consuming a lifestyle-specific hit.
        add_unique_rag_items(
            "RAG异常指标解释",
            self._rag_hit_supports_indicator_explanation,
            self._format_rag_public_line,
            limit=1,
        )
        add_unique_rag_items("RAG生活方式干预", self._rag_hit_supports_lifestyle, self._format_rag_public_line, limit=1)
        add_unique_rag_items(
            "RAG复查建议",
            self._rag_hit_supports_follow_up,
            self._format_rag_followup_line,
            limit=1,
        )
        add_unique_rag_items("RAG总体健康画像", self._rag_hit_supports_health_portrait, self._format_rag_public_line, limit=1)

        add_unique_rag_items(
            "RAG异常指标解释",
            self._rag_hit_supports_indicator_explanation,
            self._format_rag_public_line,
            limit=2,
        )
        add_unique_rag_items("RAG总体健康画像", self._rag_hit_supports_health_portrait, self._format_rag_public_line, limit=2)
        add_unique_rag_items("RAG生活方式干预", self._rag_hit_supports_lifestyle, self._format_rag_public_line, limit=2)
        add_unique_rag_items(
            "RAG复查建议",
            self._rag_hit_supports_follow_up,
            self._format_rag_followup_line,
            limit=2,
        )
        return merged

    def _rag_hit_already_used(
        self,
        hit: SafeRagHit,
        line: str,
        used_chunk_ids: set[str],
        used_signatures: list[str],
    ) -> bool:
        if hit.chunk_id in used_chunk_ids:
            return True
        candidate = self._rag_text_signature(line)
        return self._rag_signature_seen(candidate, used_signatures)

    def _remember_rag_hit(
        self,
        hit: SafeRagHit,
        line: str,
        used_chunk_ids: set[str],
        used_signatures: list[str],
    ) -> None:
        used_chunk_ids.add(hit.chunk_id)
        signature = self._rag_text_signature(line)
        if signature:
            used_signatures.append(signature)

    def _rag_text_signature(self, text: str) -> str:
        cleaned = self._sanitize_report_line(text).replace(CUSTOMER_RAG_PREFIX, "")
        cleaned = re.sub(r"；这部分仅作为营养支持背景说明.*$", "", cleaned)
        cleaned = re.sub(r"具体补充剂、禁忌和复查安排仍以医生审核与产品规则为准.*$", "", cleaned)
        cleaned = re.sub(r"[\s，。；、：:（）()“”\"'`]+", "", cleaned)
        return cleaned.lower()

    def _rag_signature_seen(self, candidate: str, used_signatures: list[str]) -> bool:
        if not candidate:
            return True
        for existing in used_signatures:
            if candidate == existing:
                return True
            shorter, longer = sorted((candidate, existing), key=len)
            if len(shorter) >= 24 and shorter in longer:
                return True
            if self._char_bigram_jaccard(candidate, existing) >= 0.82:
                return True
        return False

    def _char_bigram_jaccard(self, left: str, right: str) -> float:
        if len(left) < 12 or len(right) < 12:
            return 0.0
        left_pairs = {left[index : index + 2] for index in range(len(left) - 1)}
        right_pairs = {right[index : index + 2] for index in range(len(right) - 1)}
        if not left_pairs or not right_pairs:
            return 0.0
        return len(left_pairs & right_pairs) / len(left_pairs | right_pairs)

    def _format_rag_public_line(self, excerpt: str) -> str:
        cleaned = self._sanitize_report_line(excerpt)
        cleaned = re.sub(r"\b(docx|pdf|page|chunk|source)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；")
        if not cleaned:
            return ""
        return f"{CUSTOMER_RAG_PREFIX}{cleaned}。"

    def _format_rag_nutrition_context_line(self, excerpt: str) -> str:
        cleaned = self._sanitize_report_line(excerpt)
        if not cleaned:
            return ""
        return (
            f"{CUSTOMER_RAG_PREFIX}{cleaned}；这部分仅作为营养支持背景说明，"
            "不改变当前产品目录、剂量或禁忌审核结论。"
        )

    def _format_rag_followup_line(self, excerpt: str) -> str:
        normalized = self._normalize(excerpt)
        if any(term in normalized for term in ("甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab", "桥本")):
            return (
                f"{CUSTOMER_RAG_PREFIX}复查时建议把甲状腺功能、抗体变化、症状和睡眠压力状态放在同一趋势里观察。"
            )
        if any(term in normalized for term in ("血糖", "胰岛素", "hba1c", "代谢", "甘油三酯", "hdl", "ldl")):
            return (
                f"{CUSTOMER_RAG_PREFIX}复查时建议结合血糖、胰岛素或血脂趋势，而不是只看单次结果。"
            )
        if any(term in normalized for term in ("维生素d", "免疫", "炎症", "crp")):
            return (
                f"{CUSTOMER_RAG_PREFIX}复查时建议结合维生素D、炎症指标和症状变化，评估恢复方向是否稳定。"
            )
        if any(term in normalized for term in ("睡眠", "压力", "皮质醇", "hpa", "疲劳")):
            return (
                f"{CUSTOMER_RAG_PREFIX}复查时建议同步记录睡眠时长、晨起精力和压力恢复情况。"
            )
        return ""

    def _rag_hit_supports_indicator_explanation(self, hit: SafeRagHit) -> bool:
        excerpt = self._normalize(hit.excerpt)
        return any(
            term in excerpt
            for term in (
                "指标",
                "抗体",
                "tsh",
                "tpo",
                "tgab",
                "血糖",
                "胰岛素",
                "甘油三酯",
                "crp",
                "维生素d",
                "hdl",
                "ldl",
            )
        )

    def _rag_hit_supports_health_portrait(self, hit: SafeRagHit) -> bool:
        excerpt = self._normalize(hit.excerpt)
        if any(
            term in excerpt
            for term in (
                "tpoab",
                "tgab",
                "甲状腺过氧化物酶抗体",
                "甲状腺球蛋白抗体",
            )
        ):
            return False
        broad_context_terms = (
            "代谢",
            "胰岛素",
            "血糖",
            "甲状腺",
            "维生素d",
            "免疫",
            "炎症",
            "肠道",
            "hpa",
            "皮质醇",
        )
        return any(term in excerpt for term in broad_context_terms)

    def _rag_hit_supports_nutrition_context(self, hit: SafeRagHit) -> bool:
        tags = {self._normalize(item) for item in hit.topic_tags}
        excerpt = self._normalize(hit.excerpt)
        nutrition_tags = {self._normalize(item) for item in ("营养", "甲状腺", "代谢", "炎症", "肠道", "免疫")}
        return bool(tags & nutrition_tags) or any(
            term in excerpt
            for term in (
                "营养",
                "维生素",
                "矿物质",
                "抗氧化",
                "蛋白",
                "饮食",
                "甲状腺",
                "代谢",
                "肠道",
            )
        )

    def _rag_hit_supports_lifestyle(self, hit: SafeRagHit) -> bool:
        excerpt = self._normalize(hit.excerpt)
        return any(
            term in excerpt
            for term in (
                "睡眠",
                "压力",
                "运动",
                "饮食",
                "生活方式",
                "作息",
                "久坐",
                "活动",
            )
        )

    def _rag_hit_supports_follow_up(self, hit: SafeRagHit) -> bool:
        return bool(self._format_rag_followup_line(hit.excerpt))

    def _append_report_items(
        self,
        report_sections: dict[str, list[str] | str],
        section: str,
        items: list[str],
    ) -> None:
        cleaned_items = [self._sanitize_report_line(item) for item in items if item and self._sanitize_report_line(item)]
        if not cleaned_items:
            return
        existing = report_sections.get(section, [])
        if isinstance(existing, str):
            existing_items = [existing] if existing.strip() else []
        else:
            existing_items = [str(item) for item in existing if str(item).strip()]
        report_sections[section] = list(dict.fromkeys(existing_items + cleaned_items))

    def _apply_report_section_overrides(
        self,
        report_sections: dict[str, list[str] | str],
        overrides: dict[str, list[str]],
        *,
        analysis_mode: str,
    ) -> dict[str, list[str] | str]:
        if analysis_mode != "llm_primary" or not overrides:
            return report_sections

        merged = dict(report_sections)
        override_targets = {
            "总体健康画像": "核心结论与健康画像",
            "核心结论与健康画像": "核心结论与健康画像",
            "系统功能深度分析": "功能医学系统失衡分析",
            "功能医学系统失衡分析": "功能医学系统失衡分析",
            "生活方式干预重点": "生活方式干预处方",
            "生活方式干预处方": "生活方式干预处方",
            "功能医学检测建议": "后续检查建议",
            "后续检查建议": "后续检查建议",
            "随访计划": "随访计划",
        }
        for source_key, target_key in override_targets.items():
            values = overrides.get(source_key, [])
            cleaned_values = []
            for item in values:
                if not isinstance(item, str):
                    continue
                cleaned = self._sanitize_report_line(item)
                if cleaned:
                    cleaned_values.append(cleaned)
            if cleaned_values:
                existing = merged.get(target_key, [])
                existing_items = existing if isinstance(existing, list) else [str(existing)]
                merged[target_key] = list(dict.fromkeys(cleaned_values + existing_items))
        return merged

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
        statement_tags = {self._normalize(item) for item in (statement.tags + statement.topic_tags)}

        return bool(
            product_marker_rules & set(statement.related_markers)
            or product_goal_rules & statement_goals
            or product_goal_rules & statement_tags
            or product_symptom_rules & statement_tags
        )

    def _resolve_customer_name(self, case) -> str:
        current_name = (case.customer_name or "").strip()
        return current_name or "客户"

    def _build_case_summary(self, case, *, customer_name: str | None = None) -> list[str]:
        questionnaire = case.questionnaire
        summary_nutrient_hints = self._extract_summary_nutrient_hints(case.clinical_summary_text)
        summary = [f"客户姓名: {customer_name or case.customer_name}"]
        summary.append(
            "分析模式: 大模型优先，本地知识辅助"
            if getattr(case.analysis_mode, "value", str(case.analysis_mode)) == "llm_primary"
            else "分析模式: 本地知识优先"
        )
        if questionnaire:
            if questionnaire.age is not None:
                summary.append(f"年龄: {questionnaire.age}")
            summary.append(f"性别: {questionnaire.sex}")
            if questionnaire.chief_concerns:
                summary.append(f"主要诉求: {'、'.join(questionnaire.chief_concerns[:3])}")
            if questionnaire.goals:
                summary.append(f"健康目标: {'、'.join(questionnaire.goals[:3])}")
            if questionnaire.symptoms:
                summary.append(f"主要症状: {'、'.join(questionnaire.symptoms[:4])}")
            if questionnaire.family_history:
                summary.append(f"家族史: {'、'.join(questionnaire.family_history[:3])}")
            if questionnaire.work_pattern:
                summary.append(f"工作/生活方式: {questionnaire.work_pattern}")
        else:
            summary.append("问卷: 未填写，当前仅基于报告信息生成初稿。")
        if case.clinical_summary_text:
            summary.append(f"病例总结诊断: {case.clinical_summary_text.strip().replace(chr(10), '；')[:180]}")
        if summary_nutrient_hints:
            summary.append(f"所需营养素提示: {'、'.join(summary_nutrient_hints[:12])}")
        summary.append(f"已上传文件数: {len(case.files)}")
        summary.append(f"人工解析校对: {'已完成' if case.parsing_review_completed else '未完成'}")
        return summary

    def _build_key_lab_highlights(self, case) -> list[str]:
        highlights = []
        status_labels = {
            "normal": "正常",
            "attention": "需关注",
            "positive": "阳性",
            "info": "信息",
        }
        for indicator in self.indicator_service.build(case):
            status_value = getattr(indicator.status, "value", str(indicator.status))
            if status_value not in {"attention", "positive"}:
                continue
            highlights.append(
                f"{indicator.indicator_name}: {indicator.result_text}（{status_labels.get(status_value, status_value)}）"
            )
        return list(dict.fromkeys(highlights))

    def _extract_anti_aging_findings(self, case, context: RecommendationContext) -> list[str]:
        source_text = "\n".join(
            part
            for part in (
                case.clinical_summary_text or "",
                case.notes or "",
                getattr(case.questionnaire, "additional_notes", None) or "",
            )
            if part
        )
        if not source_text.strip():
            return []

        normalized = self._normalize(source_text)
        anti_aging_terms = (
            "端粒",
            "DNA甲基化",
            "甲基化年龄",
            "免疫系统年龄",
            "心血管系统年龄",
            "内分泌系统年龄",
            "KLF14",
            "ELOVL2",
            "TRIM59",
        )
        if not self._text_has_any(normalized, *anti_aging_terms):
            return []

        def number_after(*labels: str) -> str | None:
            for label in labels:
                pattern = rf"{re.escape(label)}[^0-9]{{0,28}}([0-9]+(?:\.[0-9]+)?)"
                match = re.search(pattern, source_text, flags=re.IGNORECASE)
                if match:
                    return match.group(1)
            return None

        findings = ["抗衰画像：病例总结已出现端粒或 DNA 甲基化相关信息，报告会把细胞老化、免疫、心血管和内分泌节律纳入系统失衡判断。"]
        telomere_age = number_after("端粒生物年龄", "端粒年龄", "生物年龄")
        methylation_age = number_after("DNA甲基化年龄", "甲基化年龄")
        immune_age = number_after("免疫系统年龄")
        cardiovascular_age = number_after("心血管系统年龄")
        endocrine_age = number_after("内分泌系统年龄")

        if telomere_age:
            findings.append(f"端粒信息提示生物年龄约 {telomere_age} 岁，可作为细胞修复储备和长期压力负荷的观察线索。")
        if methylation_age:
            findings.append(f"DNA 甲基化年龄约 {methylation_age} 岁，建议结合实际年龄、睡眠压力、代谢和炎症指标看趋势。")
        if immune_age or cardiovascular_age or endocrine_age:
            details = []
            if immune_age:
                details.append(f"免疫系统年龄约 {immune_age} 岁")
            if cardiovascular_age:
                details.append(f"心血管系统年龄约 {cardiovascular_age} 岁")
            if endocrine_age:
                details.append(f"内分泌系统年龄约 {endocrine_age} 岁")
            findings.append(f"系统年龄线索：{'、'.join(details)}，可辅助判断首月干预优先级。")

        genes = [gene for gene in ("KLF14", "ELOVL2", "TRIM59") if gene.lower() in source_text.lower()]
        if genes:
            findings.append(f"代表基因线索：{', '.join(genes)}，建议分别结合内分泌代谢、心血管老化和免疫状态进行解释。")
        return list(dict.fromkeys(findings))[:5]

    def _build_health_portrait(
        self,
        case,
        context: RecommendationContext,
        key_lab_highlights: list[str],
        risk_notices: list[str],
        report_guidance: list[str] | None = None,
        anti_aging_findings: list[str] | None = None,
    ) -> list[str]:
        priority_findings = self._prioritized_system_findings(
            context,
            report_guidance=report_guidance,
            anti_aging_findings=anti_aging_findings,
        )
        if not priority_findings:
            return ["一句话健康画像：当前资料已完成异常梳理，但尚缺少足够证据形成明确的身体系统优先主线。"]

        top_names = "、".join(SYSTEM_NAMES.get(item.system_id, item.title.split("（", 1)[0]) for item in priority_findings[:3])
        portrait = [
            f"一句话健康画像：当前问题主要集中在{top_names}，应按证据强弱分层处理，并结合症状与复查趋势持续校正。"
        ]
        for index, finding in enumerate(priority_findings[:5], start=1):
            evidence = "、".join(self._system_evidence_names(context, finding.system_id)[:3]) or "已确认异常和症状"
            portrait.append(
                f"核心问题主线{index}：{SYSTEM_NAMES.get(finding.system_id, finding.title.split('（', 1)[0])}，主要依据为{evidence}，当前分级为{finding.priority_level}。"
            )
        first_month_names = "、".join(
            SYSTEM_NAMES.get(item.system_id, item.title.split("（", 1)[0])
            for item in priority_findings[:2]
        )
        portrait.append(
            f"首月优先方向：先围绕{first_month_names}调整饮食、睡眠和活动节律，再按医生确认后的异常匹配营养支持并观察耐受。"
        )
        return portrait

    def _build_system_analysis(
        self,
        case,
        context: RecommendationContext,
        key_lab_highlights: list[str],
        report_guidance: list[str] | None = None,
        anti_aging_findings: list[str] | None = None,
    ) -> list[str]:
        priority_findings = self._prioritized_system_findings(
            context,
            report_guidance=report_guidance,
            anti_aging_findings=anti_aging_findings,
        )
        analysis: list[str] = []
        for finding in priority_findings:
            analysis.append(f"{finding.title}：{finding.body}")
        return self._format_numbered_subsections(list(dict.fromkeys(analysis)), max_groups=8)

    def _format_numbered_subsections(self, items: list[str], *, max_groups: int) -> list[str]:
        sections: list[tuple[str, str]] = []
        for raw_item in items:
            item = str(raw_item or "").strip()
            if not item:
                continue
            if item.startswith("### "):
                sections.append((item[4:].strip(), ""))
                continue
            if "：" in item:
                title, body = item.split("：", 1)
                title = title.strip()
                body = body.strip()
                if title and len(title) <= 34:
                    sections.append((title, body))
                    continue
            sections.append(("综合判断", item))

        formatted: list[str] = []
        for index, (title, body) in enumerate(sections[:max_groups], start=1):
            formatted.append(f"### {index}. {title}")
            if body:
                formatted.append(body)
        return formatted

    def _first_month_dosage(self, product: ProductRule) -> str:
        excel_rule = self.product_dosage_mapping.get(product.sku_id, {}).get("dosage_text")
        if isinstance(excel_rule, str) and excel_rule.strip():
            return excel_rule.strip()
        first_month_rules = {
            "sku_liver_detox_support": "每日 2 粒，早餐后 1 粒、午餐后 1 粒，随餐使用；连续 4 周后根据肝胆指标和胃肠耐受调整。",
            "sku_amino_acid_detox": "每日 2 粒，早餐后 1 粒、午餐后 1 粒，随餐使用；用于首月二阶段解毒支持，需结合肝肾功能人工确认。",
            "sku_thyroid_support": "每日 2 粒，早餐后 1 粒、午餐后 1 粒；若正在使用甲状腺药物，需与药物错开并人工确认。",
            "sku_fish_oil_rtg": "每日 4 粒，早餐 2 粒、午餐 2 粒，随含脂肪餐食用；如有出血风险或抗凝药物需人工确认。",
            "sku_magnesium_glycinate": "每日 4 粒，睡前 30-60 分钟使用；若出现腹泻、肾功能异常或正在补镁需人工确认。",
        }
        return first_month_rules.get(product.sku_id, product.dosage_rule)

    def _first_month_focus_summary(self, recommended_items: list[DraftRecommendationItem]) -> str:
        focus_rules = [
            ("肝脏/解毒", ("肝脏", "肝胆", "解毒", "胆汁", "谷胱甘肽", "sku_liver_detox", "sku_amino_acid_detox")),
            ("免疫/甲状腺", ("甲状腺", "桥本", "免疫", "维生素d", "sku_thyroid")),
            ("心血管/血脂", ("心血管", "血脂", "鱼油", "炎症", "hcy", "同型半胱氨酸", "sku_fish", "sku_cardiac")),
            ("代谢/血糖", ("血糖", "糖代谢", "胰岛素", "体重", "腰围", "sku_blood_sugar", "sku_weight")),
            ("睡眠/压力恢复", ("睡眠", "压力", "焦虑", "镁", "hpa", "sku_magnesium", "sku_sleep", "sku_ashwagandha")),
            ("肠道/消化执行力", ("肠道", "消化", "胃酸", "菌群", "腹胀", "sku_bile", "sku_zinc_carnosine", "sku_herbal")),
            ("细胞能量/抗氧化", ("线粒体", "细胞能量", "疲劳", "抗氧化", "辅酶q10", "sku_mito", "sku_coq10")),
        ]
        selected: list[str] = []
        for item in recommended_items:
            searchable = self._normalize(
                " ".join([item.sku_id, item.display_name, item.reason, " ".join(item.evidence_details)])
            )
            for label, terms in focus_rules:
                if label in selected:
                    continue
                if any(self._normalize(term) in searchable for term in terms):
                    selected.append(label)
                    break
            if len(selected) >= 4:
                break
        return "、".join(selected) if selected else "当前证据最明确的系统瓶颈"

    def _build_first_month_protocol(self, recommended_items: list[DraftRecommendationItem]) -> list[str]:
        if not recommended_items:
            return ["当前暂无明确可发布的首月营养素组合，建议先补充问卷、确认用药与过敏史后再生成方案。"]

        focus_summary = self._first_month_focus_summary(recommended_items)
        lines = [
            f"首月原则：先围绕{focus_summary}建立基础支持，4周后根据耐受、症状变化和复查趋势调整。",
            "执行顺序：优先从核心产品开始，不建议一次性叠加过多；若出现胃肠不适、睡眠改变、皮疹或用药冲突，应暂停升级并人工复核。",
        ]
        for item in recommended_items:
            safety_note = self._public_safety_note(item.warnings)
            safety_suffix = f"；注意/禁忌：{safety_note}" if safety_note else ""
            lines.append(f"{item.display_name}：{item.dosage}；干预靶点：{item.reason}{safety_suffix}")
        return lines

    def _build_phased_protocol(self, recommended_items: list[DraftRecommendationItem]) -> list[str]:
        if not recommended_items:
            return ["当前暂无明确可发布的营养素组合，建议先补充问卷、确认用药与过敏史后再生成阶段方案。"]

        phases = [
            ("第一阶段（前 4-6 周）", recommended_items[:3], "优先做基础修复与耐受性观察。"),
            ("第二阶段（第 6-12 周）", recommended_items[3:5], "在基础耐受稳定后，再强化能量、抗炎或代谢支持。"),
            ("按需追加阶段", recommended_items[5:], "仅在目标明确且前阶段耐受良好时再逐步加入。"),
        ]

        lines: list[str] = []
        for title, items, note in phases:
            if not items:
                continue
            lines.append(f"{title}：{note}")
            for item in items:
                safety_note = self._public_safety_note(item.warnings)
                safety_suffix = f"；注意/禁忌：{safety_note}" if safety_note else ""
                lines.append(f"{item.display_name}：{item.dosage}；适用说明：{item.reason}{safety_suffix}")
        return lines

    def _dosage_review_reasons(self, context: RecommendationContext) -> list[str]:
        reasons: list[str] = []
        if context.age is not None and context.age < 18:
            reasons.append("未成年人需按年龄/体重由医生确认剂量")
        if context.pregnancy:
            reasons.append("孕哺期需由医生确认剂量与成分安全性")
        high_risk_conditions = ("肾", "肝", "肿瘤", "癌", "甲亢")
        if any(term in condition for condition in context.conditions for term in high_risk_conditions):
            reasons.append("存在重要既往史，需结合肝肾功能与治疗方案复核")
        if context.medications:
            reasons.append("当前用药可能影响营养素剂量或服用时机")
        return reasons

    def _resolve_dosage(self, product: ProductRule, context: RecommendationContext) -> str:
        base = self._first_month_dosage(product).strip() or "请按医生建议使用"
        return f"医生复核剂量；产品规则基准：{base}" if self._dosage_review_reasons(context) else base

    def _product_safety_warnings(self, product: ProductRule, context: RecommendationContext | None = None) -> list[str]:
        dosage_reasons = self._dosage_review_reasons(context) if context is not None else []
        warnings = dosage_reasons + product.contraindications + product.interaction_rule + product.warning_text
        return list(dict.fromkeys(warnings))[:6]

    def _public_safety_note(self, warnings: list[str], *, limit: int = 3) -> str:
        public_warnings = []
        for warning in warnings:
            cleaned = str(warning).strip()
            if not cleaned or self._is_internal_safety_note(cleaned):
                continue
            public_warnings.append(cleaned)
        return "；".join(list(dict.fromkeys(public_warnings))[:limit])

    def _is_internal_safety_note(self, warning: str) -> bool:
        normalized = warning.lower()
        return "sku" in normalized or "规格" in warning

    def _build_lifestyle_prescription(self, case, context: RecommendationContext, lifestyle_actions: list[str]) -> list[str]:
        questionnaire = case.questionnaire
        prescription = [
            "### A. 饮食干预：移除-替代-重建",
            "移除：首月先减少酒精、含糖饮料、甜点、油炸食物、夜宵和高盐外食；若有桥本/甲状腺免疫问题，可观察麸质、乳制品和高度加工食品是否加重不适。",
            "替代：每餐按半盘非淀粉蔬菜、1掌心优质蛋白、1拳头低升糖主食执行；油脂优先橄榄油、坚果、深海鱼或相应替代，主食优先全谷物、豆类和薯类。",
            "重建：连续4周记录早餐、外食、酒精、咖啡因、排便和餐后困倦，用记录来判断血糖、尿酸、肠道和睡眠是否随执行改善。",
            "### B. 运动处方：低冲击代谢激活",
            "运动安排：第1-2周以饭后步行15-20分钟和久坐打断为主；第3-4周逐步过渡到每周150分钟中等强度有氧，加每周2次轻抗阻训练。",
            "运动禁忌：若出现胸闷、明显心悸、头晕、关节急性疼痛、痛风急性发作或血压明显异常，先暂停高强度训练并联系医生。",
            "### C. 睡眠与节律重建",
            "睡眠节律：固定起床时间，晨起自然光15分钟；14点后减少咖啡因，睡前1小时减少屏幕、工作输入和剧烈运动。",
            "### D. 压力与解毒负担管理",
            "压力与解毒负担：每天安排2次5分钟呼吸/冥想/伸展；首月减少熬夜、酒精、香烟和不必要的环境暴露，让肝脏解毒和HPA轴先降负荷。",
        ]
        if questionnaire and questionnaire.dining_out_frequency:
            prescription.append(f"外食策略：当前外食频率为 {questionnaire.dining_out_frequency}，建议先把外食控制在可计划场景，优先选择清蒸/炖煮、足量蛋白和蔬菜。")
        if questionnaire and questionnaire.food_sensitivities:
            prescription.append(f"触发食物观察：已记录食物敏感为 {'、'.join(questionnaire.food_sensitivities[:4])}，建议先做4周回避和症状记录。")
        if questionnaire and questionnaire.supplement_use:
            prescription.append("补剂执行：现有补充剂不要和新方案一次性全部叠加，先确认名称、剂量、服用时间和耐受性。")
        prescription.extend(lifestyle_actions)
        return list(dict.fromkeys(prescription))[:16]

    def _build_lifestyle_focus(self, case, context: RecommendationContext, lifestyle_actions: list[str]) -> list[str]:
        questionnaire = case.questionnaire
        focus: list[str] = []

        if questionnaire and questionnaire.dining_out_frequency:
            focus.append(f"饮食执行：当前外食频率为 {questionnaire.dining_out_frequency}，建议先减少高不确定性的外食场景。")
        if questionnaire and questionnaire.food_sensitivities:
            focus.append(f"饮食关注：已记录的食物敏感为 {'、'.join(questionnaire.food_sensitivities[:4])}，建议优先做回避与观察。")
        if questionnaire and questionnaire.sleep_hours is not None:
            focus.append(f"睡眠恢复：当前睡眠约 {questionnaire.sleep_hours} 小时，建议优先修复睡眠时长与深度。")
        if questionnaire and questionnaire.exercise_frequency:
            focus.append(f"运动执行：当前运动频率为 {questionnaire.exercise_frequency}，建议以可持续、低门槛的方式逐步恢复活动量。")
        if questionnaire and questionnaire.chemical_sensitivity:
            focus.append(f"环境暴露：已记录环境或化学敏感信息为 {questionnaire.chemical_sensitivity}，建议同步减少可疑暴露源。")

        focus.extend(lifestyle_actions)
        return list(dict.fromkeys(focus))[:8]

    def _build_prioritized_test_recommendations(
        self,
        context: RecommendationContext,
        anti_aging_findings: list[str] | None = None,
    ) -> list[str]:
        items = [
            "首月必做：复查或补齐本次异常指标对应的基础项目，重点看血脂、尿酸、肝肾功能、血糖/胰岛素和炎症指标的趋势，而不是只看单次结果。",
            "首月必做：记录睡眠、精力、排便、餐后困倦、酒精/咖啡因和运动执行情况，用于判断方案耐受性和生活方式触发因素。",
        ]
        if self._has_thyroid_pattern(context):
            items.append("2-3个月完善：甲状腺功能全套 TSH、FT3、FT4、TPOAb、TGAb，并结合症状、睡眠压力和营养状态判断桥本/甲减管理方向。")
        if self._has_liver_metabolic_pattern(context):
            items.append("2-3个月完善：血脂全套、尿酸、肝功能、空腹血糖/胰岛素、HbA1c，必要时加脂肪肝影像或肝胆相关评估。")
        if self._has_marker(context, "vitamin_d", "low") or self._has_marker(context, "hs_crp", "high"):
            items.append("2-3个月完善：25(OH)D、hs-CRP 或其他炎症相关指标，用于确认免疫与抗炎支持是否需要调整。")
        if self._has_gut_or_bile_pattern(context):
            items.append("可选功能医学检测：若腹胀、便秘、腹泻或食物反应持续，可做肠道菌群/消化吸收/食物不耐受评估，用于细化饮食和肠道策略。")
        if self._has_neuro_sleep_pattern(context):
            items.append("可选功能医学检测：若睡眠、疲劳和压力恢复仍明显，可考虑皮质醇节律、营养缺口或线粒体相关评估。")
        if anti_aging_findings:
            items.append("可选抗衰追踪：端粒或 DNA 甲基化结果建议作为长期趋势观察，后续与内分泌、心血管、免疫系统年龄和生活方式执行结果一起复盘。")
        return list(dict.fromkeys(items))[:8]

    def _build_existing_supplement_adjustments(self, case) -> list[str]:
        questionnaire = case.questionnaire
        supplement_use = (getattr(questionnaire, "supplement_use", None) or "").strip() if questionnaire else ""
        if not supplement_use:
            return [
                "暂未识别到正在使用的补充剂；首月开始前建议补充确认现有保健品、药物、剂量和服用时间，避免重复补充或相互作用。",
            ]
        return [
            f"已记录现有补充剂：{supplement_use}。",
            "调整原则：不要与首月新方案一次性全部叠加；先核对是否存在同类营养素重复、剂量过高、与药物冲突或胃肠不耐受。",
            "如正在使用甲状腺药、抗凝药、降糖药、降压药或长期处方药，补充剂增减必须先由医生确认。",
        ]

    def _build_test_recommendations(self, context: RecommendationContext) -> list[str]:
        items: list[str] = []

        if self._has_thyroid_pattern(context):
            items.append("甲状腺功能全套：TSH、FT3、FT4、TPOAb、TGAb，用于判断甲状腺功能与自身免疫活跃度。")
        if self._has_marker(context, "vitamin_d", "low") or self._has_thyroid_pattern(context):
            items.append("25(OH)D：用于评估维生素 D 水平，便于后续免疫与骨骼支持调整。")
        if self._has_marker(context, "ferritin", "low"):
            items.append("铁代谢相关检查：铁蛋白、血清铁、转铁蛋白饱和度，用于确认缺铁或储备不足风险。")
        if self._has_marker(context, "fasting_glucose", "high") or self._has_marker(context, "hba1c", "high"):
            items.append("血糖代谢检查：空腹血糖、胰岛素、HbA1c，用于判断代谢风险与干预优先级。")
        if "gut_support" in context.lifestyle_tags:
            items.append("肠道功能评估：可结合菌群、消化吸收或食物不耐受检测进一步细化饮食和肠道方案。")
        if "stress_support" in context.lifestyle_tags:
            items.append("压力节律评估：如条件允许，可结合皮质醇节律或睡眠恢复情况做进一步判断。")

        if not items:
            items.append("建议结合主要症状、异常指标和目标，在顾问评估后决定是否进入功能医学检测。")
        return items[:6]

    def _build_follow_up_plan(self, context: RecommendationContext) -> list[str]:
        plan = [
            "2 周内：回访补剂耐受性、睡眠变化、胃肠反应与执行难点。",
            "4-6 周：根据症状变化与基础指标，决定是否调整剂量、增减组合或加入下一阶段支持。",
            "8-12 周：复查关键指标，评估疲劳、睡眠、消化与目标达成度，再决定后续维持策略。",
        ]
        if self._has_thyroid_pattern(context):
            plan.append("如存在甲状腺相关异常，优先跟踪甲状腺症状、复查节奏与药物/营养素使用间隔。")
        return plan[:4]

    def _build_ninety_day_roadmap(
        self,
        recommended_items: list[DraftRecommendationItem],
        context: RecommendationContext,
    ) -> list[str]:
        first = "、".join(item.display_name for item in recommended_items[:2]) or "基础信息补充与症状观察"
        second = "、".join(item.display_name for item in recommended_items[2:4]) or "饮食与睡眠执行稳定"
        third = "、".join(item.display_name for item in recommended_items[4:6]) or "复查与方案微调"
        roadmap = [
            f"Month 1：建立基础饮食、睡眠与补充剂执行节律，优先完成 {first} 的耐受性观察。",
            f"Month 2：在前期稳定基础上，推进 {second} 相关支持，并观察体能、睡眠和消化变化。",
            f"Month 3：结合 {third} 与复查结果，评估是否进入维持期、强化期或进一步检测。",
        ]
        if "sedentary_risk" in context.lifestyle_tags:
            roadmap.append("整个 90 天过程中都应把久坐打断、步行和轻阻力训练纳入常规生活节律。")
        return roadmap[:4]

    def _top_msq_burdens(self, scores: dict[str, int]) -> list[str]:
        labels = {0: "无", 1: "轻度", 2: "中度", 3: "较高", 4: "较重"}
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [f"{name} {labels.get(score, str(score))}负担" for name, score in ranked if score > 0]

    def _has_marker(self, context: RecommendationContext, marker_code: str, abnormal_flag: str | None = None) -> bool:
        items = context.markers_by_code.get(marker_code, [])
        if not items:
            return False
        if abnormal_flag is None:
            return True
        return any(item.abnormal_flag.value == abnormal_flag for item in items)

    def _has_thyroid_pattern(self, context: RecommendationContext) -> bool:
        thyroid_terms = ("\u6865\u672c", "\u7532\u51cf", "\u7532\u72b6\u817a")
        return bool(
            self._has_marker(context, "thyroid_peroxidase_antibody", "high")
            or self._has_marker(context, "thyroglobulin_antibody", "high")
            or any(keyword in condition for condition in context.conditions for keyword in thyroid_terms)
        )

    def _select_products_for_output(
        self,
        ranked_products: list[ProductRule],
        selected_sku_ids: list[str],
        product_evidence_map: dict[str, list[str]],
    ) -> list[ProductRule]:
        min_option_count = min(4, len(ranked_products))
        by_id = {product.sku_id: product for product in ranked_products}
        requested = [by_id[sku_id] for sku_id in selected_sku_ids if sku_id in by_id]
        pool: list[ProductRule] = []
        for product in [*requested, *ranked_products]:
            if product not in pool:
                pool.append(product)

        selected_products: list[ProductRule] = []
        covered_goals: set[str] = set()
        system_counts: dict[str, int] = {}

        # Direct actionable deficiencies are reserved before the general top-N
        # pass. This prevents broad multi-goal products from crowding out a
        # precise product such as VD3+K after the list is truncated.
        must_cover_products: dict[str, ProductRule] = {}
        for product in ranked_products:
            evidence_ids = product_evidence_map.get(product.sku_id, [])
            for goal in self._must_cover_goals(evidence_ids):
                must_cover_products.setdefault(goal, product)
        for goal, product in must_cover_products.items():
            selected_products.append(product)
            covered_goals.add(goal)
            system_id = self._selection_system_id(product_evidence_map.get(product.sku_id, []))
            if system_id:
                system_counts[system_id] = system_counts.get(system_id, 0) + 1

        for product in pool:
            if product in selected_products:
                continue
            evidence_ids = product_evidence_map.get(product.sku_id, [])
            association = self._association_percent(evidence_ids)
            if association < 50 and product.sku_id in self.product_tag_profiles:
                continue
            primary_goal = self._selection_primary_goal(evidence_ids)
            if primary_goal and primary_goal in covered_goals:
                continue
            system_id = self._selection_system_id(evidence_ids)
            system_limit = 1 if system_id == "liver_detox" else 3
            if system_id and system_counts.get(system_id, 0) >= system_limit:
                continue
            selected_products.append(product)
            if primary_goal:
                covered_goals.add(primary_goal)
            if system_id:
                system_counts[system_id] = system_counts.get(system_id, 0) + 1
            if len(selected_products) >= 10:
                break

        if len(selected_products) < min_option_count:
            for product in pool:
                if product in selected_products:
                    continue
                evidence_ids = product_evidence_map.get(product.sku_id, [])
                if not self._is_reasonable_top_up_candidate(evidence_ids):
                    continue
                primary_goal = self._selection_primary_goal(evidence_ids)
                if primary_goal and primary_goal in covered_goals:
                    continue
                selected_products.append(product)
                if primary_goal:
                    covered_goals.add(primary_goal)
                if len(selected_products) >= min_option_count:
                    break

        return (selected_products or ranked_products)[:10]

    @staticmethod
    def _must_cover_goals(evidence_ids: list[str]) -> list[str]:
        return [
            evidence_id.replace("signal:must_cover_goal_", "", 1)
            for evidence_id in evidence_ids
            if evidence_id.startswith("signal:must_cover_goal_")
        ]

    @staticmethod
    def _selection_primary_goal(evidence_ids: list[str]) -> str | None:
        for evidence_id in evidence_ids:
            if evidence_id.startswith("signal:primary_support_goal_"):
                return evidence_id.replace("signal:primary_support_goal_", "", 1)
        return None

    @staticmethod
    def _selection_system_id(evidence_ids: list[str]) -> str | None:
        for evidence_id in evidence_ids:
            if not evidence_id.startswith("signal:body_system_"):
                continue
            system_id = evidence_id.replace("signal:body_system_", "", 1)
            if system_id.startswith("rank_"):
                continue
            return system_id
        return None

    @staticmethod
    def _has_direct_product_evidence(evidence_ids: list[str]) -> bool:
        return any(
            evidence_id.startswith("signal:tag_marker_")
            or evidence_id.startswith("signal:direct_marker_rule_")
            for evidence_id in evidence_ids
        )

    def _product_has_direct_context_match(self, product: ProductRule, context: RecommendationContext) -> bool:
        if any(rule.startswith("marker:") and self._matches_rule(rule, context) for rule in product.indications):
            return True
        profile = self.product_tag_profiles.get(product.sku_id)
        return bool(
            profile
            and (
                self._matched_marker_tags(profile.marker_tags, context)
                or set(profile.primary_axes) & set(context.support_goal_findings)
            )
        )

    def _finding_standardization_audit(self, case) -> list[dict[str, str | None]]:
        latest = self.repository.get_latest_case_analysis(case.id)
        if not latest:
            return []
        findings = latest.reviewed_abnormal_findings or latest.abnormal_findings
        return [
            {
                "finding_id": finding.id,
                "marker_code": finding.marker_code,
                "finding_code": finding.finding_code,
                "system_ids": ",".join(finding.system_ids),
                "support_goals": ",".join(finding.support_goals),
                "status": getattr(finding.standardization_status, "value", str(finding.standardization_status)),
            }
            for finding in findings
        ]

    def _product_selection_audit(
        self,
        *,
        context: RecommendationContext,
        recommended_items: list[DraftRecommendationItem],
        product_evidence_map: dict[str, list[str]],
        safety_decisions_by_sku: dict[str, list[SafetyDecision]],
    ) -> list[dict[str, str]]:
        recommended_ids = {item.sku_id for item in recommended_items}
        audit: list[dict[str, str]] = []
        for product in self._list_products(enabled_only=True):
            direct_match = self._product_has_direct_context_match(product, context)
            safety_exclusions = [
                decision
                for decision in safety_decisions_by_sku.get(product.sku_id, [])
                if decision.action == SafetyRuleAction.exclude
            ]
            if not direct_match and not safety_exclusions:
                continue
            if product.sku_id in recommended_ids:
                reason = "recommended"
            elif safety_exclusions:
                reason = "safety_excluded"
            elif product.sku_id in product_evidence_map:
                reason = "ranking_or_model_selection_excluded"
            else:
                reason = "no_qualified_evidence"
            audit.append({"sku_id": product.sku_id, "result": reason})
        return audit

    def _is_reasonable_top_up_candidate(self, evidence_ids: list[str]) -> bool:
        association_percent = self._association_percent(evidence_ids)
        if association_percent >= 35:
            return True
        if any(evidence_id.startswith("clinician_rule:") for evidence_id in evidence_ids):
            return True
        signal_ids = [evidence_id for evidence_id in evidence_ids if evidence_id.startswith("signal:")]
        return any(
            signal_id.startswith("signal:clinical_")
            or signal_id == "signal:direct_product_rule"
            or signal_id == "signal:summary_nutrient_support"
            for signal_id in signal_ids
        )

    def _ranked_product_sort_key(
        self,
        item: tuple[float, ProductRule, list[str]],
    ) -> tuple[int, int, int, int, int, int, int, int, float]:
        score, _product, evidence_ids = item
        association_percent = self._association_percent(evidence_ids)
        system_priority_percent = self._system_priority_percent(evidence_ids)
        clinician_rule_hit = any(evidence_id.startswith("clinician_rule:") for evidence_id in evidence_ids)
        top_system_primary_axis_hit = "signal:top_system_primary_axis" in evidence_ids
        marker_tag_hit = any(evidence_id.startswith("signal:tag_marker_") for evidence_id in evidence_ids)
        direct_product_rule_hit = "signal:direct_product_rule" in evidence_ids
        direct_marker_rule_hit = any(
            evidence_id.startswith("signal:direct_marker_rule_") for evidence_id in evidence_ids
        )
        must_cover_hit = any(
            evidence_id.startswith("signal:must_cover_goal_") for evidence_id in evidence_ids
        )
        return (
            1 if clinician_rule_hit else 0,
            1 if must_cover_hit else 0,
            1 if marker_tag_hit or direct_marker_rule_hit else 0,
            1 if top_system_primary_axis_hit else 0,
            system_priority_percent,
            1 if association_percent >= 50 else 0,
            association_percent,
            1 if marker_tag_hit or direct_product_rule_hit else 0,
            score,
        )

    @staticmethod
    def _body_system_rank(evidence_ids: list[str]) -> int | None:
        for evidence_id in evidence_ids:
            if not evidence_id.startswith("signal:body_system_rank_"):
                continue
            try:
                return int(evidence_id.rsplit("_", 1)[1])
            except ValueError:
                return None
        return None

    def _association_percent(self, evidence_ids: list[str]) -> int:
        for evidence_id in evidence_ids:
            if not evidence_id.startswith("signal:association_"):
                continue
            try:
                return int(evidence_id.rsplit("_", 1)[1])
            except ValueError:
                return 0
        return 0

    def _system_priority_percent(self, evidence_ids: list[str]) -> int:
        priority_values: list[int] = []
        for evidence_id in evidence_ids:
            if not evidence_id.startswith("signal:system_priority_"):
                continue
            try:
                priority_values.append(int(evidence_id.rsplit("_", 1)[1]))
            except ValueError:
                continue
        return max(priority_values) if priority_values else 0

    def _normalize(self, value: str) -> str:
        return "".join(value.lower().split())

    def _contains_cjk(self, value: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in value)
