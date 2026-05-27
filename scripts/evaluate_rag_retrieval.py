from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EVAL_CASES: list[dict[str, Any]] = [
    {
        "id": "metabolic_ir",
        "topic": "代谢",
        "query": "代谢综合征 胰岛素抵抗 血糖管理",
        "expected_any": ["代谢综合征", "胰岛素抵抗", "血糖", "代谢"],
    },
    {
        "id": "metabolic_lipids",
        "topic": "代谢",
        "query": "肥胖 腰围 甘油三酯 HDL 代谢风险",
        "expected_any": ["肥胖", "腰围", "甘油三酯", "HDL", "代谢"],
    },
    {
        "id": "thyroid_hypothyroid",
        "topic": "甲状腺",
        "query": "甲状腺功能减退 桥本 TSH T3 T4",
        "expected_any": ["甲状腺", "TSH", "T3", "T4", "桥本"],
    },
    {
        "id": "thyroid_antibody",
        "topic": "甲状腺",
        "query": "桥本甲状腺炎 TPOAb 抗体 疲劳",
        "expected_any": ["桥本", "甲状腺", "TPO", "抗体", "疲劳"],
    },
    {
        "id": "gut_barrier",
        "topic": "肠道",
        "query": "肠道通透性 肠漏 菌群 免疫",
        "expected_any": ["肠道通透性", "肠漏", "菌群", "免疫", "肠道"],
    },
    {
        "id": "gut_dysbiosis",
        "topic": "肠道",
        "query": "肠道菌群 益生菌 消化吸收 腹胀",
        "expected_any": ["肠道", "菌群", "益生菌", "消化", "腹胀"],
    },
    {
        "id": "inflammation_crp",
        "topic": "炎症",
        "query": "慢性炎症 CRP 氧化应激",
        "expected_any": ["慢性炎症", "炎症", "CRP", "氧化应激"],
    },
    {
        "id": "oxidative_stress",
        "topic": "炎症",
        "query": "氧化应激 自由基 抗氧化 炎症",
        "expected_any": ["氧化应激", "自由基", "抗氧化", "炎症"],
    },
    {
        "id": "sleep_fatigue",
        "topic": "睡眠/疲劳",
        "query": "睡眠不足 慢性疲劳 压力 HPA轴",
        "expected_any": ["睡眠", "疲劳", "压力", "HPA", "皮质醇"],
    },
    {
        "id": "hpa_cortisol",
        "topic": "睡眠/疲劳",
        "query": "HPA轴 压力 皮质醇 慢性疲劳",
        "expected_any": ["HPA", "压力", "皮质醇", "疲劳"],
    },
    {
        "id": "immune_allergy",
        "topic": "免疫",
        "query": "免疫 过敏 IgE 炎症",
        "expected_any": ["免疫", "过敏", "IgE", "炎症"],
    },
    {
        "id": "detox_liver",
        "topic": "解毒",
        "query": "解毒 肝脏 毒素 重金属",
        "expected_any": ["解毒", "肝", "毒素", "重金属"],
    },
    {
        "id": "hormone_estrogen",
        "topic": "激素",
        "query": "雌激素 孕酮 激素平衡 PMS",
        "expected_any": ["雌激素", "孕酮", "激素", "PMS"],
    },
    {
        "id": "nutrition_micronutrients",
        "topic": "营养",
        "query": "维生素 矿物质 营养缺乏",
        "expected_any": ["维生素", "矿物质", "营养", "缺乏"],
    },
    {
        "id": "lifestyle_exercise",
        "topic": "生活方式",
        "query": "运动 睡眠 压力管理 生活方式",
        "expected_any": ["运动", "睡眠", "压力", "生活方式"],
    },
    {
        "id": "colloquial_fatigue",
        "topic": "睡眠/疲劳",
        "query": "总是很累睡不醒压力大",
        "expected_any": ["疲劳", "睡眠", "压力", "HPA"],
    },
    {
        "id": "mixed_english_thyroid",
        "topic": "甲状腺",
        "query": "Hashimoto TSH T3 T4 fatigue",
        "expected_any": ["Hashimoto", "甲状腺", "TSH", "T3", "T4", "疲劳"],
    },
    {
        "id": "lab_homa_ir",
        "topic": "代谢",
        "query": "HOMA-IR 空腹胰岛素 血糖",
        "expected_any": ["HOMA", "胰岛素", "血糖", "抵抗"],
    },
    {
        "id": "out_of_scope_antibiotic",
        "topic": "边界",
        "query": "感冒发烧 抗生素 剂量",
        "expected_any": [],
        "case_type": "boundary",
    },
    {
        "id": "drug_risk_antifungal",
        "topic": "边界",
        "query": "氟康唑 伊曲康唑 真菌感染 剂量",
        "expected_any": [],
        "case_type": "risk",
    },
]

RISK_TERMS = [
    "氟康唑",
    "伊曲康唑",
    "制霉菌素",
    "特比萘芬",
    "抗生素",
    "华法林",
    "二甲双胍",
    "胰岛素注射",
    "处方药",
    "剂量",
    "用药",
    "停药",
    "禁忌",
]

