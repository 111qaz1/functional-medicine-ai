from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

from docx import Document


TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "代谢": (
        "代谢",
        "血糖",
        "胰岛素",
        "肥胖",
        "体重",
        "血脂",
        "糖尿病",
        "能量",
        "线粒体",
        "metabolic",
        "insulin",
        "glucose",
    ),
    "甲状腺": ("甲状腺", "tsh", "t3", "t4", "桥本", "thyroid", "hashimoto"),
    "肠道": (
        "肠道",
        "肠漏",
        "胃肠",
        "消化",
        "菌群",
        "益生菌",
        "小肠",
        "galt",
        "gut",
        "intestinal",
        "microbiome",
    ),
    "炎症": ("炎症", "抗炎", "氧化应激", "自身免疫", "crp", "inflammation", "inflammatory"),
    "睡眠/疲劳": (
        "睡眠",
        "失眠",
        "疲劳",
        "肾上腺",
        "压力",
        "昼夜",
        "hpa",
        "sleep",
        "fatigue",
        "stress",
        "adrenal",
    ),
    "免疫": ("免疫", "抗体", "过敏", "th1", "th2", "iga", "immune", "allergy"),
    "解毒": ("解毒", "毒素", "重金属", "肝脏", "汞", "铅", "环境", "detox", "toxin", "mercury", "lead"),
    "激素": (
        "激素",
        "雌激素",
        "孕酮",
        "睾酮",
        "皮质醇",
        "月经",
        "pcos",
        "hormone",
        "estrogen",
        "progesterone",
        "cortisol",
    ),
    "营养": (
        "营养",
        "维生素",
        "矿物质",
        "膳食",
        "蛋白",
        "脂肪酸",
        "纤维",
        "nutrition",
        "vitamin",
        "mineral",
        "diet",
    ),
    "生活方式": ("生活方式", "运动", "饮食", "压力管理", "冥想", "放松", "锻炼", "lifestyle", "exercise", "meditation"),
}

NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ISBN",
        r"97[89][-\d\s]{10,}",
        r"CIP",
        r"版权所有",
        r"侵权必究",
        r"出版社",
        r"人民卫生出版社",
        r"出版发行",
        r"责任编辑",
        r"责任印制",
        r"印刷",
        r"版次",
        r"开本",
        r"字数",
        r"定价",
        r"书号",
        r"邮编",
        r"地址",
        r"网址",
        r"热线",
        r"E-mail",
        r"图书在版",
        r"中国版本图书馆",
        r"\bhttps?://",
        r"\bwww\.",
        r"\bWebMD\b",
        r"\bMedline\b",
        r"\bMedscape\b",
        r"\bConference listings\b",
        r"\bSelected Bibliography\b",
        r"\bBibliography\b",
        r"\bReferences\b",
        r"\bFurther Reading\b",
        r"\bSuggested Reading\b",
        r"^\s*第\s*\d+\s*页\s*$",
        r"^\s*[-–—_]{3,}\s*$",
    )
)

QUESTIONNAIRE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"健康问卷",
        r"调查问卷",
        r"MSQ",
        r"姓名[:：_]",
        r"性别[:：_]",
        r"年龄[:：_]",
        r"□",
    )
)

CASE_REPORT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"病例",
        r"主诉",
        r"现病史",
        r"既往史",
        r"个人史",
        r"家族史",
        r"体格检查",
        r"辅助检查",
        r"治疗经过",
        r"患者",
    )
)

DOCX_NAME_RE = re.compile(r"【已校验】(?P<range>\d+-\d+)页\s*(?P<title>.+?)\.docx$")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千万0-9]+[章节篇部]\s*")
LEVEL2_RE = re.compile(r"^[一二三四五六七八九十]+[\s　]*[、.．]\s*")
LEVEL3_RE = re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*")
NUMBERED_RE = re.compile(r"^(\d+)[.、]\s*")


