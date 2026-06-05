from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from app.domain.models import AuditLog, DraftStatus, ReviewDecision
from app.repositories.in_memory import LocalRepository
from app.services.case_service import CaseService
from app.services.indicator_extraction import CaseIndicatorService
from app.services.pdf_export import PdfReportExporter
from app.services.rag_safety import CUSTOMER_RAG_PREFIX


class ReviewService:
    def __init__(
        self,
        repository: LocalRepository,
        case_service: CaseService,
        indicator_service: CaseIndicatorService,
        pdf_exporter: PdfReportExporter,
        rag_fusion_provider: Any | None = None,
    ) -> None:
        self.repository = repository
        self.case_service = case_service
        self.indicator_service = indicator_service
        self.pdf_exporter = pdf_exporter
        self.rag_fusion_provider = rag_fusion_provider

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
        report = self._normalize_customer_visible_report_text(
            self._select_publishable_report(draft, case, publishable_summary)
        )
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
        report = self._normalize_customer_visible_report_text(report)
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
            if not self._looks_like_internal_generated_report(publishable_summary) and not self._looks_like_corrupted_publishable_report(publishable_summary):
                report = self._remove_customer_hidden_rag_labels(publishable_summary.strip())
                report = self._ensure_report_nutrition_safety(report, draft)
                return self._ensure_report_rag_enhancement(report, draft, case)
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

    def _looks_like_corrupted_publishable_report(self, report_text: str | None) -> bool:
        if not report_text:
            return False
        stripped = report_text.strip()
        question_count = stripped.count("?")
        replacement_count = stripped.count("\ufffd")
        cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in stripped)
        if replacement_count >= 3:
            return True
        if "????" in stripped and question_count >= 8:
            return True
        if question_count >= 20 and cjk_count < 20:
            return True
        if question_count / max(len(stripped), 1) > 0.12 and cjk_count < 50:
            return True
        return False

    def _remove_customer_hidden_rag_labels(self, report_text: str) -> str:
        cleaned = report_text
        hidden_markers = (
            CUSTOMER_RAG_PREFIX,
            "功能医学知识库（仅供参考）：",
            "功能医学知识库（仅供参考）",
            "功能医学知识库：",
            "功能医学知识库",
            "仅供参考",
            "RAG",
        )
        for marker in hidden_markers:
            cleaned = cleaned.replace(marker, "")
        return cleaned

    def _normalize_customer_visible_report_text(self, report_text: str | None) -> str:
        text = str(report_text or "").replace("\r\n", "\n").replace("\r", "\n")
        normalized_lines: list[str] = []
        for raw_line in text.split("\n"):
            line = self._normalize_report_inline_spacing(raw_line.strip())
            if not line:
                if normalized_lines and normalized_lines[-1] != "":
                    normalized_lines.append("")
                continue
            if line.startswith(("# ", "## ", "- ")):
                normalized_lines.append(line)
                continue
            if normalized_lines and normalized_lines[-1].startswith("- "):
                normalized_lines[-1] = self._normalize_report_line(
                    self._collapse_inline_soft_breaks(f"{normalized_lines[-1]}\n{line}")
                )
                continue
            normalized_lines.append(line)
        finalized_lines = [
            self._normalize_report_line(line) if line else "" for line in normalized_lines
        ]
        return "\n".join(finalized_lines).strip()

    def _collapse_inline_soft_breaks(self, text: str) -> str:
        collapsed = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        collapsed = re.sub(
            r"(?<=[\u4e00-\u9fffA-Za-z0-9）)%％])\s*\n+\s*(?=[\u4e00-\u9fffA-Za-z0-9（(%％‰℃°])",
            "",
            collapsed,
        )
        collapsed = re.sub(r"\s*\n+\s*", " ", collapsed)
        collapsed = re.sub(r"[ \t\f\v]+", " ", collapsed).strip()
        return self._normalize_report_inline_spacing(collapsed)

    def _normalize_report_line(self, text: str) -> str:
        normalized = self._normalize_report_inline_spacing(text)
        if not normalized or normalized.startswith(("# ", "## ")):
            return normalized
        prefix = "- " if normalized.startswith("- ") else ""
        content = normalized[2:].strip() if prefix else normalized
        if any(marker in content for marker in ("目的：", "适用说明：", "注意/禁忌：")):
            content = self._normalize_nutrition_item_punctuation(content)
        elif prefix and content and not re.search(r"[。！？；）)]$", content):
            content += "。"
        return f"{prefix}{content}" if prefix else content

    def _normalize_report_inline_spacing(self, text: str) -> str:
        normalized = re.sub(r"[ \t\f\v]+", " ", str(text or "")).strip()
        normalized = re.sub(r"\s*([，、。；：！？])\s*", r"\1", normalized)
        normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
        normalized = re.sub(
            r"(?<=[\u4e00-\u9fff])\s+(?=\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?\s*(?:%|％|‰|℃|°|次|个|颗|粒|片|周|天|小时|分钟|秒))",
            "",
            normalized,
        )
        normalized = re.sub(
            r"(\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?)\s+(%|％|‰|℃|°|次|个|颗|粒|片|周|天|小时|分钟|秒)",
            lambda match: re.sub(r"\s+", "", match.group(1)) + match.group(2),
            normalized,
        )
        normalized = re.sub(r"。+；", "；", normalized)
        normalized = re.sub(r"；+。", "；", normalized)
        normalized = re.sub(r"([，。；：！？、])\1+", r"\1", normalized)
        return normalized

    def _normalize_nutrition_item_punctuation(self, text: str) -> str:
        normalized = text.strip(" ，。；")
        normalized = re.sub(r"[。；]+(?=(?:目的|适用说明|注意/禁忌)：)", "；", normalized)
        normalized = re.sub(r"[。；]+(?=与[\u4e00-\u9fffA-Za-z0-9])", "；", normalized)
        normalized = re.sub(r"。+；", "；", normalized)
        normalized = re.sub(r"；+。", "；", normalized)
        normalized = re.sub(r"([，。；：！？、])\1+", r"\1", normalized)
        return normalized.rstrip(" ，。；") + "。"

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

    def _has_rag_sections(self, draft) -> bool:
        sections = draft.report_sections or {}
        return any(
            sections.get(key)
            for key in ("RAG总体健康画像", "RAG异常指标解释", "RAG生活方式干预", "RAG复查建议")
        )

    def _ensure_report_rag_enhancement(self, report_text: str, draft, case) -> str:
        if not self._has_rag_sections(draft):
            return report_text
        local_report = self._ensure_report_rag_enhancement_local(report_text, draft, case)
        remote_report = self._try_llm_rag_fusion(local_report, draft, case)
        return remote_report or local_report

    def _ensure_report_rag_enhancement_local(self, report_text: str, draft, case) -> str:
        sections = draft.report_sections or {}
        result = report_text
        abnormal_indicators = self._abnormal_indicators(case)

        for rag_item in self._customerize_items(sections.get("RAG总体健康画像", []))[:4]:
            clause = self._rag_customer_clause(rag_item, max_len=140, purpose="health")
            updated = self._append_clause_to_report_section_item(result, "总体健康画像", clause, item_index=0)
            if updated != result:
                result = updated
                break

        used_indicator_rows: set[int] = set()
        for rag_item in self._customerize_items(sections.get("RAG异常指标解释", []))[:6]:
            row_index = self._best_indicator_rag_match(rag_item, abnormal_indicators)
            if row_index is None:
                row_index = self._fallback_indicator_row_for_rag(rag_item, abnormal_indicators)
            if row_index is None or row_index in used_indicator_rows:
                continue
            clause = self._rag_customer_clause(rag_item, max_len=120, purpose="indicator")
            updated = self._append_clause_to_report_section_item(result, "关键指标", clause, item_index=row_index)
            if updated != result:
                used_indicator_rows.add(row_index)
                result = updated

        lifestyle_items = self._extract_report_section_items(result, "生活方式干预重点")
        used_lifestyle_rows: set[int] = set()
        for rag_item in self._customerize_items(sections.get("RAG生活方式干预", []))[:5]:
            if not self._is_lifestyle_rag_item(rag_item):
                continue
            row_index = self._best_lifestyle_row(rag_item, lifestyle_items)
            if row_index is None and lifestyle_items:
                row_index = 0
            if row_index is None or row_index in used_lifestyle_rows:
                continue
            clause = self._rag_customer_clause(rag_item, max_len=115, purpose="lifestyle")
            updated = self._append_clause_to_report_section_item(result, "生活方式干预重点", clause, item_index=row_index)
            if updated != result:
                used_lifestyle_rows.add(row_index)
                result = updated

        follow_items = self._extract_report_section_items(result, "复查与跟进建议")
        used_follow_rows: set[int] = set()
        for rag_item in self._customerize_items(sections.get("RAG复查建议", []))[:5]:
            row_index = self._best_follow_up_row(rag_item, follow_items)
            if row_index is None:
                row_index = self._fallback_follow_up_row_for_rag(rag_item, follow_items)
            if row_index is None or row_index in used_follow_rows:
                continue
            clause = self._rag_customer_clause(rag_item, max_len=105, purpose="follow_up")
            updated = self._append_clause_to_report_section_item(result, "复查与跟进建议", clause, item_index=row_index)
            if updated != result:
                used_follow_rows.add(row_index)
                result = updated

        return result

    def _try_llm_rag_fusion(self, report_text: str, draft, case) -> str | None:
        if not self.rag_fusion_provider or not self._has_rag_sections(draft):
            return None

        target_sections = self._llm_fusion_target_sections(report_text)
        if not all(target_sections.values()):
            self._append_rag_audit(draft, "rag_fusion:remote_skipped:missing_target_section")
            return None

        try:
            fusion = self.rag_fusion_provider.fuse_report_sections(
                report_text=report_text,
                target_sections=target_sections,
                rag_context=self._llm_fusion_rag_context(draft),
                case_context=self._llm_fusion_case_context(case, draft),
            )
            patch_payload = getattr(fusion, "section_patches", {}) or {}
            valid_sections = self._validate_llm_fused_section_patches(patch_payload, target_sections, draft)
            if not valid_sections:
                section_payload = getattr(fusion, "sections", {}) or {}
                valid_sections = self._validate_llm_fused_sections(section_payload, target_sections, draft)
            if not valid_sections:
                self._append_rag_audit(draft, "rag_fusion:remote_rejected:no_valid_section")
                return None

            result = report_text
            for title, items in valid_sections.items():
                result = self._replace_report_section_items(result, title, items)

            if self._report_has_hidden_rag_leak(result) or self._looks_like_corrupted_publishable_report(result):
                self._append_rag_audit(draft, "rag_fusion:remote_rejected:report_validation")
                return None

            self._append_rag_audit(draft, "rag_fusion:remote_success")
            used_refs = getattr(fusion, "used_rag_refs", {}) or {}
            for title, refs in used_refs.items():
                if title not in target_sections or not isinstance(refs, list):
                    continue
                safe_refs = [str(ref).strip() for ref in refs if str(ref).strip()][:6]
                if safe_refs:
                    self._append_rag_audit(draft, f"rag_fusion_used:{title}:{','.join(safe_refs)}")
            return result
        except Exception as exc:
            self._append_rag_audit(draft, self._remote_fusion_failure_audit(exc))
            return None

    def _remote_fusion_failure_audit(self, exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        error_code = getattr(exc, "error_code", None)
        if status_code:
            safe_error = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(error_code or "HTTPError"))[:80]
            return f"rag_fusion:remote_unavailable:{status_code}:{safe_error}"
        if exc.__class__.__name__ in {"ConnectTimeout", "PoolTimeout", "ReadTimeout", "TimeoutException"}:
            return f"rag_fusion:remote_timeout:{exc.__class__.__name__}"
        return f"rag_fusion:remote_failed:{exc.__class__.__name__}"

    def _llm_fusion_target_sections(self, report_text: str) -> dict[str, list[str]]:
        return {
            title: self._extract_report_section_items(report_text, title)
            for title in ("总体健康画像", "关键指标", "生活方式干预重点", "复查与跟进建议")
        }

    def _llm_fusion_rag_context(self, draft) -> dict[str, list[dict[str, str]]]:
        sections = draft.report_sections or {}
        source_map = {
            "总体健康画像": ("health", "RAG总体健康画像"),
            "关键指标": ("indicator", "RAG异常指标解释"),
            "生活方式干预重点": ("lifestyle", "RAG生活方式干预"),
            "复查与跟进建议": ("followup", "RAG复查建议"),
        }
        context: dict[str, list[dict[str, str]]] = {}
        for title, (prefix, source_key) in source_map.items():
            items = []
            for index, item in enumerate(self._customerize_items(sections.get(source_key, []))[:6], start=1):
                text = self._strip_customer_rag_prefix(item)
                if text and not self._llm_text_quality_reason(text):
                    items.append({"id": f"{prefix}_{index}", "text": text[:420]})
            context[title] = items
        return context

    def _llm_fusion_case_context(self, case, draft) -> dict[str, Any]:
        questionnaire = case.questionnaire
        return {
            "case_id": case.id,
            "draft_id": draft.id,
            "symptoms": list(getattr(questionnaire, "symptoms", []) or [])[:8] if questionnaire else [],
            "goals": list(getattr(questionnaire, "goals", []) or [])[:8] if questionnaire else [],
            "chief_concerns": list(getattr(questionnaire, "chief_concerns", []) or [])[:8] if questionnaire else [],
            "missing_info": list(getattr(draft, "missing_info", []) or [])[:8],
            "red_flags": list(getattr(draft, "red_flags", []) or [])[:8],
        }

    def _validate_llm_fused_section_patches(
        self,
        patch_payload: dict[str, list[dict[str, Any]]],
        original_sections: dict[str, list[str]],
        draft,
    ) -> dict[str, list[str]]:
        if not isinstance(patch_payload, dict):
            return {}

        valid: dict[str, list[str]] = {}
        product_names = [
            getattr(sku, "display_name", "")
            for sku in getattr(draft, "recommended_skus", []) or []
            if getattr(sku, "display_name", "")
        ]
        accepted_patch_count = 0
        for title, original_items in original_sections.items():
            patches = patch_payload.get(title)
            if not isinstance(patches, list) or not patches:
                continue
            if len(patches) > 8:
                continue

            patched_items = list(original_items)
            seen_indices: set[int] = set()
            section_valid = True
            for patch in patches:
                if not isinstance(patch, dict):
                    section_valid = False
                    break
                raw_index = patch.get("index")
                if not isinstance(raw_index, int) or raw_index < 0 or raw_index >= len(original_items):
                    section_valid = False
                    break
                if raw_index in seen_indices:
                    section_valid = False
                    break
                item = self._normalize_llm_report_item_format(patch.get("text") or "")
                if not item or len(item) > 900:
                    section_valid = False
                    break
                if self._llm_text_quality_reason(item) or self._section_item_has_forbidden_llm_content(item, product_names):
                    section_valid = False
                    break
                if title == "关键指标":
                    original_name = original_items[raw_index].split("：", 1)[0].strip()
                    if original_name and original_name not in item:
                        section_valid = False
                        break
                seen_indices.add(raw_index)
                patched_items[raw_index] = item

            accepted_patch_count += len(seen_indices)
            if accepted_patch_count > 8:
                return {}
            if section_valid and seen_indices:
                valid[title] = patched_items
        return valid

    def _validate_llm_fused_sections(
        self,
        section_payload: dict[str, list[str]],
        original_sections: dict[str, list[str]],
        draft,
    ) -> dict[str, list[str]]:
        if not isinstance(section_payload, dict):
            return {}

        valid: dict[str, list[str]] = {}
        product_names = [
            getattr(sku, "display_name", "")
            for sku in getattr(draft, "recommended_skus", []) or []
            if getattr(sku, "display_name", "")
        ]
        for title, original_items in original_sections.items():
            values = section_payload.get(title)
            if not isinstance(values, list) or len(values) != len(original_items):
                continue

            cleaned_items = []
            section_valid = True
            for index, raw_item in enumerate(values):
                raw_text = str(raw_item).strip()
                if self._section_item_has_forbidden_llm_content(raw_text, product_names):
                    section_valid = False
                    break
                item = self._normalize_llm_report_item_format(raw_text)
                if not item or len(item) > 900:
                    section_valid = False
                    break
                if self._llm_text_quality_reason(item) or self._section_item_has_forbidden_llm_content(item, product_names):
                    section_valid = False
                    break
                if title == "关键指标":
                    original_name = original_items[index].split("：", 1)[0].strip()
                    if original_name and original_name not in item:
                        section_valid = False
                        break
                cleaned_items.append(item)

            if section_valid and cleaned_items:
                valid[title] = cleaned_items
        return valid

    def _normalize_llm_report_item_format(self, raw_item: Any) -> str:
        item = str(raw_item or "")
        item = self._remove_customer_hidden_rag_labels(item)
        item = item.replace("\r\n", "\n").replace("\r", "\n")
        item = re.sub(r"\n\s*(?:[-*•·]+|\d+[.)、]|[（(]?\d+[）)])\s*", " ", item)
        item = self._collapse_inline_soft_breaks(item)
        item = re.sub(r"^\s*(?:[-*•·]+|\d+[.)、]|[（(]?\d+[）)])\s*", "", item)
        item = re.sub(r"^\s*#+\s*", "", item)
        item = item.strip(" ，；")
        item = item.replace("(", "（").replace(")", "）")
        item = re.sub(r"\s+（", "（", item)
        item = re.sub(r"(?<=[\u4e00-\u9fffA-Za-z0-9])[:：]\s*", "：", item)
        item = re.sub(r"\s*([，、。；：！？])\s*", r"\1", item)
        item = re.sub(r"）\.\s*", "）。", item)
        item = re.sub(r"(?<=[\u4e00-\u9fff])[,，]\s*", "，", item)
        item = re.sub(r"(?<=[\u4e00-\u9fff]);\s*", "；", item)
        item = re.sub(r"(?<=[\u4e00-\u9fff])\?\s*", "？", item)
        item = re.sub(r"(?<=[\u4e00-\u9fff])!\s*", "！", item)
        item = re.sub(r"(?<=[\u4e00-\u9fff])\.\s*", "。", item)
        item = re.sub(r"([，。；：！？、])\1+", r"\1", item)
        item = re.sub(r"([。！？])([。！？])+", r"\1", item)
        item = item.strip(" ，；")
        if item and not re.search(r"[。！？；）)]$", item):
            item += "。"
        return item

    def _replace_report_section_items(self, report_text: str, title: str, items: list[str]) -> str:
        lines = report_text.splitlines()
        indices = self._report_section_item_line_indices(lines, title)
        if len(indices) != len(items):
            return report_text
        for line_index, item in zip(indices, items):
            lines[line_index] = f"- {item}"
        return "\n".join(lines).strip()

    def _section_item_has_forbidden_llm_content(self, item: str, product_names: list[str]) -> bool:
        forbidden_markers = (
            "RAG",
            "功能医学知识库",
            "仅供参考",
            "chunk",
            "docx",
            ".pdf",
            ".docx",
            "页码",
            "教材来源",
            "文件路径",
            "rag_query_failed",
            "DenseRetrievalUnavailable",
            "huggingface.co",
        )
        if any(marker.lower() in item.lower() for marker in forbidden_markers):
            return True
        if any(product_name and product_name in item for product_name in product_names):
            return True
        if re.search(r"(建议|推荐|需要|可考虑).{0,12}(服用|口服|加用|使用).{0,16}(药|抗生素|激素|处方)", item):
            return True
        return False

    def _llm_text_quality_reason(self, text: str) -> str | None:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return "empty_text"
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
        latin_count = len(re.findall(r"[A-Za-z]", compact))
        stripped = text.strip()
        if re.search(
            r"\b(potassium|chloride|bilirubin|alkaline|phosphatase|prostate specific antigen)\b",
            text,
            re.IGNORECASE,
        ) and cjk_count < 20:
            return "english_lab_list_fragment"
        if re.match(r"^[a-z]{3,}\b", stripped) and cjk_count < 20:
            return "english_continuation_fragment"
        if latin_count >= 30 and cjk_count < 8:
            return "non_chinese_fragment"
        if latin_count > max(cjk_count * 4, 120) and cjk_count < 30:
            return "non_chinese_fragment"
        return None

    def _report_has_hidden_rag_leak(self, report_text: str) -> bool:
        return self._section_item_has_forbidden_llm_content(report_text, [])

    def _append_rag_audit(self, draft, message: str) -> None:
        if not message:
            return
        sections = draft.report_sections or {}
        audit = list(sections.get("RAG内部审查", []) or [])
        if message not in audit:
            audit.append(message[:180])
        sections["RAG内部审查"] = audit
        draft.report_sections = sections

    def _extract_report_section_items(self, report_text: str, title: str) -> list[str]:
        lines = report_text.splitlines()
        indices = self._report_section_item_line_indices(lines, title)
        return [lines[index].lstrip()[2:].strip() for index in indices]

    def _append_clause_to_report_section_item(
        self,
        report_text: str,
        title: str,
        clause: str,
        *,
        item_index: int,
    ) -> str:
        clause = (clause or "").strip()
        if not clause:
            return report_text
        if self._normalize_text(clause) in self._normalize_text(report_text):
            return report_text

        lines = report_text.splitlines()
        indices = self._report_section_item_line_indices(lines, title)
        if item_index < 0 or item_index >= len(indices):
            return report_text

        line_index = indices[item_index]
        base = lines[line_index].rstrip()
        separator = "" if base.endswith(("。", "！", "？", "；")) else "。"
        lines[line_index] = f"{base}{separator}{clause}"
        return "\n".join(lines).strip()

    def _report_section_item_line_indices(self, lines: list[str], title: str) -> list[int]:
        header = f"## {title}"
        start_index = next((index for index, line in enumerate(lines) if line.strip() == header), None)
        if start_index is None:
            return []
        end_index = len(lines)
        for index in range(start_index + 1, len(lines)):
            if lines[index].startswith("## "):
                end_index = index
                break
        return [
            index
            for index in range(start_index + 1, end_index)
            if lines[index].lstrip().startswith("- ")
        ]

    def _fallback_indicator_row_for_rag(self, rag_item: str, abnormal_indicators: list) -> int | None:
        if not abnormal_indicators:
            return None
        normalized = self._normalize_text(rag_item)
        if any(term in normalized for term in ("甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab", "hpt", "桥本")):
            for index, indicator in enumerate(abnormal_indicators):
                name = self._normalize_text(getattr(indicator, "indicator_name", ""))
                if any(term in name for term in ("甲状腺", "促甲状腺", "tsh", "ft3", "ft4", "抗体")):
                    return index
        if any(term in normalized for term in ("血糖", "胰岛素", "hba1c", "代谢")):
            for index, indicator in enumerate(abnormal_indicators):
                name = self._normalize_text(getattr(indicator, "indicator_name", ""))
                if any(term in name for term in ("血糖", "葡萄糖", "糖化", "胰岛素")):
                    return index
        if any(term in normalized for term in ("炎症", "crp", "免疫")):
            for index, indicator in enumerate(abnormal_indicators):
                name = self._normalize_text(getattr(indicator, "indicator_name", ""))
                if any(term in name for term in ("crp", "反应蛋白", "白细胞", "炎症")):
                    return index
        return 0

    def _fallback_follow_up_row_for_rag(self, rag_item: str, follow_items: list[str]) -> int | None:
        if not follow_items:
            return None
        normalized = self._normalize_text(rag_item)
        if any(term in normalized for term in ("甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab", "桥本")):
            return self._first_matching_row(follow_items, ("甲状腺", "tsh", "ft3", "ft4", "抗体")) or 0
        if any(term in normalized for term in ("血糖", "胰岛素", "hba1c", "代谢")):
            return self._first_matching_row(follow_items, ("血糖", "胰岛素", "hba1c", "复查")) or 0
        if any(term in normalized for term in ("睡眠", "压力", "hpa", "皮质醇")):
            return self._first_matching_row(follow_items, ("睡眠", "压力", "回访")) or 0
        return 0

    def _first_matching_row(self, items: list[str], terms: tuple[str, ...]) -> int | None:
        normalized_terms = [self._normalize_text(term) for term in terms]
        for index, item in enumerate(items):
            normalized_item = self._normalize_text(item)
            if any(term and term in normalized_item for term in normalized_terms):
                return index
        return None

    def _render_report(self, draft, case) -> str:
        lines = ["# 功能医学营养与生活方式建议", ""]
        sections = draft.report_sections or {}
        abnormal_indicators = self._abnormal_indicators(case)
        nutrition_plan = self._nutrition_plan_with_safety(
            draft,
            sections.get("个性化营养素方案") or sections.get("营养素推荐"),
        )
        nutrition_plan = list(dict.fromkeys(nutrition_plan))
        follow_up = self._customer_follow_up(sections)
        missing_info = self._customerize_items(sections.get("待确认项", draft.missing_info))
        health_portrait = self._fuse_rag_into_health_portrait(
            self._customer_health_portrait(case, abnormal_indicators),
            self._customerize_items(sections.get("RAG总体健康画像", [])),
        )
        key_indicators = self._fuse_rag_into_key_indicators(
            self._customer_key_indicators(abnormal_indicators),
            abnormal_indicators,
            self._customerize_items(sections.get("RAG异常指标解释", [])),
        )

        ordered_sections = [
            ("总体健康画像", health_portrait),
            ("关键指标", key_indicators),
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

        report = "\n".join(lines)
        return self._try_llm_rag_fusion(report, draft, case) or report

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

    def _fuse_rag_into_health_portrait(self, portrait_items: list[str], rag_items: list[str]) -> list[str]:
        items = list(portrait_items)
        if not items:
            return items
        for rag_item in rag_items[:4]:
            clause = self._rag_customer_clause(rag_item, max_len=140, purpose="health")
            if clause:
                items[0] = f"{items[0].rstrip('。')}。{clause}"
                break
        return list(dict.fromkeys(items))

    def _fuse_rag_into_key_indicators(
        self,
        indicator_items: list[str],
        abnormal_indicators: list,
        rag_items: list[str],
    ) -> list[str]:
        items = list(indicator_items)
        if not items or not rag_items:
            return items

        used_rows: set[int] = set()
        for rag_item in rag_items[:6]:
            best_index = self._best_indicator_rag_match(rag_item, abnormal_indicators)
            if best_index is None:
                best_index = self._fallback_indicator_row_for_rag(rag_item, abnormal_indicators)
            if best_index is None or best_index in used_rows or best_index >= len(items):
                continue
            clause = self._rag_customer_clause(rag_item, max_len=120, purpose="indicator")
            if clause:
                items[best_index] = f"{items[best_index].rstrip('。')}。{clause}"
                used_rows.add(best_index)
        return list(dict.fromkeys(items))

    def _best_indicator_rag_match(self, rag_item: str, abnormal_indicators: list) -> int | None:
        normalized_rag = self._normalize_text(rag_item)
        best_index: int | None = None
        best_score = 0
        for index, indicator in enumerate(abnormal_indicators):
            name = self._normalize_text(getattr(indicator, "indicator_name", ""))
            aliases = self._indicator_aliases(name)
            score = sum(1 for alias in aliases if len(alias) >= 3 and alias in normalized_rag)
            if score > best_score:
                best_index = index
                best_score = score
        return best_index if best_score > 0 else None

    def _indicator_aliases(self, normalized_name: str) -> set[str]:
        aliases = {normalized_name}
        mapping = {
            "甲状腺过氧化物酶抗体": {"甲状腺过氧化物酶抗体", "tpoab", "tpo", "桥本"},
            "甲状腺球蛋白抗体": {"甲状腺球蛋白抗体", "tgab", "桥本"},
            "促甲状腺激素": {"促甲状腺激素", "甲状腺", "tsh", "hpt", "桥本"},
            "空腹血糖": {"空腹血糖", "血糖", "glucose", "胰岛素", "hba1c", "糖化"},
            "超敏c反应蛋白": {"超敏c反应蛋白", "hscrp", "crp", "炎症"},
            "25羟维生素d": {"25羟维生素d", "维生素d", "25ohd", "免疫"},
            "甘油三酯": {"甘油三酯", "血脂", "代谢"},
        }
        for key, values in mapping.items():
            if key in normalized_name:
                aliases.update(self._normalize_text(value) for value in values)
        if "甲状腺" in normalized_name or normalized_name in {"tsh", "ft3", "ft4"}:
            aliases.update(self._normalize_text(value) for value in {"甲状腺", "tsh", "ft3", "ft4", "tpoab", "tgab", "hpt", "桥本"})
        return {alias for alias in aliases if alias}

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
            safety_suffix = f"；注意/禁忌：{safety_note}" if safety_note else ""
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
                    return f"{item.rstrip(' 。；')}；注意/禁忌：{safety_note}"
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
        rag_items = self._customerize_items(sections.get("RAG生活方式干预", []))
        combined = list(dict.fromkeys(protocol_items[:10] + draft_items + protocol_items[10:]))
        combined = self._fuse_rag_into_lifestyle(combined, rag_items)
        return combined[:14]

    def _fuse_rag_into_lifestyle(self, lifestyle_items: list[str], rag_items: list[str]) -> list[str]:
        items = list(lifestyle_items)
        used_rows: set[int] = set()
        for rag_item in rag_items[:5]:
            if not self._is_lifestyle_rag_item(rag_item):
                continue
            row_index = self._best_lifestyle_row(rag_item, items)
            if row_index is None and items:
                row_index = 0
            if row_index is None or row_index in used_rows:
                continue
            clause = self._rag_customer_clause(rag_item, max_len=115, purpose="lifestyle")
            if clause:
                items[row_index] = f"{items[row_index].rstrip('。')}。{clause}"
                used_rows.add(row_index)
        return list(dict.fromkeys(items))

    def _is_lifestyle_rag_item(self, rag_item: str) -> bool:
        normalized = self._normalize_text(rag_item)
        lifestyle_terms = (
            "睡眠",
            "压力",
            "运动",
            "活动",
            "久坐",
            "饮食",
            "作息",
            "咖啡因",
            "光照",
            "呼吸",
            "散步",
        )
        return any(term in normalized for term in lifestyle_terms)

    def _best_lifestyle_row(self, rag_item: str, lifestyle_items: list[str]) -> int | None:
        normalized = self._normalize_text(rag_item)
        row_preferences = []
        if any(term in normalized for term in ("睡眠", "咖啡因", "光照", "作息")):
            row_preferences.extend(("睡眠", "作息"))
        if any(term in normalized for term in ("压力", "呼吸", "冥想", "迷走")):
            row_preferences.extend(("压力", "放松"))
        if any(term in normalized for term in ("运动", "活动", "久坐", "散步", "抗阻")):
            row_preferences.extend(("运动", "活动", "血糖"))
        if any(term in normalized for term in ("饮食", "餐", "碳水", "蛋白", "蔬菜")):
            row_preferences.extend(("饮食", "餐盘", "血糖"))

        for preferred in row_preferences:
            preferred_normalized = self._normalize_text(preferred)
            for index, item in enumerate(lifestyle_items):
                if preferred_normalized in self._normalize_text(item):
                    return index
        return None

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
        rag_items = self._customerize_items(sections.get("RAG复查建议", []))
        items = test_items[:4] + follow_items[:3]
        for item in test_items[4:] + follow_items[3:]:
            if len(items) >= 8:
                break
            items.append(item)
        items = self._fuse_rag_into_follow_up(items, rag_items)
        items = list(dict.fromkeys(items))[:8]
        if not items:
            items = [
                "建议2周内回访一次，重点看睡眠、精力、胃肠反应和方案执行难点。",
                "建议8-12周后结合本次异常指标做复查，用趋势来判断方案是否需要调整。",
            ]
        return items

    def _fuse_rag_into_follow_up(self, follow_items: list[str], rag_items: list[str]) -> list[str]:
        items = list(follow_items)
        used_rows: set[int] = set()
        for rag_item in rag_items[:5]:
            row_index = self._best_follow_up_row(rag_item, items)
            if row_index is None:
                row_index = self._fallback_follow_up_row_for_rag(rag_item, items)
            if row_index is None or row_index in used_rows:
                continue
            clause = self._rag_customer_clause(rag_item, max_len=105, purpose="follow_up")
            if clause:
                items[row_index] = f"{items[row_index].rstrip('。')}。{clause}"
                used_rows.add(row_index)
        return items

    def _best_follow_up_row(self, rag_item: str, follow_items: list[str]) -> int | None:
        normalized = self._normalize_text(rag_item)
        row_preferences = []
        if any(term in normalized for term in ("甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab", "桥本")):
            row_preferences.extend(("甲状腺", "tsh"))
        if any(term in normalized for term in ("维生素d", "25ohd", "免疫")):
            row_preferences.extend(("25", "维生素d"))
        if any(term in normalized for term in ("血糖", "胰岛素", "hba1c", "代谢")):
            row_preferences.extend(("血糖", "胰岛素", "hba1c"))
        if any(term in normalized for term in ("压力", "皮质醇", "睡眠", "hpa")):
            row_preferences.extend(("压力", "睡眠", "皮质醇"))

        for preferred in row_preferences:
            preferred_normalized = self._normalize_text(preferred)
            for index, item in enumerate(follow_items):
                if preferred_normalized in self._normalize_text(item):
                    return index
        return None

    def _customer_notice(self) -> list[str]:
        return [
            "本报告用于健康管理和营养生活方式指导，不能替代医学诊断或治疗。",
            "如果出现胸痛、持续高热、黑便/便血、明显水肿、严重头晕或其他急性不适，请及时就医。",
        ]

    def _rag_customer_clause(self, rag_item: str, *, max_len: int, purpose: str) -> str:
        cleaned = self._strip_customer_rag_prefix(rag_item)
        cleaned = re.sub(r"；这部分仅作为营养支持背景说明.*$", "", cleaned)
        cleaned = re.sub(r"具体补充剂、禁忌和复查安排仍以医生审核与产品规则为准.*$", "", cleaned)
        cleaned = self._collapse_inline_soft_breaks(cleaned).strip(" ，。；")
        if not cleaned:
            return ""
        naturalized = self._naturalize_rag_clause(cleaned, purpose=purpose)
        if naturalized:
            return naturalized
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len].rstrip(" ，。；")
        return cleaned.rstrip("。") + "。"

    def _naturalize_rag_clause(self, cleaned: str, *, purpose: str) -> str:
        cleaned = cleaned.strip(" ，。；")
        if not cleaned:
            return ""
        if purpose == "health":
            if any(term in cleaned for term in ("甲状腺激素合成", "临床甲减", "甲状腺功能减退", "低甲状腺激素", "HPT", "HPT 轴")):
                return (
                    "这也提示后续需要把甲状腺功能、抗体变化、症状表现、微量营养状态和整体代谢恢复放在同一张图里观察。"
                )
            if "维生素D" in cleaned or "维生素 D" in cleaned:
                return "这也提示免疫调节、甲状腺状态和整体恢复能力需要一起跟踪。"
            return ""
        if purpose == "indicator":
            if "TPOAb" in cleaned or "甲状腺过氧化物酶抗体" in cleaned:
                return "从功能医学思路看，该抗体更适合结合肠道通透性、麸质反应、碘摄入、硒状态和甲状腺功能趋势一起解释。"
            if "TgAb" in cleaned or "甲状腺球蛋白抗体" in cleaned:
                return "解读时可与TPOAb、TSH、FT3、FT4和症状变化一起判断自身免疫活跃度。"
            if any(term in cleaned for term in ("HPT", "HPT 轴", "甲状腺", "甲减", "甲状腺功能减退")):
                return "从功能医学思路看，甲状腺相关异常不宜只看单项数值，建议结合HPT轴相关症状、抗体变化和甲状腺功能趋势一起评估。"
            if "胰岛素" in cleaned or "血糖" in cleaned:
                return "解读时可同时关注胰岛素抵抗、餐后波动、睡眠和炎症负担。"
            return ""
        if purpose == "lifestyle":
            if ("睡眠" in cleaned or "压力" in cleaned) and ("久坐" in cleaned or "运动" in cleaned or "活动" in cleaned):
                return "因此，睡眠节律和压力恢复应与减少久坐一起纳入生活方式干预，帮助改善炎症负担和代谢恢复。"
            if "久坐" in cleaned or "运动" in cleaned:
                return "因此，减少久坐、增加可持续的低到中等强度活动，可作为改善炎症负担和代谢恢复的基础策略。"
            if "睡眠" in cleaned or "压力" in cleaned:
                return "因此，睡眠节律和压力恢复应作为生活方式干预的核心观察点。"
            return ""
        if purpose == "follow_up":
            if cleaned.startswith("复查时"):
                return cleaned.rstrip("。") + "。"
            return ""
        return cleaned.rstrip("。") + "。"

    def _strip_customer_rag_prefix(self, text: str) -> str:
        cleaned = str(text).strip()
        for prefix in (CUSTOMER_RAG_PREFIX,):
            if cleaned.startswith(prefix):
                return cleaned[len(prefix) :].strip()
        return cleaned

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[\s，。；、：:（）()“”\"'`+\\/.-]+", "", str(text).lower())

    def _customerize_items(self, content) -> list[str]:
        items = []
        for item in self._as_list(content):
            cleaned = re.sub(r"product:sku_[a-z0-9_]+", "", item, flags=re.IGNORECASE)
            cleaned = re.sub(r"statement_[a-z0-9_]+", "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.replace("当前草案", "当前方案")
            cleaned = cleaned.replace("候选推荐", "建议")
            cleaned = cleaned.replace("已审核知识命中", "本次资料提示")
            cleaned = cleaned.replace("人工复核", "顾问确认")
            cleaned = self._collapse_inline_soft_breaks(cleaned).strip(" ，。；")
            if cleaned:
                items.append(cleaned)
        return list(dict.fromkeys(items))

    def _contains_any(self, text: str, tokens: tuple[str, ...]) -> bool:
        normalized = text.lower()
        return any(token.lower() in normalized for token in tokens)
