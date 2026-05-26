from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.domain.models import AuditLog, DraftStatus, ReviewDecision
from app.repositories.in_memory import LocalRepository
from app.services.case_service import CaseService
from app.services.indicator_extraction import CaseIndicatorService
from app.services.pdf_export import PdfReportExporter


class ReviewService:
    def __init__(
        self,
        repository: LocalRepository,
        case_service: CaseService,
        indicator_service: CaseIndicatorService,
        pdf_exporter: PdfReportExporter,
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.indicator_service = indicator_service
        self.pdf_exporter = pdf_exporter

    def approve(
        self,
        draft_id: str,
        *,
        reviewer_id: str,
        publishable_summary: str | None,
        edits: dict[str, str],
    ) -> ReviewDecision:
        draft = self.repository.get_draft(draft_id)
        if not draft:
            raise KeyError(f"Draft {draft_id} not found")

        case = self.case_service.get_case(draft.case_id)
        draft.status = DraftStatus.approved
        report = self._select_publishable_report(draft, case, publishable_summary)
        pdf_path = self.pdf_exporter.export(
            draft_id=draft_id,
            customer_name=case.customer_name,
            report_text=report,
        )

        audit_log = self.repository.add_audit_log(
            AuditLog(
                id=f"audit_{uuid.uuid4().hex[:12]}",
                entity_type="draft",
                entity_id=draft_id,
                action="draft_approved",
                actor_id=reviewer_id,
                payload={
                    "edits": edits,
                    "publishable_summary": report,
                    "pdf_report_path": str(pdf_path),
                },
            )
        )
        review = ReviewDecision(
            draft_id=draft_id,
            reviewer_id=reviewer_id,
            edits=edits,
            final_status=DraftStatus.approved,
            publishable_report=report,
            pdf_report_path=str(pdf_path),
            pdf_report_filename=pdf_path.name,
            audit_log_id=audit_log.id,
        )
        self.repository.save_draft(draft)
        self.repository.save_review_decision(review)
        self.case_service.mark_approved(draft.case_id)
        return review

    def ensure_pdf(self, draft_id: str) -> tuple[Path, str]:
        draft = self.repository.get_draft(draft_id)
        if not draft:
            raise KeyError(f"Draft {draft_id} not found")

        review = self.repository.get_review_decision(draft_id)
        if not review:
            raise KeyError(f"Review decision for draft {draft_id} not found")

        case = self.case_service.get_case(draft.case_id)
        report = review.publishable_report
        if self._looks_like_internal_generated_report(report):
            report = self._render_report(draft, case)
            review.publishable_report = report
        pdf_path = self.pdf_exporter.export(
            draft_id=draft_id,
            customer_name=case.customer_name,
            report_text=report,
        )
        review.pdf_report_path = str(pdf_path)
        review.pdf_report_filename = pdf_path.name
        self.repository.save_review_decision(review)
        return pdf_path, pdf_path.name

    def _select_publishable_report(self, draft, case, publishable_summary: str | None) -> str:
        if publishable_summary and publishable_summary.strip():
            if not self._looks_like_internal_generated_report(publishable_summary):
                return self._ensure_report_nutrition_safety(publishable_summary.strip(), draft)
        return self._render_report(draft, case)

    def _looks_like_internal_generated_report(self, report_text: str | None) -> bool:
        if not report_text:
            return False
        internal_markers = (
            "## 病例摘要",
            "## 证据来源",
            "## 审核备注",
            "## 审计信息",
            "分析模式:",
            "模型版本:",
        )
        return any(marker in report_text for marker in internal_markers)

    def _ensure_report_nutrition_safety(self, report_text: str, draft) -> str:
        lines = []
        in_nutrition_section = False
        nutrition_titles = {"个性化营养素方案", "营养素推荐", "推荐营养素"}

        for raw_line in report_text.splitlines():
            line = raw_line
            stripped = line.strip()
            if stripped.startswith("## "):
                in_nutrition_section = stripped[3:].strip() in nutrition_titles
            if in_nutrition_section and "注意/禁忌" not in line:
                line = self._append_matching_safety(line, draft.recommended_skus)
            lines.append(line)

        return "\n".join(lines).strip()

    def _render_report(self, draft, case) -> str:
        lines = ["# 功能医学营养与生活方式建议", ""]
        sections = draft.report_sections or {}
        abnormal_indicators = self._abnormal_indicators(case)
        nutrition_plan = self._nutrition_plan_with_safety(
            draft,
            sections.get("个性化营养素方案") or sections.get("营养素推荐"),
        )
        follow_up = self._customer_follow_up(sections)
        missing_info = self._customerize_items(sections.get("待确认项", draft.missing_info))

        ordered_sections = [
            ("总体健康画像", self._customer_health_portrait(case, abnormal_indicators)),
            ("关键指标", self._customer_key_indicators(abnormal_indicators)),
            ("风险提示", self._customerize_items(sections.get("风险提示", draft.red_flags))),
            ("个性化营养素方案", self._customerize_items(nutrition_plan)),
            ("生活方式干预重点", self._customer_lifestyle_focus(case, draft, abnormal_indicators)),
            ("复查与跟进建议", follow_up),
            ("需要补充确认", missing_info),
            ("重要提醒", self._customer_notice()),
        ]

        if draft.abstain_reason:
            lines.extend(["## 自动拒答原因", draft.abstain_reason, ""])

        for title, content in ordered_sections:
            items = self._as_list(content)
            if not items:
                continue
            lines.append(f"## {title}")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

        return "\n".join(lines)

    def _as_list(self, content) -> list[str]:
        if not content:
            return []
        if isinstance(content, str):
            return [content.strip()] if content.strip() else []
        return [str(item).strip() for item in content if str(item).strip()]

    def _abnormal_indicators(self, case) -> list:
        indicators = []
        seen: set[tuple[str, str]] = set()
        for indicator in self.indicator_service.build(case):
            status = getattr(indicator.status, "value", str(indicator.status))
            if status not in {"attention", "positive"}:
                continue
            signature = (indicator.indicator_name.strip(), indicator.result_text.strip())
            if signature in seen:
                continue
            seen.add(signature)
            indicators.append(indicator)
        return indicators

    def _customer_health_portrait(self, case, abnormal_indicators: list) -> list[str]:
        questionnaire = case.questionnaire
        focus_names = [indicator.indicator_name for indicator in abnormal_indicators[:5]]
        symptoms = list(getattr(questionnaire, "symptoms", []) or [])[:4] if questionnaire else []
        goals = list(getattr(questionnaire, "goals", []) or [])[:3] if questionnaire else []
        concerns = list(getattr(questionnaire, "chief_concerns", []) or [])[:3] if questionnaire else []

        parts: list[str] = []
        if focus_names:
            parts.append(f"从这次报告看，当前更值得优先关注的是 {'、'.join(focus_names)}。")
        else:
            parts.append("从这次报告看，暂时没有看到需要单独拎出来强调的异常指标。")
        if symptoms:
            parts.append(f"结合您提到的 {'、'.join(symptoms)}，建议把精力、代谢、睡眠和恢复状态放在一起看。")
        if concerns or goals:
            target = "、".join(concerns or goals)
            parts.append(f"接下来的方案会围绕“{target}”这个目标，先从最容易执行的生活习惯开始。")
        parts.append("整体思路不是一次性做很多事，而是先把饮食结构、作息、压力和活动量这几个底盘稳定下来，再根据身体反应逐步调整营养素方案。")
        return ["".join(parts)]

    def _customer_key_indicators(self, abnormal_indicators: list) -> list[str]:
        if not abnormal_indicators:
            return ["本次未识别到需要重点展示的异常指标，后续以复查和症状变化继续跟踪即可。"]

        items: list[str] = []
        for indicator in abnormal_indicators:
            name = indicator.indicator_name.strip()
            result = indicator.result_text.strip()
            status_label = self._friendly_indicator_status(indicator)
            explanation = self._indicator_explanation(indicator)
            items.append(f"{name}：{result}（{status_label}）。说明：{explanation}")
        return items

    def _friendly_indicator_status(self, indicator) -> str:
        text = f"{indicator.indicator_name} {indicator.result_text} {getattr(indicator.source_span, 'snippet', '')}"
        if any(token in text for token in ("↓", "偏低", "降低", "低于", "不足")):
            return "偏低"
        if any(token in text for token in ("↑", "偏高", "升高", "增高", "高于")):
            return "偏高"
        status = getattr(indicator.status, "value", str(indicator.status))
        if status == "positive":
            return "阳性/异常"
        return "需关注"

    def _indicator_explanation(self, indicator) -> str:
        name = indicator.indicator_name.lower()
        text = f"{indicator.indicator_name} {indicator.result_text} {getattr(indicator.source_span, 'snippet', '')}".lower()
        if "25" in name and ("维生素d" in name or "vitamin d" in name) or "羟维生素d" in name:
            return "维生素D和免疫调节、骨骼健康、情绪与整体恢复有关，偏低时可把规律日晒、饮食来源和营养补充一起纳入计划。"
        if "体质指数" in name or "bmi" in name:
            return "提示体重和体脂管理压力增加，建议重点观察腰围、餐盘结构、运动量和睡眠节律。"
        if "腰围" in name:
            return "腰围偏高通常提示腹部脂肪压力增加，和血糖、血脂、脂肪肝及炎症负担都有关。"
        if "血压" in name or "收缩压" in name or "舒张压" in name:
            return "血压偏离理想范围时，需要结合头晕、乏力、心悸、饮水量和用药情况一起判断。"
        if "尿素" in name or "bun" in name or "urea" in name:
            return "尿素受蛋白摄入、水分状态和肾脏排泄影响，建议避免极端高蛋白或脱水，并结合肌酐、尿酸等指标一起看。"
        if any(token in name for token in ("胆固醇", "甘油三酯", "低密度", "载脂蛋白", "ldl", "tg", "tc")):
            return "这类指标反映血脂和心血管代谢压力，建议和饮食油脂质量、精制碳水、运动量及腰围变化一起管理。"
        if any(token in name for token in ("血糖", "葡萄糖", "糖化", "胰岛素", "hba1c", "glucose")):
            return "提示血糖稳定性需要关注，餐盘顺序、主食份量、饭后活动和睡眠都会影响后续变化。"
        if any(token in name for token in ("crp", "炎症", "白细胞", "中性粒")):
            return "提示身体可能处在炎症或应激状态，近期可优先做好抗炎饮食、睡眠恢复和压力管理。"
        if any(token in name for token in ("铁蛋白", "血清铁", "血红蛋白", "铁", "ferritin")):
            return "这类指标和铁储备、氧运输、疲劳及注意力有关，是否补铁需要结合完整铁代谢和医生评估。"
        if any(token in name for token in ("甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab")):
            return "甲状腺相关指标会影响代谢、体温、精力和情绪，建议同步关注压力、睡眠、硒锌铁状态和碘摄入是否合适。"
        if "尿酸" in name:
            return "尿酸和嘌呤代谢、饮水量、酒精/含糖饮料、肾脏排泄有关，可先从饮食和水分管理入手。"
        if any(token in name for token in ("alt", "ast", "ggt", "转氨酶", "胆红素")):
            return "这类指标和肝胆代谢、酒精、药物、脂肪肝及近期压力有关，建议减少肝脏负担并按需复查。"
        if "同型半胱氨酸" in name or "hcy" in name:
            return "同型半胱氨酸偏高和B族维生素、甲基化及心血管管理有关，需要结合B12、叶酸和生活方式一起调整。"
        if "镁" in name:
            return "镁与神经肌肉、睡眠和心律有关；异常时要同时确认肾功能、补剂使用和近期输液情况。"
        if "阳性" in text:
            return "阳性结果提示需要结合症状和其他检查进一步判断，不建议只凭单项结果下结论。"
        return "该指标已经偏离参考范围，建议结合症状、相关指标和复查趋势一起跟踪，暂不只凭单项结果下结论。"

    def _nutrition_plan_from_skus(self, draft) -> list[str]:
        items = []
        for sku in draft.recommended_skus:
            safety_note = self._public_safety_note(getattr(sku, "warnings", []))
            safety_suffix = f"。注意/禁忌：{safety_note}" if safety_note else ""
            items.append(f"{sku.display_name}：{sku.dosage}。目的：{sku.reason}{safety_suffix}")
        return items

    def _nutrition_plan_with_safety(self, draft, content) -> list[str]:
        items = self._as_list(content) or self._nutrition_plan_from_skus(draft)
        return [self._append_matching_safety(item, draft.recommended_skus) for item in items]

    def _append_matching_safety(self, item: str, recommended_skus: list) -> str:
        if "注意/禁忌" in item:
            return item
        for sku in recommended_skus:
            if getattr(sku, "display_name", "") and sku.display_name in item:
                safety_note = self._public_safety_note(getattr(sku, "warnings", []))
                if safety_note:
                    return f"{item.rstrip(' 。；')}。注意/禁忌：{safety_note}"
        return item

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

    def _customer_lifestyle_focus(self, case, draft, abnormal_indicators: list) -> list[str]:
        sections = draft.report_sections or {}
        protocol_items = self._protocol_lifestyle_items(case, abnormal_indicators)
        draft_items = self._customerize_items(sections.get("生活方式干预重点", draft.lifestyle_actions))
        return list(dict.fromkeys(protocol_items + draft_items))[:12]

    def _protocol_lifestyle_items(self, case, abnormal_indicators: list) -> list[str]:
        questionnaire = case.questionnaire
        names = " ".join(indicator.indicator_name for indicator in abnormal_indicators)
        symptoms = " ".join(getattr(questionnaire, "symptoms", []) or []) if questionnaire else ""
        conditions = " ".join(getattr(questionnaire, "known_conditions", []) or []) if questionnaire else ""
        goals = " ".join(getattr(questionnaire, "goals", []) or []) if questionnaire else ""
        combined = f"{names} {symptoms} {conditions} {goals}"

        items = [
            "饮食底盘：未来4-6周先按抗炎餐盘执行，每餐尽量做到半盘非淀粉蔬菜、1掌心优质蛋白、1拳头主食，烹调用橄榄油或蒸煮炖，减少油炸、甜食、酒精和深加工食品。",
            "执行节奏：首月不建议同时改太多，先选2-3条最容易做到的习惯连续执行2周，再逐步叠加下一步。",
        ]

        if self._contains_any(combined, ("体质指数", "腰围", "血糖", "糖化", "胰岛素", "胆固醇", "甘油三酯", "脂肪肝", "代谢", "尿酸")):
            items.extend(
                [
                    "血糖与体重管理：吃饭顺序尽量按“蔬菜先、蛋白和脂肪其次、主食最后”，主食优先选择全谷物、豆类或薯类，饭后散步15-20分钟。",
                    "心血管代谢：参考地中海和DASH饮食思路，增加深海鱼或相应替代、坚果、豆类和高纤维蔬菜，减少加工肉、高盐外食和含糖饮料。",
                ]
            )

        if self._contains_any(combined, ("甲状腺", "桥本", "tsh", "ft3", "ft4", "tpo", "tgab")):
            items.append("甲状腺友好：如有桥本、甲状腺抗体或甲减倾向，先避免自行高碘；十字花科蔬菜建议熟食，并观察麸质、乳制品是否会加重不适。")

        if self._contains_any(combined, ("维生素d", "25-", "免疫", "反复感染", "过敏")):
            items.append("免疫与恢复：白天规律户外光照，保证蛋白质和深色蔬菜摄入，减少过量糖分；如果需要补充维生素D，应结合复查结果调整。")

        if self._contains_any(combined, ("腹胀", "腹泻", "便秘", "肠", "食物敏感", "不耐受")):
            items.append("肠道修复：若腹胀、排便波动或食物敏感明显，可先做4周触发食物观察，减少超加工食品，同时记录饮食和症状变化。")

        if self._contains_any(combined, ("睡眠", "失眠", "疲劳", "焦虑", "压力", "情绪", "头痛")):
            items.extend(
                [
                    "睡眠修复：固定起床时间，晨起接触自然光15分钟；14点后减少咖啡因，睡前1小时减少屏幕和工作输入。",
                    "压力管理：每天安排2次5分钟呼吸练习或冥想，也可以用散步、哼唱、伸展来帮助身体从紧绷状态切换出来。",
                ]
            )

        if self._contains_any(combined, ("alt", "ast", "ggt", "转氨酶", "胆红素", "脂肪肝", "酒精", "化学敏感")):
            items.append("肝胆代谢：至少4周减少酒精和高果糖加工食品，增加十字花科蔬菜、洋葱蒜类、足量饮水和膳食纤维，帮助身体降低代谢负担。")

        if questionnaire and (questionnaire.sitting_hours_per_day or questionnaire.exercise_frequency):
            items.append("运动处方：从可持续的活动开始，每天增加步行和拉伸；稳定后逐步过渡到每周150分钟中等强度有氧，加每周2次抗阻训练。")
        elif self._contains_any(combined, ("体质指数", "腰围", "血糖", "胆固醇", "疲劳", "线粒体")):
            items.append("活动恢复：先从饭后走路、每天累计8000步左右或低强度骑行开始，避免一上来就做高强度训练。")

        if self._contains_any(combined, ("尿素", "bun", "urea", "尿酸", "血压", "舒张压")):
            items.append("水分和安全：保持规律饮水，避免极端高蛋白、过度断食或突然大量运动；如有头晕、心悸、水肿或血压异常，优先联系医生。")

        items.append("安全边界：如正在怀孕/哺乳、使用抗凝药、降糖药、甲状腺药或其他长期药物，任何饮食限制、禁食、排毒和补剂升级都应先让医生确认。")
        return list(dict.fromkeys(items))

    def _customer_follow_up(self, sections: dict) -> list[str]:
        test_items = self._customerize_items(sections.get("功能医学检测建议", []))
        follow_items = self._customerize_items(sections.get("随访计划", []))
        items = (test_items + follow_items)[:6]
        if not items:
            items = [
                "建议2周内回访一次，重点看睡眠、精力、胃肠反应和方案执行难点。",
                "建议8-12周后结合本次异常指标做复查，用趋势来判断方案是否需要调整。",
            ]
        return items

    def _customer_notice(self) -> list[str]:
        return [
            "本报告用于健康管理和营养生活方式指导，不能替代医学诊断或治疗。",
            "如果出现胸痛、持续高热、黑便/便血、明显水肿、严重头晕或其他急性不适，请及时就医。",
        ]

    def _customerize_items(self, content) -> list[str]:
        items = []
        for item in self._as_list(content):
            cleaned = re.sub(r"product:sku_[a-z0-9_]+", "", item, flags=re.IGNORECASE)
            cleaned = re.sub(r"statement_[a-z0-9_]+", "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.replace("当前草案", "当前方案")
            cleaned = cleaned.replace("候选推荐", "建议")
            cleaned = cleaned.replace("已审核知识命中", "本次资料提示")
            cleaned = cleaned.replace("人工复核", "顾问确认")
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；")
            if cleaned:
                items.append(cleaned)
        return list(dict.fromkeys(items))

    def _contains_any(self, text: str, tokens: tuple[str, ...]) -> bool:
        normalized = text.lower()
        return any(token.lower() in normalized for token in tokens)
