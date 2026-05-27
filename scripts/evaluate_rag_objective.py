from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


SOURCE_LEAK_RE = re.compile(r"[A-Za-z]:\\|\.docx|\.pdf|ISBN|第\s*\d+\s*页|source_title|chunk_id", re.IGNORECASE)
ACTIONABLE_RISK_RE = re.compile(
    r"(推荐|建议|可考虑|应当|需要).{0,20}(服用|使用|口服|给药|加用).{0,25}"
    r"(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射)"
    r"|"
    r"(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射).{0,25}"
    r"(剂量|mg|毫克|每日|每天|口服|疗程|停药|加量|减量)"
)
RAG_LINE_PREFIX = "功能医学知识库（仅供参考）："


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_products(root: Path):
    from app.domain.models import ProductRule

    payload = json.loads((root / "backend" / "app" / "data" / "product_catalog.json").read_text(encoding="utf-8-sig"))
    return [ProductRule.model_validate(item) for item in payload]


def contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def safe_answer_lines(safe_hits: list[Any]) -> list[str]:
    return [f"{RAG_LINE_PREFIX}{hit.excerpt}" for hit in safe_hits[:3]]


def evaluate_case(case: dict[str, Any], retriever: Any, safety_filter: Any) -> dict[str, Any]:
    query = case["query"]
    expected = list(case.get("expected_any") or [])
    raw_hits = retriever.hybrid_search(query, top_k=5)
    context = SimpleNamespace(
        pregnancy=False,
        medications=set(),
        conditions=set(),
        allergies=set(),
    )
    safe_hits, rejections = safety_filter.filter_hits(
        list(raw_hits),
        context=context,
        red_flags=[],
        contraindications=[],
        max_hits=5,
    )
    raw_texts = [hit.text for hit in raw_hits]
    safe_texts = [hit.excerpt for hit in safe_hits]
    answer_lines = safe_answer_lines(safe_hits)
    answer_text = "\n".join(answer_lines)

    expected_context_hits = [
        index
        for index, text in enumerate(raw_texts, start=1)
        if expected and contains_any(text, expected)
    ]
    safe_expected_hits = [
        index
        for index, text in enumerate(safe_texts, start=1)
        if expected and contains_any(text, expected)
    ]
    answer_expected_hit = bool(expected and contains_any(answer_text, expected))
    source_leak = bool(SOURCE_LEAK_RE.search(answer_text))
    actionable_risk = bool(ACTIONABLE_RISK_RE.search(answer_text))
    faithfulness_proxy = all(
        line.replace(RAG_LINE_PREFIX, "", 1) in {hit.excerpt for hit in safe_hits}
        for line in answer_lines
    )

    return {
        "id": case["id"],
        "topic": case["topic"],
        "case_type": case.get("case_type", "positive" if expected else "boundary"),
        "query": query,
        "expected_any": expected,
        "raw_hit_count": len(raw_hits),
        "safe_hit_count": len(safe_hits),
        "rejection_reasons": [item.reason for item in rejections],
        "context_hit_at_5": bool(expected_context_hits) if expected else None,
        "safe_context_hit_at_5": bool(safe_expected_hits) if expected else None,
        "context_precision_at_5": round(len(expected_context_hits) / max(len(raw_hits), 1), 4) if expected else None,
        "safe_context_precision_at_5": round(len(safe_expected_hits) / max(len(safe_hits), 1), 4) if expected else None,
        "answer_relevancy_proxy": answer_expected_hit if expected else None,
        "faithfulness_proxy": faithfulness_proxy,
        "source_leak": source_leak,
        "actionable_risk": actionable_risk,
        "answer_preview": answer_lines,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [item for item in results if item["case_type"] == "positive"]
    boundaries = [item for item in results if item["case_type"] != "positive"]

    def avg(values: list[float | None]) -> float | None:
        numeric = [float(value) for value in values if value is not None]
        return round(sum(numeric) / len(numeric), 4) if numeric else None

    positive_hit_rate = sum(1 for item in positives if item["context_hit_at_5"]) / max(len(positives), 1)
    safe_positive_hit_rate = sum(1 for item in positives if item["safe_context_hit_at_5"]) / max(len(positives), 1)
    answer_relevancy = sum(1 for item in positives if item["answer_relevancy_proxy"]) / max(len(positives), 1)
    faithfulness = sum(1 for item in results if item["faithfulness_proxy"]) / max(len(results), 1)
    source_safety = sum(1 for item in results if not item["source_leak"]) / max(len(results), 1)
    risk_safety = sum(1 for item in results if not item["actionable_risk"]) / max(len(results), 1)

    gate = {
        "positive_context_hit_rate_at_5": round(positive_hit_rate, 4),
        "safe_positive_context_hit_rate_at_5": round(safe_positive_hit_rate, 4),
        "answer_relevancy_proxy": round(answer_relevancy, 4),
        "faithfulness_proxy": round(faithfulness, 4),
        "source_safety_rate": round(source_safety, 4),
        "actionable_risk_safety_rate": round(risk_safety, 4),
        "minimum_positive_context_hit_rate_at_5": 0.8,
        "minimum_safe_positive_context_hit_rate_at_5": 0.75,
        "required_source_safety_rate": 1.0,
        "required_actionable_risk_safety_rate": 1.0,
    }
    gate["passed"] = (
        gate["positive_context_hit_rate_at_5"] >= gate["minimum_positive_context_hit_rate_at_5"]
        and gate["safe_positive_context_hit_rate_at_5"] >= gate["minimum_safe_positive_context_hit_rate_at_5"]
        and gate["source_safety_rate"] >= gate["required_source_safety_rate"]
        and gate["actionable_risk_safety_rate"] >= gate["required_actionable_risk_safety_rate"]
    )
    return {
        "case_count": len(results),
        "positive_case_count": len(positives),
        "boundary_case_count": len(boundaries),
        "context_precision_at_5_avg": avg([item["context_precision_at_5"] for item in positives]),
        "safe_context_precision_at_5_avg": avg([item["safe_context_precision_at_5"] for item in positives]),
        "methodology_note": (
            "This deterministic report is a safety and retrieval regression gate, not a clinical quality score. "
            "The 1.0 rates mean the fixed keyword-based cases passed their proxy checks; they do not prove that "
            "customer-facing report prose is concise, non-repetitive, or clinically sufficient."
        ),
        "quality_gate": gate,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    gate = payload["summary"]["quality_gate"]
    lines = [
        "# RAGAS-like Objective RAG Evaluation",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- case_count: `{payload['summary']['case_count']}`",
        f"- positive_case_count: `{payload['summary']['positive_case_count']}`",
        f"- context_precision_at_5_avg: `{payload['summary']['context_precision_at_5_avg']}`",
        f"- safe_context_precision_at_5_avg: `{payload['summary']['safe_context_precision_at_5_avg']}`",
        f"- quality_gate_passed: `{gate['passed']}`",
        "",
        "This is a deterministic proxy for RAGAS-style checks: context hit rate, context precision, answer relevancy, faithfulness, and safety.",
        "",
        f"Important limitation: {payload['summary']['methodology_note']}",
        "",
        "## Quality Gate",
        "",
    ]
    for key, value in gate.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Cases", ""])
    for item in payload["cases"]:
        lines.extend(
            [
                f"### {item['id']} | {item['topic']}",
                "",
                f"- query: `{item['query']}`",
                f"- raw_hit_count: `{item['raw_hit_count']}`",
                f"- safe_hit_count: `{item['safe_hit_count']}`",
                f"- context_hit_at_5: `{item['context_hit_at_5']}`",
                f"- safe_context_hit_at_5: `{item['safe_context_hit_at_5']}`",
                f"- source_leak: `{item['source_leak']}`",
                f"- actionable_risk: `{item['actionable_risk']}`",
                f"- rejection_reasons: `{', '.join(item['rejection_reasons']) if item['rejection_reasons'] else 'none'}`",
                "",
            ]
        )
        for answer_line in item["answer_preview"][:3]:
            lines.append(f"- {answer_line}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Run deterministic RAGAS-like objective checks for the local RAG index.")
    parser.add_argument("--index-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_index")
    parser.add_argument("--output-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_staging")
    parser.add_argument("--extra-site-packages", type=Path, default=None)
    args = parser.parse_args()

    if args.extra_site_packages is not None:
        sys.path.insert(0, str(args.extra_site_packages))
    sys.path.insert(0, str(root / "backend"))
    sys.path.insert(0, str(root))

    from app.services.rag_retriever import RagRetriever
    from app.services.rag_safety import RagSafetyFilter
    from scripts.evaluate_rag_retrieval import DEFAULT_EVAL_CASES

    retriever = RagRetriever(args.index_dir, strict_dense=True)
    safety_filter = RagSafetyFilter(load_products(root))
    results = [evaluate_case(case, retriever, safety_filter) for case in DEFAULT_EVAL_CASES]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": summarize(results),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "ragas_like_quality_report.json"
    markdown_path = args.output_dir / "ragas_like_quality_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(markdown_path, payload)
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(markdown_path),
                "quality_gate": payload["summary"]["quality_gate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["summary"]["quality_gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
