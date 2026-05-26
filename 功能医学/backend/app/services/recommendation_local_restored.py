from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from app.domain.models import AuditLog, ClinicianRule, ClinicianRuleAction, DraftRecommendationItem, DraftStatus, ProductRule, RecommendationDraft
from app.providers.base import DraftCompositionInput, LLMProvider, VectorStoreProvider
from app.repositories.in_memory import LocalRepository
from app.services.case_service import CaseService
from app.services.indicator_extraction import CaseIndicatorService


@dataclass
class RecommendationContext:
    markers_by_code: dict[str, list]
    goals: set[str]
    chief_concerns: set[str]
    symptoms: set[str]
    conditions: set[str]
    family_history: set[str]
    medications: set[str]
    allergies: set[str]
    food_sensitivities: set[str]
    pregnancy: bool
    lifestyle_tags: set[str]
    msq_system_scores: dict[str, int]
    clinical_summary_text: str
    summary_nutrient_hints: list[str]


@dataclass
class SupportProfile:
    profile_id: str
    title: str
    weight: float
    preferred_categories: tuple[str, ...]
    match_terms: tuple[str, ...]
    query_terms: tuple[str, ...]
    marker_codes: tuple[str, ...]


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
    )

    def __init__(
        self,
        *,
        repository: LocalRepository,
        case_service: CaseService,
        indicator_service: CaseIndicatorService,
        vector_store: VectorStoreProvider,
        llm_provider: LLMProvider,
        model_version: str = "local-structured-v1",
        prompt_version: str = "local-report-v1",
        rule_version: str = "local-rules-v1",
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.indicator_service = indicator_service
        self.vector_store = vector_store
        self.llm_provider = llm_provider
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.rule_version = rule_version
        self.object_store = None

    def generate(self, case_id: str, requested_by: str) -> RecommendationDraft:
        case = self.case_service.get_case(case_id)
        analysis_mode = getattr(case.analysis_mode, "value", str(case.analysis_mode))
        context = self._build_context(case)
        support_profiles = self._build_support_profiles(context)
        case_summary = self._build_case_summary(case)
        key_lab_highlights = self._build_key_lab_highlights(case)
        report_guidance = self._extract_report_guidance(case)
        red_flags = self._evaluate_red_flags(context, case.questionnaire.age if case.questionnaire else None)
        missing_info = self._collect_missing_info(case)
        reviewed_report_text = self._build_reviewed_report_text(case)
        structured_case_context = self._build_structured_case_context(case, context, support_profiles, report_guidance)

        reviewed_knowledge = self.repository.list_knowledge(reviewed_only=True)
        knowledge_hits = []
        if reviewed_knowledge:
            retrieval_query = self._build_query(case, context, support_profiles)
            knowledge_hits = self.vector_store.search(retrieval_query, top_k=12 if analysis_mode == "llm_primary" else 8)

        knowledge_by_id = {item.statement_id: item for item in reviewed_knowledge}
        product_by_id = {item.sku_id: item for item in self.repository.list_products(enabled_only=True)}
        matched_clinician_rules = self.list_matched_clinician_rules(case, context=context, support_profiles=support_profiles)
        clinician_rule_by_id = {item.id: item for item in matched_clinician_rules}

        if analysis_mode == "llm_primary":
            ranked_products, product_evidence_map, contraindications = self._rank_products_for_llm_primary(
                case,
                context,
                knowledge_hits,
                support_profiles,
                matched_clinician_rules,
            )
        else:
            ranked_products, product_evidence_map, contraindications = self._rank_products(
                context,
                knowledge_hits,
                support_profiles,
                matched_clinician_rules,
            )
        composition = self.llm_provider.compose(
            DraftCompositionInput(
                customer_name=case.customer_name,
                analysis_mode=analysis_mode,
                case_summary=case_summary,
                key_lab_highlights=key_lab_highlights,
                candidate_products=ranked_products,
                knowledge_hits=knowledge_hits,
                product_evidence_map=product_evidence_map,
                red_flags=red_flags,
                contraindications=contraindications,
                missing_info=missing_info,
                reviewed_report_text=reviewed_report_text,
                structured_case_context=structured_case_context,
            )
        )
        lifestyle_actions = self._finalize_lifestyle_actions(composition.lifestyle_actions, knowledge_hits, context)

        recommended_items: list[DraftRecommendationItem] = []
        selected_products = self._select_products_for_output(ranked_products, composition.selected_sku_ids)
        if not composition.abstain_reason:
            for product in selected_products:
                evidence_ids = product_evidence_map.get(product.sku_id, [])[:4]
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
                        display_name=product.display_name,
                        dosage=product.dosage_rule,
                        reason=final_reason,
                        evidence_ids=evidence_ids,
                        evidence_details=self._build_evidence_details(
                            evidence_ids,
                            product_by_id=product_by_id,
                            knowledge_by_id=knowledge_by_id,
                            clinician_rule_by_id=clinician_rule_by_id,
                        ),
                        warnings=list(
                            dict.fromkeys(product.warning_text + product.interaction_rule + product.contraindications)
                        )[:4],
                    )
                )

        evidence_ids = list(dict.fromkeys([e for ids in product_evidence_map.values() for e in ids]))[:20]
        evidence_details = self._build_evidence_details(
            evidence_ids,
            product_by_id=product_by_id,
            knowledge_by_id=knowledge_by_id,
            clinician_rule_by_id=clinician_rule_by_id,
        )
        health_portrait = self._build_health_portrait(case, context, key_lab_highlights, red_flags, report_guidance)
        system_analysis = self._build_system_analysis(case, context, key_lab_highlights, report_guidance)
        phased_protocol = self._build_phased_protocol(recommended_items)
        lifestyle_focus = self._build_lifestyle_focus(case, context, lifestyle_actions)
        test_recommendations = self._build_test_recommendations(context)
        follow_up_plan = self._build_follow_up_plan(context)
        roadmap = self._build_ninety_day_roadmap(recommended_items, context)
        report_sections = {
            "病例摘要": case_summary,
            "总体健康画像": health_portrait,
            "关键指标摘要": key_lab_highlights,
            "原报告小结与建议": report_guidance,
            "系统功能深度分析": system_analysis,
            "风险提示": list(dict.fromkeys(red_flags + contraindications)),
            "个性化营养素方案": phased_protocol,
            "生活方式干预重点": lifestyle_focus,
            "功能医学检测建议": test_recommendations,
            "随访计划": follow_up_plan,
            "90天健康路线图": roadmap,
            "待确认项": missing_info,
            "审核备注": [
                "\u6240\u6709\u7ed3\u679c\u4ec5\u57fa\u4e8e\u672c\u5730\u5df2\u5ba1\u6838\u77e5\u8bc6\u3001\u4ea7\u54c1\u89c4\u5219\u548c\u4eba\u5de5\u786e\u8ba4\u540e\u7684\u75c5\u4f8b\u6570\u636e\u751f\u6210\u3002",
                "\u82e5\u5b58\u5728\u9ad8\u98ce\u9669\u6307\u6807\u3001\u5b55\u54fa\u3001\u513f\u7ae5\u6216\u7528\u836f\u51b2\u7a81\uff0c\u5fc5\u987b\u4eba\u5de5\u590d\u6838\u540e\u518d\u5bf9\u5916\u53d1\u5e03\u3002",
                "\u82e5\u542f\u7528\u4e86\u4e91\u7aef\u5927\u6a21\u578b\uff0c\u5176\u4f5c\u7528\u4ec5\u9650\u4e8e\u5728\u672c\u5730\u5019\u9009\u4ea7\u54c1\u548c\u5df2\u5ba1\u6838\u8bc1\u636e\u8303\u56f4\u5185\u505a\u91cd\u6392\u4e0e\u6da6\u8272\u3002",
                "\u82e5\u533b\u751f\u901a\u8fc7\u667a\u6167\u52a9\u624b\u6c89\u6dc0\u4e86\u75c5\u4f8b\u89c4\u5219\uff0c\u8fd9\u4e9b\u89c4\u5219\u4f1a\u5728\u540e\u7eed\u76f8\u4f3c\u75c5\u4f8b\u4e2d\u4f5c\u4e3a\u53ef\u5ba1\u8ba1\u7684\u52a0\u6743\u4f9d\u636e\u53c2\u4e0e\u63a8\u8350\u3002",
            ],
        }
        if not report_guidance:
            report_sections.pop("原报告小结与建议", None)
        report_sections = self._apply_report_section_overrides(
            report_sections,
            composition.section_overrides,
            analysis_mode=analysis_mode,
        )

        draft = RecommendationDraft(
            id=f"draft_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            status=DraftStatus.abstained if composition.abstain_reason else DraftStatus.pending_review,
            case_summary=case_summary,
            key_lab_highlights=key_lab_highlights,
            recommended_skus=recommended_items,
            lifestyle_actions=lifestyle_actions,
            rationale=composition.rationale,
            evidence_ids=evidence_ids,
            evidence_details=evidence_details,
            contraindications=list(dict.fromkeys(contraindications)),
            missing_info=missing_info,
            confidence=composition.confidence,
            abstain_reason=composition.abstain_reason,
            manual_review_required=True,
            red_flags=red_flags,
            report_sections=report_sections,
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
            if self._is_admin_metadata_snippet(getattr(getattr(item, "source_span", None), "snippet", "")):
                continue
            markers_by_code.setdefault(item.marker_code, []).append(item)
        self._augment_markers_from_case_indicators(case, markers_by_code)

        questionnaire = case.questionnaire
        chief_concerns = {self._normalize(text) for text in (questionnaire.chief_concerns if questionnaire else [])}
        symptoms = {self._normalize(text) for text in (questionnaire.symptoms if questionnaire else [])}
        conditions = {self._normalize(text) for text in (questionnaire.known_conditions if questionnaire else [])}
        family_history = {self._normalize(text) for text in (questionnaire.family_history if questionnaire else [])}
        goals = {self._normalize(text) for text in (questionnaire.goals if questionnaire else [])}
        medications = {self._normalize(text) for text in (questionnaire.medications if questionnaire else [])}
        allergies = {self._normalize(text) for text in (questionnaire.allergies if questionnaire else [])}
        food_sensitivities = {
            self._normalize(text) for text in (questionnaire.food_sensitivities if questionnaire else [])
        }
        msq_system_scores = dict(questionnaire.msq_system_scores) if questionnaire else {}
        clinical_summary_text = (case.clinical_summary_text or "").strip()
        normalized_clinical_summary = self._normalize(clinical_summary_text)
        summary_nutrient_hints = self._extract_summary_nutrient_hints(clinical_summary_text)

        lifestyle_tags: set[str] = set()
        if questionnaire:
            if (questionnaire.sleep_hours or 0) < 6 or self._normalize(questionnaire.sleep_quality or "") in {
                "poor",
                "\u5dee",
            }:
                lifestyle_tags.add("sleep_recovery")
            if questionnaire.stress_level == "high":
                lifestyle_tags.add("stress_support")
            if self._normalize(questionnaire.exercise_frequency or "") in {"rare", "none", "寰堝皯"}:
                lifestyle_tags.add("movement")
            if self._normalize(questionnaire.bowel_habits or "") in {"constipation", "渚跨"}:
                lifestyle_tags.add("gut_support")
            if (questionnaire.sitting_hours_per_day or 0) >= 6:
                lifestyle_tags.add("sedentary_risk")
            if questionnaire.dining_out_frequency and any(
                token in questionnaire.dining_out_frequency for token in ("4", "5", "6", "7", "棰戠箒", "缁忓父")
            ):
                lifestyle_tags.add("outside_dining")
            if questionnaire.chemical_sensitivity:
                lifestyle_tags.add("chemical_sensitivity")

            if msq_system_scores.get("\u6d88\u5316\u9053", 0) >= 2:
                lifestyle_tags.add("gut_support")
            if msq_system_scores.get("鑳介噺/娲诲姩", 0) >= 2:
                lifestyle_tags.add("energy_support")
            if msq_system_scores.get("鎯呯华", 0) >= 2 or msq_system_scores.get("鎬濈淮", 0) >= 2:
                lifestyle_tags.add("stress_support")
            if msq_system_scores.get("浣撻噸", 0) >= 2:
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
            goals=goals,
            chief_concerns=chief_concerns,
            symptoms=symptoms,
            conditions=conditions,
            family_history=family_history,
            medications=medications,
            allergies=allergies,
            food_sensitivities=food_sensitivities,
            pregnancy=bool(questionnaire and questionnaire.pregnant_or_lactating),
            lifestyle_tags=lifestyle_tags,
            msq_system_scores=msq_system_scores,
            clinical_summary_text=clinical_summary_text,
            summary_nutrient_hints=summary_nutrient_hints,
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

    def _evaluate_red_flags(self, context: RecommendationContext, age: int | None) -> list[str]:
        red_flags: list[str] = []
        if age is not None and age < 18:
            red_flags.append("未成年案例需要人工审核后再给出建议。")
        if context.pregnancy:
            red_flags.append("孕期或哺乳期需要人工审核。")
        if any(
            term in condition
            for condition in context.conditions
            for term in ("癌", "肾衰", "肝硬化", "renal", "cancer")
        ):
            red_flags.append("既往疾病提示高风险，需要人工审核。")
        if any(
            term in medication
            for medication in context.medications
            for term in ("华法林", "warfarin", "胰岛素", "insulin")
        ):
            red_flags.append("当前用药与营养素之间可能存在相互作用。")

        for items in context.markers_by_code.values():
            for item in items:
                value = item.normalized_value or item.value or 0
                if item.marker_code == "fasting_glucose" and value >= 7.0:
                    red_flags.append("空腹血糖达到高风险阈值，需要优先人工评估。")
                if item.marker_code == "hba1c" and value >= 6.5:
                    red_flags.append("糖化血红蛋白达到高风险阈值，需要优先人工评估。")
                if item.marker_code == "hs_crp" and value >= 10:
                    red_flags.append("炎症指标显著升高，需先排查急性风险。")
                if item.marker_code == "alt" and value >= 120:
                    red_flags.append("肝功能指标明显异常，需要人工审核。")

        return list(dict.fromkeys(red_flags))

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
        missing.extend(case.parsing_missing_fields)
        return list(dict.fromkeys(missing))

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
                notes.append(f"鍖荤敓瑙勫垯鎻愮ず褰撳墠闃舵璋ㄦ厧澶勭悊 {product.display_name}")
            else:
                score += 2.25 * max(rule.strength, 0.2)
                notes.append(f"鍖荤敓瑙勫垯寤鸿褰撳墠闃舵浼樺厛鑰冭檻 {product.display_name}")
        return round(score, 3), list(dict.fromkeys(evidence_ids)), list(dict.fromkeys(notes))

    def _rank_products_for_llm_primary(
        self,
        case,
        context: RecommendationContext,
        knowledge_hits,
        support_profiles: list[SupportProfile],
        matched_clinician_rules: list[ClinicianRule],
    ):
        ranked: list[tuple[float, ProductRule, list[str]]] = []
        contraindications: list[str] = []
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

        for product in self.repository.list_products(enabled_only=True):
            exclusion_matches = [rule for rule in product.exclusions if self._matches_rule(rule, context)]
            if exclusion_matches:
                contraindications.extend([f"{product.display_name} 琚帓闄? {rule}" for rule in exclusion_matches])
                continue

            score = max(0.08, (100 - product.priority) / 160)
            evidence_ids = [f"product:{product.sku_id}"]
            supportive_evidence = 0

            for indication in product.indications:
                if self._matches_rule(indication, context):
                    score += 0.7
                    supportive_evidence += 1

            signal_score, signal_evidence_ids = self._score_product_from_profiles(product, support_profiles)
            score += signal_score
            evidence_ids.extend(signal_evidence_ids)
            supportive_evidence += len(signal_evidence_ids)

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

            if supportive_evidence == 0:
                continue

            ranked.append((round(score, 3), product, list(dict.fromkeys(evidence_ids))))

        ranked.sort(key=lambda item: item[0], reverse=True)
        product_evidence_map = {product.sku_id: evidence for _, product, evidence in ranked}
        return [product for _, product, _ in ranked[:12]], product_evidence_map, list(dict.fromkeys(contraindications))

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
    ):
        ranked: list[tuple[float, ProductRule, list[str]]] = []
        contraindications: list[str] = []
        products = self.repository.list_products(enabled_only=True)

        for product in products:
            exclusion_matches = [rule for rule in product.exclusions if self._matches_rule(rule, context)]
            if exclusion_matches:
                contraindications.extend([f"{product.display_name} 琚帓闄? {rule}" for rule in exclusion_matches])
                continue

            score = max(0.05, (100 - product.priority) / 100)
            evidence_ids: list[str] = []
            direct_hits = 0
            supportive_evidence = 0

            for indication in product.indications:
                if self._matches_rule(indication, context):
                    direct_hits += 1
                    supportive_evidence += 1
                    score += 0.9

            signal_score, signal_evidence_ids = self._score_product_from_profiles(product, support_profiles)
            score += signal_score
            evidence_ids.extend(signal_evidence_ids)
            supportive_evidence += len(signal_evidence_ids)

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

            if supportive_evidence == 0:
                continue

            ranked.append(
                (
                    round(score, 3),
                    product,
                    [f"product:{product.sku_id}", *list(dict.fromkeys(evidence_ids))],
                )
            )

        ranked.sort(key=lambda item: item[0], reverse=True)
        product_evidence_map = {product.sku_id: evidence for _, product, evidence in ranked}
        return [product for _, product, _ in ranked[:6]], product_evidence_map, list(dict.fromkeys(contraindications))

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
                    title="琛€鑴備唬璋㈡敮鎸?",
                    weight=1.45,
                    preferred_categories=("omega3_support", "cardiometabolic_support", "methylation_support"),
                    match_terms=("蹇冭绠℃敮鎸?", "鎶楃値鏀寔", "浠ｈ阿鏀寔", "鎭㈠鏀寔", "楸兼补", "EPA", "DHA", "杈呴叾Q10"),
                    query_terms=("琛€鑴傚紓甯?", "蹇冭绠℃敮鎸?", "鎶楃値鏀寔", "鑴備唬璋?", "楸兼补"),
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
                    title="琛€绯栦笌浠ｈ阿鏀寔",
                    weight=1.35,
                    preferred_categories=("glucose_support", "weight_support", "foundational_support"),
                    match_terms=("琛€绯栧钩琛?", "浠ｈ阿鏀寔", "琛€绯栫鐞?", "浣撻噸绠＄悊", "鍩虹寰噺钀ュ吇琛ュ厖"),
                    query_terms=("琛€绯栧钩琛?", "浠ｈ阿鏀寔", "鑳板矝绱犳晱鎰熸€?", "浣撻噸绠＄悊"),
                    marker_codes=("fasting_glucose", "hba1c"),
                )
            )

        if self._has_marker(context, "hs_crp", "high"):
            profiles.append(
                SupportProfile(
                    profile_id="inflammation_resolution",
                    title="鐐庣棁涓庢仮澶嶆敮鎸?",
                    weight=1.2,
                    preferred_categories=("inflammation", "omega3_support", "antioxidant_support"),
                    match_terms=("鎶楃値鏀寔", "鎶楁哀鍖?", "鎭㈠鏀寔", "楸兼补", "鐧借棞鑺﹂唶", "缁寸敓绱燙"),
                    query_terms=("鐐庣棁鏀寔", "鎶楃値鏀寔", "鎭㈠鏀寔", "鎶楁哀鍖?"),
                    marker_codes=("hs_crp",),
                )
            )

        if self._has_marker(context, "vitamin_d", "low"):
            profiles.append(
                SupportProfile(
                    profile_id="vitamin_d_repletion",
                    title="缁寸敓绱燚琛ュ厖鏀寔",
                    weight=1.25,
                    preferred_categories=("fat_soluble_support", "immune_support", "foundational_support"),
                    match_terms=("缁寸敓绱燚鏀寔", "楠ㄩ鏀寔", "鍏嶇柅鏀寔", "VD3", "K"),
                    query_terms=("缁寸敓绱燚鍋忎綆", "楠ㄩ鏀寔", "鍏嶇柅鏀寔", "缁寸敓绱燚鏀寔"),
                    marker_codes=("vitamin_d",),
                )
            )

        if self._has_thyroid_pattern(context):
            profiles.append(
                SupportProfile(
                    profile_id="thyroid_axis",
                    title="鐢茬姸鑵鸿酱鏀寔",
                    weight=1.3,
                    preferred_categories=("thyroid_support", "antioxidant_support"),
                    match_terms=("鐢茬姸鑵烘敮鎸?", "鍩虹浠ｈ阿鏀寔", "鎶楁哀鍖?", "纭?", "缁寸敓绱燛"),
                    query_terms=("鐢茬姸鑵烘敮鎸?", "鐢茬姸鑵烘姉浣?", "鍩虹浠ｈ阿鏀寔", "纭?"),
                    marker_codes=("tsh", "thyroglobulin_antibody", "thyroid_peroxidase_antibody"),
                )
            )

        if self._has_marker(context, "homocysteine", "high"):
            profiles.append(
                SupportProfile(
                    profile_id="methylation",
                    title="鐢插熀鍖栦笌蹇冭绠℃敮鎸?",
                    weight=1.15,
                    preferred_categories=("methylation_support", "foundational_support", "energy_support"),
                    match_terms=("鐢插熀鍖栨敮鎸?", "蹇冭绠℃敮鎸?", "鍩虹缁寸敓绱犺ˉ鍏?", "B鏃?", "杈呴叾Q10"),
                    query_terms=("鐢插熀鍖栨敮鎸?", "HCY绠＄悊", "蹇冭绠℃敮鎸?", "B鏃?"),
                    marker_codes=("homocysteine",),
                )
            )

        if any(self._has_marker(context, marker_code, "high") for marker_code in ("alt", "ast", "ggt")):
            profiles.append(
                SupportProfile(
                    profile_id="liver_detox",
                    title="鑲濊儐涓庤В姣掓敮鎸?",
                    weight=1.1,
                    preferred_categories=("detox_support", "digestive_support"),
                    match_terms=("瑙ｆ瘨鏀寔", "鑲濊剰鏀寔", "鑲濊儐鏀寔", "鑳嗘眮鍒嗘硨鏀寔"),
                    query_terms=("鑲濊剰鏀寔", "鑲濊儐鏀寔", "瑙ｆ瘨鏀寔", "鑳嗘眮鍒嗘硨"),
                    marker_codes=("alt", "ast", "ggt"),
                )
            )

        if "sleep_recovery" in context.lifestyle_tags or "stress_support" in context.lifestyle_tags:
            profiles.append(
                SupportProfile(
                    profile_id="sleep_stress",
                    title="鐫＄湢涓庡帇鍔涙仮澶?",
                    weight=0.9,
                    preferred_categories=("sleep_support", "stress_support", "mineral_support"),
                    match_terms=("鐫＄湢鎭㈠", "鍘嬪姏绠＄悊", "鎯呯华骞宠　", "鍏ョ潯鍥伴毦", "澶滈棿瑙夐啋"),
                    query_terms=("鐫＄湢鎭㈠", "鍘嬪姏绠＄悊", "鎯呯华骞宠　"),
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

        return f"宸插鏍哥煡璇嗭細{statement.topic}"

    def _signal_label(self, signal_id: str) -> str:
        labels = {
            "lipid_balance": "血脂与心血管支持",
            "iron_repletion": "缺铁与造血支持",
            "glycemic_balance": "琛€绯栦笌浠ｈ阿鏀寔",
            "inflammation_resolution": "炎症与恢复支持",
            "vitamin_d_repletion": "缁寸敓绱燚琛ュ厖鏀寔",
            "thyroid_axis": "鐢茬姸鑵鸿酱鏀寔",
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
        if kind == "pattern":
            return self._matches_pattern(value, context)
        return False

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

    def _build_product_reason(self, product: ProductRule, evidence_ids: list[str]) -> str:
        use_case = "、".join(product.candidate_use_cases[:2]) if product.candidate_use_cases else "当前病例目标"
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
        for key in ("总体健康画像", "系统功能深度分析", "生活方式干预重点", "功能医学检测建议", "随访计划"):
            values = overrides.get(key, [])
            cleaned_values = []
            for item in values:
                if not isinstance(item, str):
                    continue
                cleaned = self._sanitize_report_line(item)
                if cleaned:
                    cleaned_values.append(cleaned)
            if cleaned_values:
                merged[key] = list(dict.fromkeys(cleaned_values))
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

    def _build_case_summary(self, case) -> list[str]:
        questionnaire = case.questionnaire
        summary_nutrient_hints = self._extract_summary_nutrient_hints(case.clinical_summary_text)
        summary = [f"客户姓名: {case.customer_name}"]
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
            highlights.append(
                f"{indicator.indicator_name}: {indicator.result_text}（{status_labels.get(indicator.status.value, indicator.status.value)}）"
            )
        return list(dict.fromkeys(highlights))

    def _build_health_portrait(
        self,
        case,
        context: RecommendationContext,
        key_lab_highlights: list[str],
        red_flags: list[str],
        report_guidance: list[str] | None = None,
    ) -> list[str]:
        questionnaire = case.questionnaire
        portrait: list[str] = []

        if questionnaire and questionnaire.chief_concerns:
            portrait.append(f"主要诉求：{'、'.join(questionnaire.chief_concerns[:3])}")
        if questionnaire and questionnaire.symptoms:
            portrait.append(f"主要症状：{'、'.join(questionnaire.symptoms[:4])}")

        msq_burdens = self._top_msq_burdens(context.msq_system_scores)
        if msq_burdens:
            portrait.append(f"MSQ 系统负担：{'、'.join(msq_burdens[:4])}")

        if self._has_thyroid_pattern(context):
            portrait.append("整体画像提示甲状腺相关免疫或功能轴值得优先关注，建议同步考虑炎症、压力节律与恢复能力。")
        if "gut_support" in context.lifestyle_tags:
            portrait.append("问卷与症状提示肠道与消化支持可能参与当前问题表现。")
        if "stress_support" in context.lifestyle_tags or "sleep_recovery" in context.lifestyle_tags:
            portrait.append("睡眠恢复和压力调节是当前方案中的基础优先级。")
        if "sedentary_risk" in context.lifestyle_tags:
            portrait.append("久坐与活动不足提示代谢和能量系统需要同步重建。")
        if red_flags:
            portrait.append("当前存在需要人工优先复核的风险信号，最终建议以顾问审核为准。")
        elif key_lab_highlights:
            portrait.append("当前草案以异常指标、症状负担与已审核知识命中为主线进行整理。")
        if report_guidance:
            portrait.append("原体检报告中的阳性结果、检查小结和专家建议已纳入本次综合分析。")
        if case.clinical_summary_text:
            portrait.append("人工录入的病例总结诊断已一并纳入当前综合分析与方案排序。")
        if context.summary_nutrient_hints:
            portrait.append("病例总结中列出的所需营养素已作为当前产品候选排序与报告整理的参考。")

        return list(dict.fromkeys(portrait))[:6]

    def _build_system_analysis(
        self,
        case,
        context: RecommendationContext,
        key_lab_highlights: list[str],
        report_guidance: list[str] | None = None,
    ) -> list[str]:
        analysis: list[str] = []
        questionnaire = case.questionnaire

        if self._has_thyroid_pattern(context):
            analysis.append("甲状腺免疫/功能轴：现有指标或病史提示甲状腺相关风险，需同步关注炎症负担、压力节律与营养支持。")

        if self._has_marker(context, "ferritin", "low"):
            analysis.append("铁储备：铁蛋白偏低提示储备不足，可能与疲劳、恢复差、头晕或注意力下降有关。")

        if self._has_marker(context, "vitamin_d", "low"):
            analysis.append("免疫与骨代谢：维生素 D 偏低时，免疫调节、骨骼支持与整体恢复能力往往会受到影响。")

        if self._has_marker(context, "hs_crp", "high"):
            analysis.append("炎症负担：高敏 CRP 升高提示炎症活跃度偏高，方案中应提高抗炎与恢复支持优先级。")

        if "gut_support" in context.lifestyle_tags or questionnaire and questionnaire.food_sensitivities:
            analysis.append("肠道功能：腹胀、排便波动、食物敏感或外食偏多时，往往需要先处理消化道负担和饮食触发因素。")

        if "energy_support" in context.lifestyle_tags or self._normalize("疲劳") in context.symptoms:
            analysis.append("能量代谢：疲劳、活动后恢复慢或晨起乏力时，应同步关注线粒体支持、睡眠质量与营养缺口。")

        if "sedentary_risk" in context.lifestyle_tags or self._has_marker(context, "fasting_glucose", "high"):
            analysis.append("代谢风险：久坐、活动不足或血糖指标波动时，需要把稳定血糖和逐步恢复运动能力纳入方案。")

        guidance_text = " ".join(report_guidance or [])
        if "乳腺增生" in guidance_text:
            analysis.append("乳腺健康：原报告提示乳腺增生时，生活方式部分需同步关注压力、睡眠、体重管理和高脂饮食控制。")
        if "尿抗坏血酸" in guidance_text:
            analysis.append("尿检复核：原报告提示尿抗坏血酸增高，可能影响部分尿干化学项目，建议结合复查结果再判断泌尿系统风险。")
        if "心律不齐" in guidance_text:
            analysis.append("心血管节律：原报告提示窦性心律不齐，如有心悸、胸闷或运动不适，应优先转临床复核。")
        if any(
            term in context.clinical_summary_text
            for term in ("脂肪酸代谢不佳", "脂代谢异常", "脂肪燃烧")
        ):
            analysis.append("脂肪酸代谢：人工评估提示脂代谢或脂肪燃烧效率偏弱时，方案中应提高心血管与代谢支持优先级。")
        if any(
            term in context.clinical_summary_text
            for term in ("碳水化合物代谢不佳", "胰岛素抵抗", "2型糖尿病", "血糖")
        ):
            analysis.append("碳水代谢：人工评估提示碳水代谢效率下降时，需同步关注血糖稳定、外食结构和活动恢复。")
        if any(
            term in context.clinical_summary_text
            for term in ("细胞能量生成反应不佳", "细胞能量生成不佳", "线粒体", "活动力下降", "认知功能衰退")
        ):
            analysis.append("细胞能量：人工评估提示线粒体或细胞能量生成支持不足时，可优先考虑能量与抗氧化支持。")
        if context.summary_nutrient_hints:
            analysis.append(
                f"营养素方向：病例总结中已明确提示 {'、'.join(context.summary_nutrient_hints[:10])} 等所需营养素，本次草案已将其作为候选营养方案排序参考。"
            )

        if not analysis and key_lab_highlights:
            analysis.append("当前已根据异常指标和已审核知识，整理出需要优先干预的系统方向。")

        return list(dict.fromkeys(analysis))[:6]

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
    ) -> list[ProductRule]:
        if not selected_sku_ids:
            return ranked_products[:5]

        by_id = {product.sku_id: product for product in ranked_products}
        selected_products: list[ProductRule] = []
        for sku_id in selected_sku_ids:
            product = by_id.get(sku_id)
            if product and product not in selected_products:
                selected_products.append(product)

        return selected_products or ranked_products[:5]

    def _normalize(self, value: str) -> str:
        return "".join(value.lower().split())

    def _contains_cjk(self, value: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in value)

