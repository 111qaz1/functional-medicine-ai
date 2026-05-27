from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path


RAG_LINE_PREFIX = "功能医学知识库（仅供参考）："
RAG_TARGET_SECTIONS = (
    "总体健康画像",
    "关键指标",
    "生活方式干预重点",
    "复查与跟进建议",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_settings(runtime_root: Path, *, rag_enabled: bool):
    from app.core.settings import AppSettings

    root = project_root()
    return AppSettings(
        project_root=root,
        data_dir=root / "backend" / "app" / "data",
        runtime_dir=runtime_root / ".runtime",
        upload_dir=runtime_root / ".runtime" / "uploads",
        report_export_dir=runtime_root / ".runtime" / "reports",
        sqlite_path=runtime_root / ".runtime" / "comparison.sqlite3",
        knowledge_root=runtime_root / "功能医学相关资料",
        report_reference_path=runtime_root / "report-reference.pdf",
        rag_enabled=rag_enabled,
        rag_index_dir=root / "backend" / "app" / "data" / "rag_index",
    )


def prepare_case(container):
    from app.domain.models import Questionnaire, UploadedFile

    report_text = "\n".join(
        [
            "25-OH维生素D 18 ng/mL 30-100",
            "空腹血糖 6.2 mmol/L 3.9-5.6",
            "hs-CRP 4.2 mg/L 0-3",
            "甲状腺过氧化物酶抗体 329 IU/mL 0-95",
            "甘油三酯 1.97 mmol/L 0.00-1.70",
        ]
    )
    case = container.case_service.create_case(
        customer_name="RAG固定审查病例",
        consultant_id="nutrition-team",
        notes=None,
        consent=None,
    )
    uploaded_file = UploadedFile(
        id=f"file_{case.id}",
        case_id=case.id,
        filename="review_case.txt",
        content_type="text/plain",
        size_bytes=len(report_text.encode("utf-8")),
        storage_uri="memory://review_case.txt",
    )
    container.case_service.add_uploaded_file(case.id, uploaded_file)
    extraction, lab_items = container.parsing_service.parse(
        filename="review_case.txt",
        content_type="text/plain",
        content=report_text.encode("utf-8"),
    )
    container.case_service.attach_parse_results(
        case.id,
        uploaded_file.id,
        extracted_text=extraction.text,
        parse_confidence=extraction.confidence,
        source_spans=extraction.spans,
        lab_items=lab_items,
    )
    container.case_service.review_parsing(
        case.id,
        reviewer_id="reviewer-01",
        file_updates=[{"file_id": uploaded_file.id, "corrected_text": extraction.text, "missing_fields": []}],
        normalized_lab_items=lab_items,
        missing_fields=[],
        review_notes="rag comparison",
    )
    container.case_service.submit_questionnaire(
        case.id,
        Questionnaire(
            age=38,
            sex="female",
            symptoms=["疲劳", "便秘", "睡眠质量差"],
            known_conditions=["桥本氏甲状腺炎"],
            medications=[],
            allergies=[],
            goals=["血糖平衡", "免疫支持", "睡眠恢复"],
            sleep_hours=5.5,
            sleep_quality="差",
            bowel_habits="便秘",
            stress_level="high",
        ),
    )
    return case


def generate_one(*, rag_enabled: bool) -> dict:
    from app.core.bootstrap import build_container

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        (runtime_root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        container = build_container(build_settings(runtime_root, rag_enabled=rag_enabled))
        if rag_enabled and container.recommendation_service.rag_retriever is None:
            raise RuntimeError("RAG was enabled but the retriever did not load")
        case = prepare_case(container)
        draft = container.recommendation_service.generate(case.id, requested_by="rag-comparison")
        review = container.review_service.approve(
            draft.id,
            reviewer_id="reviewer-01",
            publishable_summary=None,
            edits={},
        )
        return {
            "rag_enabled": rag_enabled,
            "recommended_skus": [item.sku_id for item in draft.recommended_skus],
            "report_sections": draft.report_sections,
            "publishable_report": review.publishable_report,
            "abstain_reason": draft.abstain_reason,
            "rag_audit": draft.report_sections.get("RAG内部审查", []),
        }


def report_bullets_by_section(report: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_section = ""
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections.setdefault(current_section, [])
            continue
        if current_section and line.startswith("- "):
            sections.setdefault(current_section, []).append(line[2:].strip())
    return sections


def normalize_rag_line(line: str) -> str:
    cleaned = line.replace(RAG_LINE_PREFIX, "")
    cleaned = re.sub(r"；这部分仅作为营养支持背景说明.*$", "", cleaned)
    cleaned = re.sub(r"具体补充剂、禁忌和复查安排仍以医生审核与产品规则为准.*$", "", cleaned)
    cleaned = re.sub(r"^[\\-\\s]+", "", cleaned)
    cleaned = re.sub(r"[\\s，。；、：:（）()“”\"'`]+", "", cleaned)
    return cleaned.lower()


def rag_influenced_lines(before_report: str, after_report: str) -> list[dict[str, str | int]]:
    before_sections = report_bullets_by_section(before_report)
    after_sections = report_bullets_by_section(after_report)
    influenced: list[dict[str, str | int]] = []
    for section in RAG_TARGET_SECTIONS:
        before_items = before_sections.get(section, [])
        after_items = after_sections.get(section, [])
        for index, after_item in enumerate(after_items):
            before_item = before_items[index] if index < len(before_items) else ""
            if normalize_rag_line(before_item) == normalize_rag_line(after_item):
                continue
            influenced.append(
                {
                    "section": section,
                    "index": index + 1,
                    "before": before_item,
                    "after": after_item,
                }
            )
    return influenced


def customer_rag_prefix_leaks(report: str) -> list[str]:
    return [line.strip() for line in report.splitlines() if RAG_LINE_PREFIX in line]


def duplicate_rag_pairs(rag_lines: list[str]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    normalized = [(line, normalize_rag_line(line)) for line in rag_lines]
    for left_index, (left_line, left_text) in enumerate(normalized):
        for right_line, right_text in normalized[left_index + 1 :]:
            if not left_text or not right_text:
                continue
            shorter, longer = sorted((left_text, right_text), key=len)
            if left_text == right_text or (len(shorter) >= 24 and shorter in longer):
                pairs.append({"first": left_line, "second": right_line})
    return pairs


def write_markdown(path: Path, payload: dict) -> None:
    before = payload["before"]
    after = payload["after"]
    duplicate_pairs = payload.get("duplicate_rag_pairs") or []
    influenced_lines = payload.get("rag_influenced_lines") or []
    prefix_leaks = payload.get("customer_rag_prefix_leaks") or []
    lines = [
        "# RAG Report Comparison",
        "",
        "This internal review artifact compares one fixed case with RAG disabled vs enabled. The customer-facing report must not display RAG labels; changed lines below are marked only for internal review.",
        "",
        f"- recommended_skus_before: `{', '.join(before['recommended_skus'])}`",
        f"- recommended_skus_after: `{', '.join(after['recommended_skus'])}`",
        f"- recommendation_changed: `{before['recommended_skus'] != after['recommended_skus']}`",
        f"- rag_audit: `{'; '.join(after.get('rag_audit') or [])}`",
        f"- rag_influenced_line_count: `{len(influenced_lines)}`",
        f"- customer_rag_prefix_leak_count: `{len(prefix_leaks)}`",
        f"- duplicate_rag_pair_count: `{len(duplicate_pairs)}`",
        "",
        "## RAG-Influenced Lines (Internal Review Only)",
        "",
    ]
    if influenced_lines:
        for item in influenced_lines:
            lines.append(f"- {item['section']} #{item['index']}")
            if item.get("before"):
                lines.append(f"  - before: {item['before']}")
            lines.append(f"  - after: {item['after']}")
    else:
        lines.append("- No customer report lines changed after enabling RAG.")
    lines.extend(["", "## Customer Label Leak Check", ""])
    if prefix_leaks:
        for leak in prefix_leaks:
            lines.append(f"- leaked_label_line: `{leak}`")
    else:
        lines.append("- No customer-facing RAG labels detected.")
    lines.extend(["", "## RAG Reuse Check", ""])
    if duplicate_pairs:
        for pair in duplicate_pairs:
            lines.append(f"- duplicate_or_contained: `{pair['first']}` / `{pair['second']}`")
    else:
        lines.append("- No duplicate or contained RAG-influenced customer report lines detected.")
    lines.extend(["", "## Before", "", before["publishable_report"], "", "## After", "", after["publishable_report"]])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Generate fixed-case before/after RAG report comparison.")
    parser.add_argument("--extra-site-packages", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_staging")
    args = parser.parse_args()

    if args.extra_site_packages is not None:
        sys.path.insert(0, str(args.extra_site_packages))
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))

    before = generate_one(rag_enabled=False)
    after = generate_one(rag_enabled=True)
    influenced_lines = rag_influenced_lines(before["publishable_report"], after["publishable_report"])
    influenced_after_lines = [str(item["after"]) for item in influenced_lines]
    prefix_leaks = customer_rag_prefix_leaks(after["publishable_report"])
    payload = {
        "before": before,
        "after": after,
        "rag_influenced_lines": influenced_lines,
        "rag_influenced_line_count": len(influenced_lines),
        "added_rag_lines": influenced_after_lines,
        "customer_rag_prefix_leaks": prefix_leaks,
        "duplicate_rag_pairs": duplicate_rag_pairs(influenced_after_lines),
        "recommendation_changed": before["recommended_skus"] != after["recommended_skus"],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "rag_report_comparison.json"
    markdown_path = args.output_dir / "rag_report_comparison.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(markdown_path, payload)
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
