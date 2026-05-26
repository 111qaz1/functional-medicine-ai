from __future__ import annotations

import re
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


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

        try:
            pdfmetrics.getFont(self.font_name)
        except KeyError:
            pdfmetrics.registerFont(UnicodeCIDFont(self.font_name))

    def export(self, *, draft_id: str, customer_name: str, report_text: str) -> Path:
        safe_name = self._sanitize_filename(customer_name)
        target = self.root / f"{safe_name}-{draft_id}.pdf"
        self._build_pdf(target, customer_name=customer_name, draft_id=draft_id, report_text=report_text)
        return target

    def _build_pdf(self, target: Path, *, customer_name: str, draft_id: str, report_text: str) -> None:
        title, sections = self._parse_report(report_text)
        document = SimpleDocTemplate(
            str(target),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=18 * mm,
            bottomMargin=16 * mm,
            title=title,
            author="Functional Medicine Nutrition AI",
        )

        styles = self._styles()
        story = [
            Paragraph(escape(title), styles["title"]),
            Spacer(1, 4),
            Paragraph(
                escape(f"客户：{customer_name} ｜ 报告编号：{draft_id} ｜ 类型：功能医学营养与生活方式建议"),
                styles["meta"],
            ),
            Spacer(1, 12),
        ]

        for section_title, items in sections:
            story.append(Paragraph(escape(section_title), self._section_style(section_title, styles)))
            story.append(Spacer(1, 5))
            for item in items:
                story.append(Paragraph(self._format_item(section_title, item), self._body_style(section_title, styles)))
                story.append(Spacer(1, 5))
            story.append(Spacer(1, 4))

        document.build(story, onFirstPage=self._draw_page_footer, onLaterPages=self._draw_page_footer)

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
        }

    def _section_style(self, section_title: str, styles: dict[str, ParagraphStyle]) -> ParagraphStyle:
        if section_title in {"风险提示", "关键指标", "关键指标摘要"}:
            return styles["section-risk"]
        if section_title in {"生活方式建议", "生活方式干预重点", "待确认项", "需要补充确认", "复查与跟进建议"}:
            return styles["section-notice"]
        if section_title == "证据来源":
            return styles["section-evidence"]
        return styles["section"]

    def _body_style(self, section_title: str, styles: dict[str, ParagraphStyle]) -> ParagraphStyle:
        if section_title == "证据来源":
            return styles["body-muted"]
        if section_title in {"关键指标", "关键指标摘要", "风险提示"}:
            return styles["body-risk"]
        return styles["body"]

    def _parse_report(self, report_text: str) -> tuple[str, list[tuple[str, list[str]]]]:
        title = "功能医学营养干预报告"
        sections: list[tuple[str, list[str]]] = []
        current_title: str | None = None
        current_items: list[str] = []
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
                skipping = section_title in skip_titles
                current_title = None if skipping else section_title
                current_items = []
                continue
            if current_title is None or skipping:
                continue
            current_items.append(line[2:].strip() if line.startswith("- ") else line)

        if current_title:
            sections.append((current_title, current_items))
        return title, sections

    def _format_item(self, section_title: str, item: str) -> str:
        if section_title in {"总体健康画像"}:
            return self._highlight_tokens(item)

        if section_title in {"关键指标", "关键指标摘要"}:
            return f"- <font color='#b04a34'><b>{escape(item)}</b></font>"

        if section_title in {"营养素推荐", "个性化营养素方案"}:
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
            return f"- {formatted}"

        if "：" in item and section_title in {"病例摘要", "关键指标摘要", "风险提示", "待确认项"}:
            label, rest = item.split("：", 1)
            return f"- <font color='#14564a'><b>{escape(label)}：</b></font>{self._highlight_tokens(rest.strip())}"

        return f"- {self._highlight_tokens(item)}"

    def _highlight_tokens(self, text: str) -> str:
        escaped = escape(text)
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
            r"(\d+(?:\.\d+)?)\s*(ng/mL|pg/mL|mmol/L|IU/mL|mIU/L|mg/L|U/L|粒|次|小时|%)",
            r"<font color='#14564a'><b>\1 \2</b></font>",
            escaped,
            flags=re.IGNORECASE,
        )
        return escaped

    def _draw_page_footer(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(self.text_muted)
        canvas.setFont(self.font_name, 8.8)
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    def _sanitize_filename(self, value: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
        cleaned = re.sub(r"\s+", "_", cleaned)
        return cleaned or "report"
