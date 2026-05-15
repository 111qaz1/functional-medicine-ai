from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.settings import AppSettings
from app.domain.models import ClinicianRule, RecommendationDraft
from app.providers.remote import OpenAICompatibleCaseAssistant
from app.repositories.in_memory import LocalRepository
from app.services.assistant_rules import ClinicianRuleService
from app.services.case_service import CaseService
from app.services.indicator_extraction import CaseIndicatorService


@dataclass
class AssistantChatTurn:
    role: str
    text: str


@dataclass
class AssistantChatResult:
    reply: str
    mode: str
    model_label: str


class CaseAssistantService:
    def __init__(
        self,
        *,
        settings: AppSettings,
        repository: LocalRepository,
        case_service: CaseService,
        indicator_service: CaseIndicatorService,
        assistant_rule_service: ClinicianRuleService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.case_service = case_service
        self.indicator_service = indicator_service
        self.assistant_rule_service = assistant_rule_service
        self.remote_assistant = self._build_remote_assistant()

    def reply(
        self,
        *,
        case_id: str,
        user_message: str,
        history: list[AssistantChatTurn] | None = None,
    ) -> AssistantChatResult:
        case = self.case_service.get_case(case_id)
        latest_draft = self.repository.get_draft(case.draft_ids[-1]) if case.draft_ids else None
        review = self.repository.get_review_decision(case.draft_ids[-1]) if case.draft_ids else None
        indicators = self.indicator_service.build(case)
        matched_rules = self.assistant_rule_service.match_rules_for_case(case)
        case_snapshot = self._build_case_snapshot(
            case=case,
            indicators=indicators,
            latest_draft=latest_draft,
            matched_rules=matched_rules,
            review=review,
        )

        if self.remote_assistant:
            try:
                reply = self.remote_assistant.reply(
                    case_snapshot=case_snapshot,
                    user_message=user_message,
                    history=history or [],
                )
                return AssistantChatResult(
                    reply=reply,
                    mode="llm",
                    model_label=f"remote:{self.settings.llm_model}",
                )
            except Exception:
                pass

        return AssistantChatResult(
            reply=self._build_local_reply(
                user_message=user_message,
                case_snapshot=case_snapshot,
                latest_draft=latest_draft,
                matched_rules=matched_rules,
                review_exists=review is not None,
            ),
            mode="local",
            model_label="local-case-assistant-v1",
        )

    def _build_remote_assistant(self) -> OpenAICompatibleCaseAssistant | None:
        if not (self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model):
            return None

        return OpenAICompatibleCaseAssistant(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            model=self.settings.llm_model,
            api_style=self.settings.llm_api_style,
            timeout_seconds=self.settings.llm_timeout_seconds,
            temperature=min(max(self.settings.llm_temperature, 0.0), 0.5),
        )

    def _build_case_snapshot(
        self,
        *,
        case,
        indicators,
        latest_draft: RecommendationDraft | None,
        matched_rules: list[ClinicianRule],
        review,
    ) -> dict[str, Any]:
        questionnaire = case.questionnaire
        file_summaries = []
        reviewed_segments: list[str] = []
        for file in case.files[:4]:
            corrected_text = (file.corrected_text or file.raw_extracted_text or "").strip()
            if corrected_text:
                reviewed_segments.append(corrected_text[:1000])
            file_summaries.append(
                {
                    "filename": file.filename,
                    "parse_status": getattr(file.parse_status, "value", str(file.parse_status)),
                    "parse_confidence": file.parse_confidence,
                    "missing_fields": file.missing_fields[:8],
                }
            )

        indicator_payload = [
            {
                "indicator_name": item.indicator_name,
                "result_text": item.result_text,
                "status": getattr(item.status, "value", str(item.status)),
                "category": item.category,
                "evidence_snippet": item.source_span.snippet[:160],
            }
            for item in indicators[:24]
        ]

        draft_payload: dict[str, Any] | None = None
        if latest_draft:
            draft_payload = {
                "status": getattr(latest_draft.status, "value", str(latest_draft.status)),
                "confidence": latest_draft.confidence,
                "abstain_reason": latest_draft.abstain_reason,
                "key_lab_highlights": latest_draft.key_lab_highlights[:12],
                "rationale": latest_draft.rationale[:8],
                "red_flags": latest_draft.red_flags[:8],
                "missing_info": latest_draft.missing_info[:10],
                "recommended_skus": [
                    {
                        "sku_id": item.sku_id,
                        "display_name": item.display_name,
                        "dosage": item.dosage,
                        "reason": item.reason,
                        "warnings": item.warnings[:4],
                    }
                    for item in latest_draft.recommended_skus[:8]
                ],
                "lifestyle_actions": latest_draft.lifestyle_actions[:8],
            }

        return {
            "case_id": case.id,
            "customer_name": case.customer_name,
            "analysis_mode": getattr(case.analysis_mode, "value", str(case.analysis_mode)),
            "case_status": getattr(case.status, "value", str(case.status)),
            "parsing_review_completed": case.parsing_review_completed,
            "questionnaire_completed": questionnaire is not None,
            "questionnaire_summary": {
                "age": questionnaire.age if questionnaire else None,
                "sex": questionnaire.sex if questionnaire else "unknown",
                "chief_concerns": questionnaire.chief_concerns[:6] if questionnaire else [],
                "symptoms": questionnaire.symptoms[:8] if questionnaire else [],
                "known_conditions": questionnaire.known_conditions[:6] if questionnaire else [],
                "goals": questionnaire.goals[:6] if questionnaire else [],
                "medications": questionnaire.medications[:6] if questionnaire else [],
                "allergies": questionnaire.allergies[:6] if questionnaire else [],
                "stress_level": questionnaire.stress_level if questionnaire else None,
                "sleep_hours": questionnaire.sleep_hours if questionnaire else None,
                "sleep_quality": questionnaire.sleep_quality if questionnaire else None,
            },
            "file_count": len(case.files),
            "file_summaries": file_summaries,
            "indicators": indicator_payload,
            "latest_draft": draft_payload,
            "review_published": review is not None,
            "matched_rules": [
                {
                    "title": rule.title,
                    "enabled": rule.enabled,
                    "action": getattr(rule.action, "value", str(rule.action)),
                    "target_sku_ids": rule.target_sku_ids[:5],
                    "notes": rule.notes,
                }
                for rule in matched_rules[:8]
            ],
            "reviewed_report_excerpt": "\n".join(reviewed_segments)[:2400],
        }

    def _build_local_reply(
        self,
        *,
        user_message: str,
        case_snapshot: dict[str, Any],
        latest_draft: RecommendationDraft | None,
        matched_rules: list[ClinicianRule],
        review_exists: bool,
    ) -> str:
        normalized = self._normalize(user_message)

        if self._includes_any(normalized, ["总结当前病例", "总结病例", "概括当前病例", "概括一下"]):
            return self._build_case_summary_reply(case_snapshot, latest_draft)

        if self._includes_any(normalized, ["为什么当前这样推荐", "为什么这样推荐", "推荐原因", "草案为什么", "为何这样推荐"]):
            return self._build_draft_reason_reply(latest_draft)

        if self._includes_any(normalized, ["营养素", "推荐什么", "推荐哪些", "产品建议", "补充剂"]):
            return self._build_recommendation_status_reply(latest_draft)

        if self._includes_any(normalized, ["命中规则", "当前规则", "有哪些规则", "查看规则"]):
            return self._build_rule_reply(matched_rules)

        if self._includes_any(normalized, ["关键指标", "异常指标", "指标情况", "检验指标", "化验指标"]):
            return self._build_indicator_reply(case_snapshot)

        if self._includes_any(normalized, ["下一步", "后续怎么做", "接下来怎么做", "下一步做什么"]):
            return self._build_next_step_reply(case_snapshot, latest_draft, review_exists)

        return self._build_generic_reply(case_snapshot, latest_draft, matched_rules)

    def _build_case_summary_reply(self, case_snapshot: dict[str, Any], latest_draft: RecommendationDraft | None) -> str:
        indicator_lines = self._top_indicator_lines(case_snapshot, alert_only=True, limit=4)
        summary = [
            f"当前病例：{case_snapshot['customer_name']}，已上传 {case_snapshot['file_count']} 份资料，人工校对"
            f"{'已完成' if case_snapshot['parsing_review_completed'] else '尚未完成'}。",
            "目前优先关注："
            + ("；".join(indicator_lines) if indicator_lines else "暂未形成明确的重点异常指标。"),
        ]
        if latest_draft:
            if latest_draft.abstain_reason:
                summary.append(f"当前草案状态为 {latest_draft.status.value}，主要原因：{latest_draft.abstain_reason}")
            elif latest_draft.recommended_skus:
                sku_names = "、".join(item.display_name for item in latest_draft.recommended_skus[:4])
                summary.append(f"当前草案已形成营养素建议，主要包括：{sku_names}。")
            else:
                summary.append("当前草案已生成，但还没有形成明确的营养素组合。")
        else:
            summary.append("目前还没有生成结构化草案。")
        return "\n".join(summary)

    def _build_indicator_reply(self, case_snapshot: dict[str, Any]) -> str:
        indicators = case_snapshot["indicators"]
        if not indicators:
            return "当前还没有可用的关键指标。建议先完成报告上传与人工校对，再继续解读。"

        alerts = [
            f"{item['indicator_name']} {item['result_text']}"
            for item in indicators
            if item["status"] in {"attention", "positive"}
        ][:6]
        normals = [
            f"{item['indicator_name']} {item['result_text']}"
            for item in indicators
            if item["status"] == "normal"
        ][:4]

        lines = []
        if alerts:
            lines.append("当前优先关注的指标有：" + "；".join(alerts) + "。")
        if normals:
            lines.append("同时，已识别到相对平稳的指标有：" + "；".join(normals) + "。")
        if not lines:
            lines.append("当前识别到的指标以信息项为主，还没有明显需要优先处理的异常。")
        return "\n".join(lines)

    def _build_draft_reason_reply(self, latest_draft: RecommendationDraft | None) -> str:
        if not latest_draft:
            return "当前还没有生成结构化草案。建议先完成人工校对，然后点击“生成结构化草案”。"

        if latest_draft.abstain_reason:
            return (
                f"当前没有直接发布营养素组合，主要原因是：{latest_draft.abstain_reason}\n"
                "如果要继续推进，优先补齐问卷、用药、过敏史或继续人工补充关键判断。"
            )

        if latest_draft.recommended_skus:
            lines = ["当前推荐主要是根据已校对指标、病例信息和已命中的规则综合整理出来的："]
            for item in latest_draft.recommended_skus[:4]:
                lines.append(f"{item.display_name}：{item.reason}")
            return "\n".join(lines)

        return "当前草案已生成，但还没有形成明确的营养素推荐组合。"

    def _build_recommendation_status_reply(self, latest_draft: RecommendationDraft | None) -> str:
        if not latest_draft:
            return "当前还没有生成草案，所以系统还没有给出正式的营养素推荐。"

        if latest_draft.abstain_reason:
            return f"当前草案仍是谨慎拒答状态，原因是：{latest_draft.abstain_reason}"

        if latest_draft.recommended_skus:
            lines = ["当前草案里的营养素推荐包括："]
            for item in latest_draft.recommended_skus[:5]:
                lines.append(f"{item.display_name}：{item.dosage}")
            return "\n".join(lines)

        return "当前草案暂时没有给出明确的营养素组合。"

    def _build_rule_reply(self, matched_rules: list[ClinicianRule]) -> str:
        if not matched_rules:
            return "当前病例还没有命中已沉淀的医生规则。你也可以直接说“以后遇到类似病例，优先加入某产品”，系统会把它沉淀成规则。"

        lines = [f"当前命中的医生规则有 {len(matched_rules)} 条："]
        for rule in matched_rules[:4]:
            action = "优先/增强" if rule.action.value == "boost" else "谨慎/抑制"
            lines.append(f"{rule.title}：{action}；目标产品 {', '.join(rule.target_sku_ids[:4])}")
        return "\n".join(lines)

    def _build_next_step_reply(
        self,
        case_snapshot: dict[str, Any],
        latest_draft: RecommendationDraft | None,
        review_exists: bool,
    ) -> str:
        if case_snapshot["file_count"] == 0:
            return "下一步先上传体检报告或病例资料，系统解析后我再继续帮你整理。"
        if not case_snapshot["parsing_review_completed"]:
            return "下一步建议先完成“解析校对”。只有人工确认后的病例数据，后面的推荐和报告才更稳。"
        if not latest_draft:
            return "下一步可以直接点击“生成结构化草案”。"
        if not review_exists:
            return "下一步建议先审核当前草案，必要时补充问卷或医生经验，再执行“审核并发布”。"
        return "当前病例已经完成审核发布。下一步可以导出 PDF，或继续把这次经验沉淀成可复用规则。"

    def _build_generic_reply(
        self,
        case_snapshot: dict[str, Any],
        latest_draft: RecommendationDraft | None,
        matched_rules: list[ClinicianRule],
    ) -> str:
        lines = []
        alerts = self._top_indicator_lines(case_snapshot, alert_only=True, limit=3)
        if alerts:
            lines.append("当前最值得优先看的指标是：" + "；".join(alerts) + "。")

        if latest_draft:
            if latest_draft.abstain_reason:
                lines.append(f"当前草案仍偏保守，主要因为：{latest_draft.abstain_reason}")
            elif latest_draft.recommended_skus:
                lines.append(
                    "当前草案已经形成的营养素方向包括："
                    + "、".join(item.display_name for item in latest_draft.recommended_skus[:4])
                    + "。"
                )

        if matched_rules:
            lines.append(f"另外，这个病例目前还命中了 {len(matched_rules)} 条医生规则。")

        if not lines:
            lines.append("我已经接入当前病例上下文，但这个问题还需要结合更具体的问法来解释。")

        lines.append("你可以继续直接问我：为什么当前这样推荐、当前哪些指标最关键、或者下一步怎么处理。")
        return "\n".join(lines)

    def _top_indicator_lines(
        self,
        case_snapshot: dict[str, Any],
        *,
        alert_only: bool,
        limit: int,
    ) -> list[str]:
        items = case_snapshot["indicators"]
        results = []
        for item in items:
            if alert_only and item["status"] not in {"attention", "positive"}:
                continue
            results.append(f"{item['indicator_name']} {item['result_text']}")
            if len(results) >= limit:
                break
        return results

    def _normalize(self, value: str) -> str:
        return "".join((value or "").lower().split())

    def _includes_any(self, value: str, tokens: list[str]) -> bool:
        return any(token in value for token in tokens)

    def debug_snapshot(self, case_id: str) -> str:
        case = self.case_service.get_case(case_id)
        latest_draft = self.repository.get_draft(case.draft_ids[-1]) if case.draft_ids else None
        review = self.repository.get_review_decision(case.draft_ids[-1]) if case.draft_ids else None
        indicators = self.indicator_service.build(case)
        matched_rules = self.assistant_rule_service.match_rules_for_case(case)
        snapshot = self._build_case_snapshot(
            case=case,
            indicators=indicators,
            latest_draft=latest_draft,
            matched_rules=matched_rules,
            review=review,
        )
        return json.dumps(snapshot, ensure_ascii=False, indent=2)