@dataclass
class FilteredItem:
    source_kind: str
    source_id: str
    reason: str
    text_preview: str


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\r", "\n")
    value = re.sub(r"[\u200b-\u200f\ufeff]", "", value)
    value = re.sub(r"[ \t\u3000]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def strip_sensitive_metadata(value: str) -> str:
    value = re.sub(r"(?i)\bISBN\b", "", value)
    value = re.sub(r"97[89][-\d\s]{10,}", "", value)
    value = re.sub(r"[-_ ]{2,}", " ", value)
    value = re.sub(r"\s+([.)\]）])", r"\1", value)
    return value.strip(" -_")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def should_filter_text(text: str, *, min_chars: int) -> str | None:
    cleaned = compact_text(text)
    if len(cleaned) < min_chars:
        return "too_short"
    if any(pattern.search(cleaned) for pattern in NOISE_PATTERNS):
        return "publication_or_page_noise"
    page_pointer_hits = len(re.findall(r"\b\d{1,4}[–—-]\d{1,4}\b|\b\d{2,4}[ft]\b", cleaned))
    comma_density = cleaned.count(",") / max(len(cleaned), 1)
    if page_pointer_hits >= 4 and comma_density > 0.015:
        return "index_or_reference_noise"
    if re.search(r"\bet al\.?", cleaned, re.IGNORECASE) and re.search(r"\b(19|20)\d{2}\b", cleaned):
        return "bibliography_noise"
    if cleaned in {"功能医学概论", "续表", "表"}:
        return "structural_noise"
    if re.fullmatch(r"[\d\s.,，。:：;；()/（）-]+", cleaned):
        return "punctuation_or_number_only"
    return None


def looks_like_questionnaire(text: str, source_text: str = "") -> bool:
    combined = f"{source_text}\n{text}"
    hits = sum(1 for pattern in QUESTIONNAIRE_PATTERNS if pattern.search(combined))
    return hits >= 2


def looks_like_case_report(text: str, section: str = "") -> bool:
    combined = f"{section}\n{text}"
    if "病例" in section:
        return True
    hits = sum(1 for pattern in CASE_REPORT_PATTERNS if pattern.search(combined))
    has_age_or_demographic = bool(re.search(r"\d+\s*岁|男[，,]|女[，,]|性别", combined))
    return hits >= 3 and has_age_or_demographic


def infer_topic_tags(*texts: str) -> list[str]:
    merged = "\n".join(texts).lower()
    tags = [topic for topic, keywords in TOPIC_KEYWORDS.items() if any(keyword.lower() in merged for keyword in keywords)]
    return tags or ["未分类"]


def sanitize_source_label(value: str | None) -> str:
    if not value:
        return "unknown_source"
    value = str(value).replace("\\", "/")
    value = value.split("#", 1)[0]
    source = strip_sensitive_metadata(Path(value).stem or Path(value).name or "unknown_source")
    return source or "unknown_source"


def safe_section(value: str | None) -> str:
    cleaned = strip_sensitive_metadata(compact_text(value or ""))
    return cleaned if cleaned else "未知章节"


def stable_id(prefix: str, *parts: str) -> str:
    digest = sha1("\n".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def source_hash(text: str) -> str:
    return sha1(compact_text(text)[:200].encode("utf-8", errors="ignore")).hexdigest()


def make_corpus_entry(
    *,
    chunk_id: str,
    source_kind: str,
    source_doc_id: str,
    source_title: str,
    section: str,
    text: str,
    review_status: str,
    evidence_level: str,
    topic_tags: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "source_doc_id": source_doc_id,
        "source_title": source_title,
        "section": safe_section(section),
    }
    if extra:
        metadata.update(extra)
    return {
        "chunk_id": chunk_id,
        "source_kind": source_kind,
        "review_status": review_status,
        "evidence_level": evidence_level,
        "topic_tags": topic_tags,
        "text": normalize_text(text),
        "needs_review": False,
        "metadata": metadata,
    }


def clean_reference_only(items: list[dict[str, Any]], min_chars: int) -> tuple[list[dict[str, Any]], list[FilteredItem]]:
    cleaned: list[dict[str, Any]] = []
    filtered: list[FilteredItem] = []

    for item in items:
        statement_id = str(item.get("statement_id") or stable_id("reference", item.get("normalized_text", "")))
        text = normalize_text(str(item.get("normalized_text") or ""))
        source_doc = sanitize_source_label(item.get("source_doc_id") or item.get("source_path"))
        reason = should_filter_text(text, min_chars=min_chars)
        if reason is None and looks_like_questionnaire(text, str(item.get("source_path") or item.get("topic") or "")):
            reason = "questionnaire_or_form"
        if reason is None and looks_like_case_report(text, str(item.get("topic") or "")):
            reason = "case_report_like"
        if reason:
            filtered.append(FilteredItem("reference_only", statement_id, reason, compact_text(text)[:240]))
            continue

        section = safe_section(str(item.get("topic") or "未知章节"))
        topic_tags = infer_topic_tags(section, text, " ".join(str(tag) for tag in item.get("topic_tags") or []))
        cleaned.append(
            make_corpus_entry(
                chunk_id=stable_id("ref", statement_id, text),
                source_kind="reference_only",
                source_doc_id=stable_id("source", source_doc),
                source_title=source_doc,
                section=section,
                text=text,
                review_status="reference_only",
                evidence_level=str(item.get("evidence_level") or "local_reference_material"),
                topic_tags=topic_tags,
                extra={
                    "legacy_statement_id": statement_id,
                    "legacy_source_type": str(item.get("source_type") or ""),
                },
            )
        )

    return cleaned, filtered


def convert_reviewed(items: list[dict[str, Any]], min_chars: int) -> tuple[list[dict[str, Any]], list[FilteredItem]]:
    converted: list[dict[str, Any]] = []
    filtered: list[FilteredItem] = []
    for item in items:
        statement_id = str(item.get("statement_id") or stable_id("reviewed", item.get("normalized_text", "")))
        text = normalize_text(str(item.get("normalized_text") or ""))
        reason = should_filter_text(text, min_chars=min_chars)
        if reason:
            filtered.append(FilteredItem("reviewed", statement_id, reason, compact_text(text)[:240]))
            continue
        source_doc = sanitize_source_label(item.get("source_doc_id") or item.get("source_path"))
        section = safe_section(str(item.get("topic") or "已审核知识"))
        topic_tags = infer_topic_tags(section, text)
        converted.append(
            make_corpus_entry(
                chunk_id=stable_id("reviewed", statement_id, text),
                source_kind="reviewed_knowledge",
                source_doc_id=stable_id("source", source_doc),
                source_title=source_doc,
                section=section,
                text=text,
                review_status="reviewed",
                evidence_level=str(item.get("evidence_level") or "internal_reviewed"),
                topic_tags=topic_tags,
                extra={
                    "legacy_statement_id": statement_id,
                    "related_markers": item.get("related_markers") or [],
                    "related_skus": item.get("related_skus") or [],
                    "contraindications": item.get("contraindications") or [],
                    "legacy_topic_tags": item.get("topic_tags") or [],
                },
            )
        )
    return converted, filtered


def docx_source_info(path: Path, index: int) -> tuple[str, str, str]:
    match = DOCX_NAME_RE.match(path.name)
    if not match:
        return f"functional_medicine_intro_part_{index:02d}", path.stem, f"part_{index:02d}"
    title = match.group("title")
    return f"functional_medicine_intro_part_{index:02d}", title, f"part_{index:02d}"


def paragraph_level(text: str, style_name: str) -> int | None:
    style_lower = style_name.lower()
    if "heading 1" in style_lower or "标题 1" in style_lower:
        return 1
    if "heading 2" in style_lower or "标题 2" in style_lower:
        return 2
    if "heading 3" in style_lower or "标题 3" in style_lower:
        return 3
    if CHAPTER_RE.match(text):
        return 1
    if len(text) <= 42 and LEVEL2_RE.match(text):
        return 2
    if len(text) <= 48 and LEVEL3_RE.match(text):
        return 3
    if len(text) <= 42 and NUMBERED_RE.match(text) and not re.search(r"[。；;，,]", text):
        return 3
    return None


def markdown_lines_from_docx(path: Path) -> list[tuple[int | None, str]]:
    document = Document(str(path))
    lines: list[tuple[int | None, str]] = []
    for paragraph in document.paragraphs:
        text = compact_text(paragraph.text or "")
        if not text:
            continue
        if should_filter_text(text, min_chars=2):
            continue
        if re.fullmatch(r"第[一二三四五六七八九十百千万0-9]+[章节篇部]", text):
            continue
        style_name = paragraph.style.name if paragraph.style else ""
        level = paragraph_level(text, style_name)
        lines.append((level, text))
    return lines


def split_long_text(text: str, max_chars: int) -> list[str]:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_len + len(sentence) > max_chars:
            pieces.append("".join(current).strip())
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence)
    if current:
        pieces.append("".join(current).strip())
    return [piece for piece in pieces if piece]


def flush_docx_chunk(
    chunks: list[dict[str, Any]],
    *,
    source_doc_id: str,
    source_title: str,
    part_label: str,
    section_stack: dict[int, str],
    body: list[str],
    chunk_index: int,
    max_chars: int,
    min_chars: int,
    filtered: list[FilteredItem],
) -> int:
    text = normalize_text("\n".join(body))
    section = " > ".join(section_stack[level] for level in sorted(section_stack) if level <= 2 and section_stack[level])
    section = section or "未知章节"
    reason = should_filter_text(text, min_chars=min_chars)
    if reason is None and looks_like_case_report(text, section):
        reason = "case_report_like"
    if reason:
        filtered.append(FilteredItem("docx_textbook", f"{source_doc_id}:{chunk_index}", reason, compact_text(text)[:240]))
        return chunk_index

    for sub_index, piece in enumerate(split_long_text(text, max_chars), start=1):
        reason = should_filter_text(piece, min_chars=min_chars)
        if reason:
            filtered.append(
                FilteredItem("docx_textbook", f"{source_doc_id}:{chunk_index}:{sub_index}", reason, compact_text(piece)[:240])
            )
            continue
        if looks_like_case_report(piece, section):
            filtered.append(
                FilteredItem("docx_textbook", f"{source_doc_id}:{chunk_index}:{sub_index}", "case_report_like", compact_text(piece)[:240])
            )
            continue
        chunk_index += 1
        chunks.append(
            make_corpus_entry(
                chunk_id=stable_id("docx", source_doc_id, section, str(chunk_index), piece),
                source_kind="docx_textbook",
                source_doc_id=source_doc_id,
                source_title=source_title,
                section=section,
                text=piece,
                review_status="reference_only",
                evidence_level="local_textbook_docx",
                topic_tags=infer_topic_tags(section, piece),
                extra={"part_label": part_label, "section_level": "heading_2"},
            )
        )
    return chunk_index


def parse_docx_files(paths: list[Path], min_chars: int, max_chars: int) -> tuple[list[dict[str, Any]], list[FilteredItem], dict[str, int]]:
    chunks: list[dict[str, Any]] = []
    filtered: list[FilteredItem] = []
    per_file_counts: dict[str, int] = {}

    for index, path in enumerate(paths, start=1):
        source_doc_id, source_title, part_label = docx_source_info(path, index)
        lines = markdown_lines_from_docx(path)
        section_stack: dict[int, str] = {}
        body: list[str] = []
        chunk_index = 0
        skip_until_first_body_heading = index == 1

        for level, text in lines:
            if skip_until_first_body_heading:
                if level is None:
                    continue
                if level <= 2:
                    skip_until_first_body_heading = False
                else:
                    continue
            if level is not None:
                if level <= 2 and body:
                    chunk_index = flush_docx_chunk(
                        chunks,
                        source_doc_id=source_doc_id,
                        source_title=source_title,
                        part_label=part_label,
                        section_stack=section_stack,
                        body=body,
                        chunk_index=chunk_index,
                        max_chars=max_chars,
                        min_chars=min_chars,
                        filtered=filtered,
                    )
                    body = []
                section_stack[level] = text
                for existing_level in list(section_stack):
                    if existing_level > level:
                        section_stack.pop(existing_level, None)
                body.append(f"{'#' * min(level, 6)} {text}")
            else:
                body.append(text)

        if body:
            chunk_index = flush_docx_chunk(
                chunks,
                source_doc_id=source_doc_id,
                source_title=source_title,
                part_label=part_label,
                section_stack=section_stack,
                body=body,
                chunk_index=chunk_index,
                max_chars=max_chars,
                min_chars=min_chars,
                filtered=filtered,
            )
        per_file_counts[path.name] = chunk_index

    return chunks, filtered, per_file_counts


def deduplicate(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    priority = {"reviewed_knowledge": 0, "docx_textbook": 1, "reference_only": 2}

    for entry in entries:
        key = source_hash(entry["text"])
        current = seen.get(key)
        if current is None:
            seen[key] = entry
            continue
        if priority.get(entry["source_kind"], 99) < priority.get(current["source_kind"], 99):
            duplicates.append({"kept": entry["chunk_id"], "dropped": current["chunk_id"], "hash": key})
            seen[key] = entry
        else:
            duplicates.append({"kept": current["chunk_id"], "dropped": entry["chunk_id"], "hash": key})

    return list(seen.values()), duplicates


def validate_jsonl(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {line_number} is invalid JSON: {exc}") from exc
            count += 1
    return count


def sensitive_scan(paths: Iterable[Path], root: Path) -> dict[str, list[str]]:
    patterns = {
        "isbn_or_copyright": re.compile(r"ISBN|97[89][-\d\s]{10,}|版权所有|侵权必究|CIP", re.IGNORECASE),
        "local_absolute_path": re.compile(
            r"[A-Za-z]:[\\/](?:Users|RAG|medical|Desktop|Windows|Program Files|ProgramData|Temp|tmp)[^\n\"']*",
            re.IGNORECASE,
        ),
        "api_key_like": re.compile(r"(?i)(api[_-]?key|secret[_-]?key|sk-[A-Za-z0-9]{20,})"),
    }
    findings: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        try:
            display_path = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            display_path = path.name
        for name, pattern in patterns.items():
            for match in pattern.finditer(text):
                findings[name].append(f"{display_path}:{match.start()}")
                if len(findings[name]) >= 20:
                    break
    return dict(findings)


def build_review_sample(entries: list[dict[str, Any]], sample_size: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        by_kind[entry["source_kind"]].append(entry)

    sample: list[dict[str, Any]] = []
    kinds = sorted(by_kind)
    per_kind = max(1, sample_size // max(1, len(kinds)))
    for kind in kinds:
        pool = by_kind[kind]
        sample.extend(rng.sample(pool, min(per_kind, len(pool))))
    remaining = [entry for entry in entries if entry not in sample]
    if len(sample) < sample_size and remaining:
        sample.extend(rng.sample(remaining, min(sample_size - len(sample), len(remaining))))
    rng.shuffle(sample)
    return sample[:sample_size]


def write_review_sample_markdown(path: Path, sample: list[dict[str, Any]]) -> None:
    lines = [
        "# RAG Corpus Review Sample",
        "",
        "This file contains deterministic random samples for human review. Source/page details are intentionally limited to internal-safe metadata.",
        "",
    ]
    for index, entry in enumerate(sample, start=1):
        metadata = entry.get("metadata") or {}
        preview = compact_text(entry.get("text", ""))
        if len(preview) > 420:
            preview = f"{preview[:420]}..."
        lines.extend(
            [
                f"## {index}. {entry.get('source_kind')} / {entry.get('review_status')}",
                "",
                f"- chunk_id: `{entry.get('chunk_id')}`",
                f"- section: {metadata.get('section', '未知章节')}",
                f"- topic_tags: {', '.join(entry.get('topic_tags') or [])}",
                "",
                preview,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build the RAG corpus from reviewed knowledge, reference-only text, and DOCX textbooks.")
    parser.add_argument("--knowledge-file", type=Path, default=root / "backend" / "app" / "data" / "knowledge_statements.json")
    parser.add_argument("--docx-dir", type=Path, required=True, help="Directory containing source DOCX files. The path is not written to outputs.")
    parser.add_argument("--output-dir", type=Path, default=root / "backend" / "app" / "data")
    parser.add_argument("--staging-dir", type=Path, default=root / "backend" / "app" / "data" / "rag_staging")
    parser.add_argument("--min-reference-chars", type=int, default=40)
    parser.add_argument("--min-docx-chars", type=int, default=80)
    parser.add_argument("--max-docx-chars", type=int, default=1200)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--sample-seed", type=int, default=20260526)
    args = parser.parse_args()

    docx_paths = sorted(args.docx_dir.glob("*.docx"))
    if not docx_paths:
        raise FileNotFoundError(f"No DOCX files found in {args.docx_dir}")

    raw_items = read_json(args.knowledge_file)
    if not isinstance(raw_items, list):
        raise TypeError("knowledge_statements.json must contain a list of statements")

    reviewed_items = [item for item in raw_items if item.get("review_status") == "reviewed"]
    reference_items = [item for item in raw_items if item.get("review_status") == "reference_only"]

    reviewed_entries, reviewed_filtered = convert_reviewed(reviewed_items, min_chars=args.min_reference_chars)
    reference_entries, reference_filtered = clean_reference_only(reference_items, min_chars=args.min_reference_chars)
    docx_entries, docx_filtered, docx_counts = parse_docx_files(docx_paths, min_chars=args.min_docx_chars, max_chars=args.max_docx_chars)

    merged_before_dedupe = reviewed_entries + reference_entries + docx_entries
    corpus_entries, duplicates = deduplicate(merged_before_dedupe)
    corpus_entries.sort(key=lambda row: (row["source_kind"], row["metadata"]["source_doc_id"], row["chunk_id"]))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.staging_dir.mkdir(parents=True, exist_ok=True)
    cleaned_reference_path = args.staging_dir / "cleaned_reference_only.jsonl"
    docx_chunks_path = args.staging_dir / "docx_chunks.jsonl"
    filtered_path = args.staging_dir / "filtered_items.jsonl"
    duplicates_path = args.staging_dir / "deduped_items.jsonl"
    sample_path = args.staging_dir / "rag_review_sample.jsonl"
    sample_markdown_path = args.staging_dir / "rag_review_sample.md"
    corpus_path = args.output_dir / "rag_corpus.jsonl"
    report_path = args.output_dir / "rag_import_report.json"

    review_sample = build_review_sample(corpus_entries, args.sample_size, args.sample_seed)
    all_filtered = reviewed_filtered + reference_filtered + docx_filtered
    write_jsonl(cleaned_reference_path, reference_entries)
    write_jsonl(docx_chunks_path, docx_entries)
    write_jsonl(filtered_path, [item.__dict__ for item in all_filtered])
    write_jsonl(duplicates_path, duplicates)
    write_jsonl(corpus_path, corpus_entries)
    write_jsonl(sample_path, review_sample)
    write_review_sample_markdown(sample_markdown_path, review_sample)

    corpus_count = validate_jsonl(corpus_path)
    validate_jsonl(cleaned_reference_path)
    validate_jsonl(docx_chunks_path)
    validate_jsonl(filtered_path)
    validate_jsonl(sample_path)

    topic_distribution = Counter(topic for row in corpus_entries for topic in row.get("topic_tags", []))
    source_distribution = Counter(row["source_kind"] for row in corpus_entries)
    filtered_distribution = Counter(item.reason for item in all_filtered)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_counts": {
            "knowledge_total": len(raw_items),
            "reviewed": len(reviewed_items),
            "reference_only": len(reference_items),
            "docx_files": len(docx_paths),
        },
        "output_counts": {
            "rag_corpus": corpus_count,
            "reviewed_knowledge": source_distribution.get("reviewed_knowledge", 0),
            "cleaned_reference_only": source_distribution.get("reference_only", 0),
            "docx_chunks": source_distribution.get("docx_textbook", 0),
        },
        "intermediate_counts": {
            "reviewed_after_cleaning": len(reviewed_entries),
            "reference_only_after_cleaning": len(reference_entries),
            "docx_chunks_before_dedupe": len(docx_entries),
            "filtered_total": len(all_filtered),
            "deduplicated_total": len(duplicates),
        },
        "filtered_by_reason": dict(filtered_distribution),
        "topic_distribution": dict(topic_distribution.most_common()),
        "docx_chunk_counts_by_file": docx_counts,
        "artifact_paths": {
            "rag_corpus": "backend/app/data/rag_corpus.jsonl",
            "rag_import_report": "backend/app/data/rag_import_report.json",
            "cleaned_reference_only": "backend/app/data/rag_staging/cleaned_reference_only.jsonl",
            "docx_chunks": "backend/app/data/rag_staging/docx_chunks.jsonl",
            "filtered_items": "backend/app/data/rag_staging/filtered_items.jsonl",
            "deduped_items": "backend/app/data/rag_staging/deduped_items.jsonl",
            "review_sample": "backend/app/data/rag_staging/rag_review_sample.jsonl",
            "review_sample_markdown": "backend/app/data/rag_staging/rag_review_sample.md",
        },
        "safety_notes": {
            "raw_docx_committed": False,
            "absolute_source_paths_written": False,
            "source_and_page_hidden_from_report_generation": True,
            "reviewed_knowledge_priority_on_duplicate": True,
        },
    }
    report["sensitive_scan_findings"] = sensitive_scan(
        [corpus_path, cleaned_reference_path, docx_chunks_path, sample_path, sample_markdown_path], root
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
