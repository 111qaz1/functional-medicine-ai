from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class PdfReportItem:
    kind: str
    text: str


class PdfReportExporter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.font_name = "STSong-Light"
        self.brand = colors.HexColor("#14564a")
        self.risk = colors.HexColor("#b04a34")
        self.notice = colors.HexColor("#b07a1f")
        self.evidence = colors.HexColor("#4f6478")
        self.text = colors.HexColor("#243129")
        self.text_muted = colors.HexColor("#647067")
        app_root = Path(__file__).resolve().parents[1]
        self.product_report_catalog = self._load_product_report_catalog(app_root / "data" / "product_report_catalog.json")
        self.logo_path = app_root / "assets" / "balasaier.jpg"
        self.nutrition_sections = {"营养素推荐", "个性化营养素方案", "首月营养素干预方案"}

        try:
            pdfmetrics.getFont(self.font_name)
        except KeyError:
            pdfmetrics.registerFont(UnicodeCIDFont(self.font_name))

    def export(
        self,
        *,
        draft_id: str,
        customer_name: str,
        report_text: str,
        recommended_skus: list[Any] | None = None,
    ) -> Path:
        safe_name = self._sanitize_filename(customer_name)
        # The downloaded report should be recognizable by the case name. A case
        # can be regenerated repeatedly, so the newest approved report replaces
        # the previous file with the same customer-facing name.
        target = self.root / f"{safe_name}.pdf"
        self._build_pdf(
            target,
            customer_name=customer_name,
            draft_id=draft_id,
            report_text=report_text,
            recommended_skus=recommended_skus or [],
        )
        return target

    def _build_pdf(
        self,
        target: Path,
        *,
        customer_name: str,
        draft_id: str,
        report_text: str,
        recommended_skus: list[Any],
    ) -> None:
        title, sections = self._parse_report(report_text)
        document = SimpleDocTemplate(
            str(target),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=30 * mm,
            bottomMargin=16 * mm,
            title=title,
            author="Functional Medicine Nutrition AI",
        )

        styles = self._styles()
        story = [
            Paragraph(escape(self._clean_customer_text(title)), styles["title"]),
            Spacer(1, 4),
            Paragraph(
                escape(self._clean_customer_text(f"客户：{customer_name} ｜ 报告编号：{draft_id} ｜ 类型：功能医学综合分析与首月干预方案")),
                styles["meta"],
            ),
            Spacer(1, 12),
        ]

        skip_section_indexes: set[int] = set()
        for index, (section_title, items) in enumerate(sections):
            if index in skip_section_indexes:
                continue
            story.append(Paragraph(escape(section_title), self._section_style(section_title, styles)))
            story.append(Spacer(1, 5))
            canonical_title = self._canonical_section_title(section_title)
            if canonical_title in self.nutrition_sections and recommended_skus:
                prescription_advice_items = self._subsection_items(items, "总医嘱说明")
                if (
                    not prescription_advice_items
                    and index + 1 < len(sections)
                    and self._canonical_section_title(sections[index + 1][0]) == "总医嘱说明"
                ):
                    prescription_advice_items = [
                        item.text for item in sections[index + 1][1] if item.kind != "subsection"
                    ]
                    skip_section_indexes.add(index + 1)
                story.extend(
                    self._build_nutrition_table_flowables(
                        recommended_skus,
                        styles,
                        prescription_advice_items=prescription_advice_items,
                    )
                )
            else:
                for item in items:
                    if item.kind == "subsection":
                        story.append(Paragraph(self._format_subheading_item(item.text), styles["subsection"]))
                        story.append(Spacer(1, 4))
                        continue
                    is_list = item.kind == "list"
                    story.append(
                        Paragraph(
                            self._format_item(canonical_title, item.text),
                            self._body_style(
                                canonical_title,
                                styles,
                                is_list=is_list,
                            ),
                            bulletText="•" if is_list else None,
                        )
                    )
                    story.append(Spacer(1, 5))
            story.append(Spacer(1, 4))

        document.build(story, onFirstPage=self._draw_page_template, onLaterPages=self._draw_page_template)

    def _styles(self) -> dict[str, ParagraphStyle]:
        sample = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "PdfTitle",
                parent=sample["Title"],
                fontName=self.font_name,
                fontSize=20,
                leading=26,
                textColor=self.text,
            ),
            "meta": ParagraphStyle(
                "PdfMeta",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=9.6,
                leading=13,
                textColor=self.text_muted,
            ),
            "section": ParagraphStyle(
                "PdfSection",
                parent=sample["Heading2"],
                fontName=self.font_name,
                fontSize=12.6,
                leading=18,
                textColor=self.brand,
                spaceBefore=2,
            ),
            "section-risk": ParagraphStyle(
                "PdfSectionRisk",
                parent=sample["Heading2"],
                fontName=self.font_name,
                fontSize=12.6,
                leading=18,
                textColor=self.risk,
                spaceBefore=2,
            ),
            "section-notice": ParagraphStyle(
                "PdfSectionNotice",
                parent=sample["Heading2"],
                fontName=self.font_name,
                fontSize=12.6,
                leading=18,
                textColor=self.notice,
                spaceBefore=2,
            ),
            "section-evidence": ParagraphStyle(
                "PdfSectionEvidence",
                parent=sample["Heading2"],
                fontName=self.font_name,
                fontSize=12.6,
                leading=18,
                textColor=self.evidence,
                spaceBefore=2,
            ),
            "body": ParagraphStyle(
                "PdfBody",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=10.4,
                leading=16,
                textColor=self.text,
            ),
            "body-paragraph": ParagraphStyle(
                "PdfBodyParagraph",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=10.4,
                leading=16,
                textColor=self.text,
                firstLineIndent=20.8,
            ),
            "body-list": ParagraphStyle(
                "PdfBodyList",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=10.4,
                leading=16,
                textColor=self.text,
                leftIndent=15,
                bulletIndent=3,
            ),
            "body-list-risk": ParagraphStyle(
                "PdfBodyListRisk",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=10.4,
                leading=16,
                textColor=self.risk,
                leftIndent=15,
                bulletIndent=3,
            ),
            "body-muted": ParagraphStyle(
                "PdfBodyMuted",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=9.8,
                leading=15,
                textColor=self.text_muted,
            ),
            "body-risk": ParagraphStyle(
                "PdfBodyRisk",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=10.4,
                leading=16,
                textColor=self.risk,
            ),
            "subsection": ParagraphStyle(
                "PdfSubsection",
                parent=sample["Heading3"],
                fontName=self.font_name,
                fontSize=11.2,
                leading=15.5,
                textColor=self.brand,
                spaceBefore=4,
                spaceAfter=1,
                leftIndent=1.5 * mm,
            ),
            "table-header": ParagraphStyle(
                "PdfTableHeader",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=9.4,
                leading=12,
                textColor=colors.white,
                alignment=1,
            ),
            "table-cell": ParagraphStyle(
                "PdfTableCell",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=8.7,
                leading=12.2,
                textColor=self.text,
                wordWrap="CJK",
            ),
            "table-cell-muted": ParagraphStyle(
                "PdfTableCellMuted",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=8.4,
                leading=11.8,
                textColor=self.text_muted,
                wordWrap="CJK",
            ),
            "table-cell-risk": ParagraphStyle(
                "PdfTableCellRisk",
                parent=sample["BodyText"],
                fontName=self.font_name,
                fontSize=8.4,
                leading=11.8,
                textColor=self.risk,
                wordWrap="CJK",
            ),
        }

    def _section_style(self, section_title: str, styles: dict[str, ParagraphStyle]) -> ParagraphStyle:
        canonical_title = self._canonical_section_title(section_title)
        if canonical_title in {"风险提示", "关键指标", "关键指标摘要", "异常指标汇总"}:
            return styles["section-risk"]
        if canonical_title in {"生活方式建议", "生活方式干预重点", "生活方式干预处方", "待确认项", "需要补充确认", "复查与跟进建议", "后续检查建议", "现有补充剂调整建议"}:
            return styles["section-notice"]
        if canonical_title == "证据来源":
            return styles["section-evidence"]
        return styles["section"]

    def _body_style(
        self,
        section_title: str,
        styles: dict[str, ParagraphStyle],
        *,
        is_list: bool,
    ) -> ParagraphStyle:
        if section_title == "证据来源":
            return styles["body-muted"]
        if is_list and section_title in {"关键指标", "关键指标摘要", "异常指标汇总", "风险提示"}:
            return styles["body-list-risk"]
        if is_list:
            return styles["body-list"]
        if section_title in {"关键指标", "关键指标摘要", "异常指标汇总", "风险提示"}:
            return styles["body-risk"]
        return styles["body-paragraph"]

    def _parse_report(self, report_text: str) -> tuple[str, list[tuple[str, list[PdfReportItem]]]]:
        title = "功能医学营养干预报告"
        sections: list[tuple[str, list[PdfReportItem]]] = []
        current_title: str | None = None
        current_items: list[PdfReportItem] = []
        skip_titles = {"病例摘要", "证据来源", "审核备注", "审计信息"}
        skipping = False

        for raw_line in report_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# "):
                title = line[2:].strip()
                continue
            if line.startswith("## "):
                if current_title:
                    sections.append((current_title, current_items))
                section_title = line[3:].strip()
                canonical_title = self._canonical_section_title(section_title)
                skipping = canonical_title in skip_titles or self._is_hidden_customer_section(canonical_title)
                current_title = None if skipping else section_title
                current_items = []
                continue
            if current_title is None or skipping:
                continue
            if line.startswith("### "):
                current_items.append(PdfReportItem(kind="subsection", text=line[4:].strip()))
            elif line.startswith(("- ", "* ", "• ")):
                current_items.append(PdfReportItem(kind="list", text=line[2:].strip()))
            else:
                current_items.append(PdfReportItem(kind="paragraph", text=line))

        if current_title:
            sections.append((current_title, current_items))
        return title, sections

    def _subsection_items(self, items: list[PdfReportItem], title: str) -> list[str]:
        collecting = False
        collected: list[str] = []
        for item in items:
            if item.kind == "subsection":
                collecting = self._canonical_section_title(item.text) == title
                continue
            if collecting:
                collected.append(item.text)
        return collected

    def _is_hidden_customer_section(self, section_title: str) -> bool:
        return (
            section_title.startswith("RAG")
            or "内部审查" in section_title
            or "知识库" in section_title
            or "仅供参考" in section_title
        )

    def _load_product_report_catalog(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"products": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"products": {}}
        products = payload.get("products")
        return payload if isinstance(products, dict) else {"products": {}}

    def _build_nutrition_table_flowables(
        self,
        recommended_skus: list[Any],
        styles: dict[str, ParagraphStyle],
        *,
        prescription_advice_items: list[str] | None = None,
    ) -> list[Any]:
        rows = self._nutrition_table_rows(recommended_skus)
        if not rows:
            return []

        flowables: list[Any] = [
            Paragraph(escape(self._build_dose_summary(rows)), styles["body-muted"]),
            Spacer(1, 7),
        ]
        header = ["营养素序号", "营养素名称", "主要功效", "服用说明"]
        data: list[list[Any]] = [[Paragraph(escape(item), styles["table-header"]) for item in header]]

        for row in rows:
            dosage_html = escape(row["dosage"])
            if row["warnings"]:
                dosage_html += (
                    "<br/><font color='#b04a34'><b>注意/禁忌：</b>"
                    + escape(self._format_warning_text(row["warnings"]))
                    + "</font>"
                )
            data.append(
                [
                    Paragraph(escape(row["sequence"]), styles["table-cell"]),
                    Paragraph(escape(row["product_name"]), styles["table-cell"]),
                    Paragraph(escape(row["effect"]), styles["table-cell"]),
                    Paragraph(dosage_html, styles["table-cell"]),
                ]
            )

        table = Table(
            data,
            colWidths=[23 * mm, 32 * mm, 88 * mm, 35 * mm],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.brand),
                    ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#d8ddd7")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbf8f0")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        flowables.append(table)
        flowables.extend(self._build_prescription_advice_flowables(prescription_advice_items or [], styles))
        return flowables

    def _build_prescription_advice_flowables(
        self,
        items: list[str],
        styles: dict[str, ParagraphStyle],
    ) -> list[Any]:
        cleaned_items = [self._clean_customer_text(item) for item in items if self._clean_customer_text(item)]
        if not cleaned_items:
            return []

        flowables: list[Any] = [
            Spacer(1, 9),
            Paragraph("总医嘱说明", styles["subsection"]),
            Spacer(1, 4),
        ]
        for item in cleaned_items:
            flowables.append(Paragraph(self._format_item("总医嘱说明", item), styles["body-paragraph"]))
            flowables.append(Spacer(1, 4))
        return flowables

    def _nutrition_table_rows(self, recommended_skus: list[Any]) -> list[dict[str, Any]]:
        products = self.product_report_catalog.get("products", {})
        rows: list[dict[str, Any]] = []
        seen_skus: set[str] = set()

        for sku in recommended_skus:
            sku_id = self._sku_value(sku, "sku_id")
            if sku_id in seen_skus:
                continue
            seen_skus.add(sku_id)
            display_name = self._clean_customer_text(self._sku_value(sku, "display_name"))
            product_profile = products.get(sku_id, {}) if sku_id else {}
            product_name = self._clean_customer_text(product_profile.get("product_name") or display_name or "营养素")
            sequence = self._clean_customer_text(product_profile.get("sequence") or "待确认")
            dosage = self._clean_customer_text(self._sku_value(sku, "dosage") or "请按顾问建议使用")
            reason = self._clean_customer_text(self._sku_value(sku, "reason"))
            warnings = self._public_warnings(self._sku_list_value(sku, "warnings"))
            description = self._full_product_description(product_profile.get("description", ""))
            effect = self._clean_customer_text(description or "用于本次个性化营养支持，具体适用性已结合当前报告结果筛选。")

            rows.append(
                {
                    "sequence": sequence,
                    "product_name": product_name,
                    "effect": effect,
                    "dosage": dosage,
                    "reason": reason,
                    "warnings": warnings,
                }
            )
        return rows

    def _build_dose_summary(self, rows: list[dict[str, Any]]) -> str:
        slot_order = ["早餐后", "午餐后", "晚餐后", "随餐/餐后", "晚间/睡前", "需人工确认", "按顾问建议"]
        counts = {slot: 0 for slot in slot_order}
        for row in rows:
            counts[self._dose_slot(row["dosage"])] += 1
        parts = [f"{slot}{counts[slot]}项" for slot in slot_order if counts[slot]]
        summary = f"每日服用概览：本次方案共 {len(rows)} 项营养素，" + "，".join(parts) + "。"
        return summary + "具体剂量、服用时间和注意事项请以表格为准。"

    def _dose_slot(self, dosage: str) -> str:
        normalized = dosage.lower()
        if any(token in dosage for token in ("人工确认", "顾问确认", "医生确认", "仅在")):
            return "需人工确认"
        if "早餐" in dosage:
            return "早餐后"
        if "午餐" in dosage:
            return "午餐后"
        if "晚餐" in dosage:
            return "晚餐后"
        if any(token in dosage for token in ("睡前", "晚间", "傍晚")):
            return "晚间/睡前"
        if any(token in dosage for token in ("随餐", "餐后", "主餐", "正餐")):
            return "随餐/餐后"
        if "as needed" in normalized:
            return "按顾问建议"
        return "按顾问建议"

    def _full_product_description(self, description: str) -> str:
        text = self._clean_customer_text(description)
        replacements = {
            "强效": "",
            "精准": "",
            "高效": "",
            "从根源": "",
            "告别": "改善",
            "优选": "可选",
            "突破传统VC吸收局限": "用于改善传统维生素C的吸收利用",
            "快速降低": "支持调节",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return re.sub(r"\s+", " ", text).strip()

    def _public_warnings(self, warnings: list[Any], *, limit: int = 2) -> list[str]:
        public_warnings: list[str] = []
        for warning in warnings:
            cleaned = self._clean_customer_text(str(warning))
            if not cleaned or "sku" in cleaned.lower() or "规格" in cleaned:
                continue
            public_warnings.append(self._strip_trailing_sentence_punctuation(cleaned))
        return list(dict.fromkeys(public_warnings))[:limit]

    def _format_warning_text(self, warnings: list[str]) -> str:
        normalized = [self._strip_trailing_sentence_punctuation(item) for item in warnings]
        normalized = [item for item in normalized if item]
        if not normalized:
            return ""
        return "；".join(normalized) + "。"

    def _strip_trailing_sentence_punctuation(self, value: str) -> str:
        return re.sub(r"[。；;，,\s]+$", "", self._clean_customer_text(value))

    def _sku_value(self, sku: Any, field: str) -> str:
        value = sku.get(field, "") if isinstance(sku, dict) else getattr(sku, field, "")
        return str(value).strip() if value is not None else ""

    def _sku_list_value(self, sku: Any, field: str) -> list[Any]:
        value = sku.get(field, []) if isinstance(sku, dict) else getattr(sku, field, [])
        return value if isinstance(value, list) else []

    def _format_item(self, section_title: str, item: str) -> str:
        item = self._clean_customer_text(item)
        if self._is_subheading_item(item):
            return self._format_subheading_item(item)
        if section_title in {"总体健康画像"}:
            return self._highlight_tokens(item)

        if section_title in {"关键指标", "关键指标摘要", "异常指标汇总"}:
            if "：" in item:
                label, rest = item.split("：", 1)
                return (
                    f"<font color='#14564a'><b>{escape(label)}：</b></font>"
                    f"<font color='#b04a34'><b>{escape(rest)}</b></font>"
                )
            return f"<font color='#b04a34'><b>{escape(item)}</b></font>"

        if section_title in {"营养素推荐", "个性化营养素方案", "首月营养素干预方案"}:
            formatted = self._highlight_tokens(item)
            formatted = formatted.replace(
                "适用说明：",
                "<font color='#647067'>适用说明：</font>",
            )
            formatted = formatted.replace(
                "目的：",
                "<font color='#647067'>目的：</font>",
            )
            formatted = formatted.replace(
                "注意/禁忌：",
                "<font color='#b04a34'><b>注意/禁忌：</b></font>",
            )
            return formatted

        if "：" in item and section_title in {
            "病例摘要",
            "核心结论与健康画像",
            "总体健康画像",
            "关键指标摘要",
            "异常指标汇总",
            "风险提示",
            "功能医学系统失衡分析",
            "待确认项",
        }:
            label, rest = item.split("：", 1)
            return f"<font color='#14564a'><b>{escape(label)}：</b></font>{self._highlight_tokens(rest.strip())}"

        return self._highlight_tokens(item)

    def _is_subheading_item(self, item: str) -> bool:
        return self._clean_customer_text(item).startswith("### ")

    def _format_subheading_item(self, item: str) -> str:
        cleaned = self._clean_customer_text(item)
        title = cleaned[4:].strip() if cleaned.startswith("### ") else cleaned
        return f"<font color='#14564a'><b>{escape(title)}</b></font>"

    def _highlight_tokens(self, text: str) -> str:
        escaped = escape(self._clean_customer_text(text))
        replacements = {
            "高风险": "<font color='#b04a34'><b>高风险</b></font>",
            "低风险": "<font color='#b07a1f'><b>低风险</b></font>",
            "人工复核": "<font color='#b07a1f'><b>人工复核</b></font>",
            "偏高": "<font color='#b04a34'><b>偏高</b></font>",
            "升高": "<font color='#b04a34'><b>升高</b></font>",
            "异常": "<font color='#b04a34'><b>异常</b></font>",
            "偏低": "<font color='#b07a1f'><b>偏低</b></font>",
            "不足": "<font color='#b07a1f'><b>不足</b></font>",
            "(high)": "<font color='#b04a34'><b>(high)</b></font>",
            "(low)": "<font color='#b07a1f'><b>(low)</b></font>",
        }
        for source, replacement in replacements.items():
            escaped = escaped.replace(source, replacement)
        escaped = re.sub(
            r"(\d+(?:\.\d+)?)\s*(ng/mL|pg/mL|mmol/L|IU/mL|mIU/L|mg/L|U/L)",
            r"<font color='#14564a'><b>\1 \2</b></font>",
            escaped,
            flags=re.IGNORECASE,
        )
        escaped = re.sub(
            r"(\d+(?:\.\d+)?)\s*(粒|次|小时|分钟|天|周|月|年|%|％)",
            r"<font color='#14564a'><b>\1\2</b></font>",
            escaped,
        )
        return escaped

    def _draw_page_template(self, canvas, doc) -> None:
        canvas.saveState()
        if self.logo_path.exists():
            try:
                canvas.drawImage(
                    str(self.logo_path),
                    doc.leftMargin,
                    A4[1] - 21 * mm,
                    width=42 * mm,
                    height=11 * mm,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pass
        canvas.setFillColor(self.text_muted)
        canvas.setFont(self.font_name, 8.8)
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    @staticmethod
    def _canonical_section_title(value: str) -> str:
        return re.sub(
            r"^(?:[零一二三四五六七八九十百]+|\d+)[、.．]\s*",
            "",
            str(value or "").strip(),
        )

    def _clean_customer_text(self, value: str) -> str:
        cleaned = str(value or "").replace("\ufffd", "").strip()
        cleaned = re.sub(r"\?{3,}", "", cleaned)
        cleaned = re.sub(r"[A-Za-z]:\\[^\s，。；：]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+([，。；：、])", r"\1", cleaned)
        return cleaned.strip()

    def _truncate_text(self, value: str, limit: int) -> str:
        cleaned = self._clean_customer_text(value)
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 3)].rstrip("，。；：、 ") + "..."

    def _sanitize_filename(self, value: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
        cleaned = re.sub(r"\s+", "_", cleaned)
        return cleaned or "report"