HIGH_RISK_DRUG_TERMS = [
    "氟康唑",
    "伊曲康唑",
    "制霉菌素",
    "特比萘芬",
    "抗生素",
    "华法林",
    "二甲双胍",
    "胰岛素注射",
]

ACTIONABLE_DRUG_PATTERNS = [
    re.compile(r"(推荐|建议|可考虑|应当|需要).{0,20}(服用|使用|口服|给药|加用).{0,20}(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射)"),
    re.compile(r"(氟康唑|伊曲康唑|制霉菌素|特比萘芬|抗生素|华法林|二甲双胍|胰岛素注射).{0,20}(剂量|mg|毫克|每日|每天|口服|疗程|停药|加量|减量)"),
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compact_text(text: str, max_chars: int = 360) -> str:
    compacted = " ".join((text or "").split())
    return compacted if len(compacted) <= max_chars else f"{compacted[:max_chars]}..."


def terms_found(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def actionable_drug_patterns_found(text: str) -> list[str]:
    return [pattern.pattern for pattern in ACTIONABLE_DRUG_PATTERNS if pattern.search(text)]


def first_expected_rank(hits: list[Any], expected_terms: list[str]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if terms_found(hit.text, expected_terms):
            return rank
    return None


def hit_to_dict(hit: Any) -> dict[str, Any]:
    metadata = hit.metadata or {}
    return {
        "chunk_id": hit.chunk_id,
        "score": hit.score,
        "dense_score": hit.dense_score,
        "sparse_score": hit.sparse_score,
        "source_kind": hit.source_kind,
        "source_title": metadata.get("source_title"),
        "section": metadata.get("section"),
        "topic_tags": hit.topic_tags,
        "needs_review": hit.needs_review,
        "preview": compact_text(hit.text),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    positive_results = [result for result in results if result["expected_any"]]
    top3_hits = [result for result in positive_results if result["expected_rank"] is not None and result["expected_rank"] <= 3]
    reciprocal_ranks = [
        1 / result["expected_rank"] if result["expected_rank"] is not None else 0.0
        for result in positive_results
    ]
    dense_available = [result for result in results if result["dense_candidate_count"] > 0 and result["max_dense_score"] > 0]
    positive_actionable_drug = [
        result
        for result in positive_results
        if result["actionable_drug_patterns_found"] and result.get("case_type") not in {"risk", "boundary"}
    ]
    sparse_scores = [
        result["max_sparse_score"]
        for result in positive_results
        if result["max_sparse_score"] is not None and result["max_sparse_score"] > 0
    ]
    topic_counter = Counter(result["topic"] for result in results)

    recall_at_3 = len(top3_hits) / len(positive_results) if positive_results else 0.0
    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
    quality_gate = {
        "dense_available_for_all_queries": len(dense_available) == len(results),
        "positive_recall_at_3": round(recall_at_3, 4),
        "minimum_positive_recall_at_3": 0.8,
        "positive_mrr": round(mrr, 4),
        "no_actionable_drug_recommendation_in_positive_top3": not positive_actionable_drug,
    }
    quality_gate["passed"] = (
        quality_gate["dense_available_for_all_queries"]
        and recall_at_3 >= quality_gate["minimum_positive_recall_at_3"]
        and quality_gate["no_actionable_drug_recommendation_in_positive_top3"]
    )

    return {
        "case_count": len(results),
        "positive_case_count": len(positive_results),
        "topic_distribution": dict(topic_counter),
        "dense_available_count": len(dense_available),
        "positive_top3_hit_count": len(top3_hits),
        "positive_recall_at_3": round(recall_at_3, 4),
        "positive_mrr": round(mrr, 4),
        "positive_sparse_score_min": round(min(sparse_scores), 6) if sparse_scores else None,
        "positive_sparse_score_median": round(statistics.median(sparse_scores), 6) if sparse_scores else None,
        "positive_sparse_score_max": round(max(sparse_scores), 6) if sparse_scores else None,
        "positive_actionable_drug_cases": [result["id"] for result in positive_actionable_drug],
        "quality_gate": quality_gate,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    gate = summary["quality_gate"]
    lines = [
        "# RAG Retrieval Quality Review",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- index_dir: `{payload['index_dir']}`",
        f"- model_name: `{payload.get('manifest', {}).get('model_name')}`",
        f"- embedding_backend: `{payload.get('manifest', {}).get('embedding_backend')}`",
        f"- document_count: `{payload.get('manifest', {}).get('document_count')}`",
        f"- fixed_eval_cases: `{summary['case_count']}`",
        f"- positive_recall_at_3: `{summary['positive_recall_at_3']}`",
        f"- positive_mrr: `{summary['positive_mrr']}`",
        f"- dense_available_count: `{summary['dense_available_count']}/{summary['case_count']}`",
        f"- quality_gate_passed: `{gate['passed']}`",
        "",
        "Notes:",
        "- The query set is fixed in `scripts/evaluate_rag_retrieval.py`; results below are not post-filtered or hand-picked.",
        "- `dense_score` is the raw inner-product/cosine score from the normalized bge-m3 vector index.",
        "- `sparse_score` is the raw BM25 score from `rank_bm25`; it is not normalized and has no universal 0.2 quality threshold.",
        "- Customer-facing reports must not expose source titles, sections, chunk IDs, or page information.",
        "",
        "## Quality Gate",
        "",
    ]
    for key, value in gate.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Query Results", ""])
    for item in payload["queries"]:
        lines.extend(
            [
                f"### {item['id']} | {item['topic']}",
                "",
                f"- query: `{item['query']}`",
                f"- expected_any: `{', '.join(item['expected_any']) if item['expected_any'] else 'N/A'}`",
                f"- expected_rank: `{item['expected_rank']}`",
                f"- dense_candidate_count: `{item['dense_candidate_count']}`",
                f"- max_dense_score: `{item['max_dense_score']}`",
                f"- max_sparse_score: `{item['max_sparse_score']}`",
                f"- risk_terms_found: `{', '.join(item['risk_terms_found']) if item['risk_terms_found'] else 'none'}`",
                f"- actionable_drug_patterns_found: `{len(item['actionable_drug_patterns_found'])}`",
                "",
            ]
        )
        for idx, hit in enumerate(item["hits"], start=1):
            lines.extend(
                [
                    f"#### {idx}. {hit['source_kind']} | score={hit['score']}",
                    "",
                    f"- source_title: {hit.get('source_title') or 'unknown'}",
                    f"- section: {hit.get('section') or '未知章节'}",
                    f"- topic_tags: {', '.join(hit.get('topic_tags') or [])}",
                    f"- dense_score: `{hit['dense_score']}`",
                    f"- sparse_score: `{hit['sparse_score']}`",
                    "",
                    hit["preview"],
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Evaluate the persisted RAG index with a fixed, auditable query set.")
    parser.add_argument("--index-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_index")
    parser.add_argument("--output-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_staging")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--extra-site-packages", type=Path, default=None)
    parser.add_argument("--query", action="append", dest="queries", default=None)
    args = parser.parse_args()

    if args.extra_site_packages is not None:
        sys.path.insert(0, str(args.extra_site_packages))
    sys.path.insert(0, str(root / "backend"))

    from app.services.rag_retriever import RagRetriever

    manifest_path = args.index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    retriever = RagRetriever(args.index_dir, strict_dense=True, topic_boost_weight=0.0)
    cases = [
        {"id": f"ad_hoc_{index}", "topic": "ad_hoc", "query": query, "expected_any": []}
        for index, query in enumerate(args.queries or [], start=1)
    ] or DEFAULT_EVAL_CASES

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "index_dir": "backend/app/data/rag_index",
        "manifest": manifest,
        "queries": [],
    }
    pool_size = max(args.top_k * 8, 40)
    for case in cases:
        query = case["query"]
        debug = retriever.rank_debug(query, pool_size=pool_size)
        hits = retriever.hybrid_search(query, top_k=args.top_k)
        joined_top_hits = "\n".join(hit.text for hit in hits)
        expected_any = list(case.get("expected_any") or [])
        expected_rank = first_expected_rank(hits, expected_any) if expected_any else None
        dense_scores = [score for _index, score in debug["dense_ranked"]]
        sparse_scores = [score for _index, score in debug["sparse_ranked"]]
        risk_terms = terms_found(joined_top_hits, RISK_TERMS)
        high_risk_drug_terms = terms_found(joined_top_hits, HIGH_RISK_DRUG_TERMS)
        actionable_patterns = actionable_drug_patterns_found(joined_top_hits)
        payload["queries"].append(
            {
                "id": case["id"],
                "topic": case["topic"],
                "case_type": case.get("case_type", "positive" if expected_any else "boundary"),
                "query": query,
                "expected_any": expected_any,
                "expected_rank": expected_rank,
                "dense_candidate_count": len(debug["dense_ranked"]),
                "sparse_candidate_count": len(debug["sparse_ranked"]),
                "max_dense_score": round(max(dense_scores), 6) if dense_scores else 0.0,
                "max_sparse_score": round(max(sparse_scores), 6) if sparse_scores else 0.0,
                "risk_terms_found": risk_terms,
                "high_risk_drug_terms_found": high_risk_drug_terms,
                "actionable_drug_patterns_found": actionable_patterns,
                "hits": [hit_to_dict(hit) for hit in hits],
            }
        )

    payload["summary"] = summarize_results(payload["queries"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "rag_retrieval_quality_report.json"
    markdown_path = args.output_dir / "rag_retrieval_quality_report.md"
    examples_json_path = args.output_dir / "rag_retrieval_examples.json"
    examples_markdown_path = args.output_dir / "rag_retrieval_examples.md"
    json_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    json_path.write_text(json_text, encoding="utf-8")
    examples_json_path.write_text(json_text, encoding="utf-8")
    write_markdown(markdown_path, payload)
    write_markdown(examples_markdown_path, payload)
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
