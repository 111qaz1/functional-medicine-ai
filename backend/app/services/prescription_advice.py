from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.llm_compat import chat_generation_options
from app.core.settings import AppSettings


AdviceSource = Literal["llm", "local_fallback"]
MIN_ADVICE_CHARS = 50
MAX_ADVICE_CHARS = 100

_FORBIDDEN_TERMS = (
    "关联度",
    "命中产品标签",
    "产品标签命中",
    "RAG内部审查",
    "RAG",
    "后台判断",
    "内部知识证据",
    "命中证据",
    "证据ID",
    "evidence",
    "product:",
    "statement_",
    "?????",
    "API Key",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "治疗",
    "治愈",
    "疗效",
)


@dataclass(frozen=True)
class PrescriptionAdviceResult:
    medical_advice: str
    advice_source: AdviceSource


class PrescriptionAdviceService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def build_advice(self, draft: Any) -> PrescriptionAdviceResult:
        fallback = self._local_fallback(draft)
        remote = self._remote_polish(draft)
        if remote:
            return PrescriptionAdviceResult(medical_advice=remote, advice_source="llm")
        return PrescriptionAdviceResult(medical_advice=fallback, advice_source="local_fallback")

    def _local_fallback(self, draft: Any) -> str:
        directions = self._system_support_directions(draft)
        direction_text = "、".join(directions[:3]) if directions else "代谢调节、免疫平衡"
        text = (
            f"处方级营养素用于补充身体当下所需营养，予以{direction_text}等方向的营养支持，"
            "帮助平衡免疫、抗炎、抗氧化及代谢调节。"
        )
        return self._normalize_local_advice(text)

    def _remote_polish(self, draft: Any) -> str | None:
        if not getattr(draft, "recommended_skus", None):
            return None
        if not (self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model):
            return None

        payload = self._draft_payload(draft)
        try:
            raw = self._call_remote_model(payload)
            parsed = self._parse_remote_json(raw)
        except Exception:
            return None
        return self._valid_remote_advice(parsed)

    def _draft_payload(self, draft: Any) -> dict[str, Any]:
        sections = getattr(draft, "report_sections", {}) or {}
        return {
            "key_lab_highlights": self._sanitize_list(getattr(draft, "key_lab_highlights", [])[:8]),
            "system_analysis": self._sanitize_list(
                self._as_list(sections.get("功能医学系统失衡分析") or sections.get("系统功能深度分析"))[:8]
            ),
            "recommendations": [
                {
                    "name": self._sanitize_text(getattr(item, "display_name", "")),
                    "reason": self._sanitize_reason(getattr(item, "reason", "")),
                    "warnings": self._sanitize_list(getattr(item, "warnings", [])[:3]),
                }
                for item in getattr(draft, "recommended_skus", [])
            ],
        }

    def _call_remote_model(self, payload: dict[str, Any]) -> str:
        base_url = self.settings.llm_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}", "Content-Type": "application/json"}
        timeout = min(max(float(self.settings.llm_timeout_seconds), 3.0), 20.0)
        with httpx.Client(timeout=timeout) as client:
            if self.settings.llm_api_style in {"auto", "responses"}:
                try:
                    response = client.post(
                        f"{base_url}/responses",
                        headers=headers,
                        json=self._responses_payload(payload),
                    )
                    response.raise_for_status()
                    return self._extract_response_text(response.json())
                except httpx.HTTPError:
                    if self.settings.llm_api_style == "responses":
                        raise

            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=self._chat_payload(payload),
            )
            response.raise_for_status()
            return self._extract_response_text(response.json())

    def _system_prompt(self) -> str:
        return (
            "你是功能医学营养方案的总医嘱说明润色器。只能基于输入的异常指标、系统失衡分析、"
            "营养素名称和推荐理由，生成一段面向客户的总医嘱说明。"
            "请学习这种处方医嘱句式：处方级营养素用于补充身体当下所需营养，予以相关系统支持、平衡免疫、抗炎、抗氧化及代谢调节。"
            "重点说明推荐这些营养素的依据，例如肝脏解毒代谢、血糖代谢、心血管代谢、肠道消化、睡眠压力、甲状腺代谢、免疫调节等系统负担。"
            "可使用“对症支持、营养支持、营养干预”，不得使用“治疗、治愈、疗效”，不得新增营养素，不得改变剂量，不得输出内部证据、RAG、关联度、产品标签命中或后台判断。"
            "medical_advice 使用简体中文，50-100字，只返回 JSON，格式为 {\"medical_advice\":\"...\"}。"
        )

    def _chat_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.settings.llm_model,
            **chat_generation_options(
                model=self.settings.llm_model,
                temperature=min(self.settings.llm_temperature, 0.2),
                thinking_type="disabled",
            ),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }

    def _responses_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.settings.llm_model,
            "temperature": min(self.settings.llm_temperature, 0.2),
            "instructions": self._system_prompt(),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}],
                }
            ],
        }

    def _parse_remote_json(self, raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = json.loads(self._extract_first_json_object(raw))
        return payload if isinstance(payload, dict) else {}

    def _valid_remote_advice(self, payload: dict[str, Any]) -> str | None:
        raw_advice = payload.get("medical_advice")
        if not isinstance(raw_advice, str):
            return None
        advice = self._sanitize_advice(raw_advice)
        if not self._is_valid_advice(advice):
            return None
        return advice

    def _system_support_directions(self, draft: Any) -> list[str]:
        sections = getattr(draft, "report_sections", {}) or {}
        sources = []
        sources.extend(self._as_list(sections.get("功能医学系统失衡分析") or sections.get("系统功能深度分析"))[:3])
        sources.extend(self._as_list(getattr(draft, "key_lab_highlights", []))[:3])
        sources.extend(self._recommendation_reasons(draft)[:4])
        source_text = self._sanitize_reason("；".join(sources))

        focus_terms = [
            ("肝脏解毒代谢支持", ("肝", "胆", "解毒", "谷胱甘肽", "尿酸", "ALT", "AST", "GGT")),
            ("血糖代谢支持", ("血糖", "胰岛素", "糖化", "代谢", "体重")),
            ("心血管代谢支持", ("血脂", "胆固醇", "甘油三酯", "同型半胱氨酸", "HCY")),
            ("肠道消化支持", ("肠道", "消化", "菌群", "胃", "腹胀")),
            ("睡眠压力调节支持", ("睡眠", "压力", "疲劳", "焦虑", "镁")),
            ("甲状腺代谢支持", ("甲状腺", "TSH", "FT3", "FT4", "桥本")),
            ("免疫调节支持", ("免疫", "维生素D", "炎症", "过敏", "CRP", "白细胞")),
        ]

        matched = []
        for label, terms in focus_terms:
            if any(term.lower() in source_text.lower() for term in terms):
                matched.append(label)
        return list(dict.fromkeys(matched))

    def _recommendation_reasons(self, draft: Any) -> list[str]:
        reasons = []
        for item in getattr(draft, "recommended_skus", []):
            reason = self._sanitize_reason(getattr(item, "reason", ""))
            if reason:
                reasons.append(reason)
        return reasons

    def _normalize_local_advice(self, value: str) -> str:
        text = self._sanitize_advice(value)
        if len(text) < MIN_ADVICE_CHARS:
            text = f"{text.rstrip('。')}，建议结合耐受反馈和复查趋势，由医生确认后续调整。"
        if len(text) > MAX_ADVICE_CHARS:
            text = text[: MAX_ADVICE_CHARS - 1].rstrip("，。；; ") + "。"
        if len(text) < MIN_ADVICE_CHARS:
            text = f"{text.rstrip('。')}，首月先稳妥执行并跟踪身体反馈。"
        return text

    def _sanitize_advice(self, value: str) -> str:
        text = self._sanitize_reason(value)
        text = re.sub(r"^[\-*•\d.、\s]+", "", text)
        if not text:
            return ""
        if not text.endswith(("。", "！", "；")):
            text += "。"
        return text

    def _sanitize_reason(self, value: str) -> str:
        text = self._sanitize_text(value)
        text = re.sub(r"product:[\w\-:/.]+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"statement_[\w\-]+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[A-Za-z]:\\[^\s，。；;]+", "", text)
        text = re.sub(r"/(?:[\w.\-]+/){2,}[\w.\-]+", "", text)
        parts = re.split(r"[。；;\n]+", text)
        kept = [part.strip(" ，。；;") for part in parts if part.strip() and not self._contains_forbidden(part)]
        text = "；".join(kept) if kept else ""
        return self._sanitize_text(text)

    def _sanitize_text(self, value: str) -> str:
        text = re.sub(r"\s+", " ", (value or "").strip())
        return text.strip().strip("\"'")

    def _sanitize_list(self, values: list[Any]) -> list[str]:
        cleaned = []
        for value in values:
            text = self._sanitize_reason(str(value))
            if text and not self._contains_forbidden(text):
                cleaned.append(text[:160])
        return cleaned[:8]

    def _as_list(self, content: Any) -> list[str]:
        if not content:
            return []
        if isinstance(content, str):
            return [content.strip()] if content.strip() else []
        if isinstance(content, list):
            return [str(item).strip() for item in content if str(item).strip()]
        return [str(content).strip()]

    def _is_valid_advice(self, value: str) -> bool:
        return (
            bool(value)
            and MIN_ADVICE_CHARS <= len(value) <= MAX_ADVICE_CHARS
            and not self._contains_forbidden(value)
        )

    def _contains_forbidden(self, value: str) -> bool:
        normalized = value.lower()
        return any(term.lower() in normalized for term in _FORBIDDEN_TERMS)

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
            return payload["output_text"]

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}).get("content")
            if isinstance(message, str) and message.strip():
                return message
            if isinstance(message, list):
                chunks = []
                for part in message:
                    if isinstance(part, dict):
                        text = part.get("text") or part.get("content")
                        if isinstance(text, str):
                            chunks.append(text)
                joined = "".join(chunks).strip()
                if joined:
                    return joined

        output = payload.get("output")
        if isinstance(output, list):
            chunks = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            chunks.append(part["text"])
                elif isinstance(content, str):
                    chunks.append(content)
            joined = "".join(chunks).strip()
            if joined:
                return joined

        raise ValueError("Remote LLM returned empty content")

    def _extract_first_json_object(self, raw_response: str) -> str:
        start = raw_response.find("{")
        if start < 0:
            raise ValueError("Remote LLM did not return JSON content")

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw_response)):
            char = raw_response[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return raw_response[start : index + 1]
        raise ValueError("Remote LLM returned incomplete JSON content")
