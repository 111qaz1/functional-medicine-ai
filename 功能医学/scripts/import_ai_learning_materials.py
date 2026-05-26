from __future__ import annotations

import json
import re
import zipfile
from hashlib import sha1
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from pypdf import PdfReader


W_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
X_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def discover_source_root(root: Path) -> Path:
    direct = root / "AI学习功能医学相关资料"
    if direct.exists():
        return direct

    for candidate in root.iterdir():
        if candidate.is_dir() and "AI" in candidate.name and "功能医学" in candidate.name:
            return candidate
    raise FileNotFoundError("Cannot find AI learning material directory under project root")


def data_paths(root: Path) -> tuple[Path, Path, Path]:
    data_dir = root / "backend" / "app" / "data"
    return (
        data_dir / "knowledge_statements.json",
        data_dir / "knowledge_import_ai_learning_report.json",
        data_dir / "knowledge_import_ai_learning_failures.json",
    )


def normalize_whitespace(value: str) -> str:
    value = value.replace("\x00", " ")
    value = value.replace("\r", "\n")
    value = re.sub(r"[\u200b-\u200f\ufeff]", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_line(value: str) -> str:
    value = normalize_whitespace(value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    if value in {"-", "--", "---", "•", "·"}:
        return ""
    if re.fullmatch(r"[0-9]+", value):
        return ""
    return value


def split_text_chunks(text: str, *, max_chars: int = 680) -> list[str]:
    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", normalize_whitespace(text)) if block.strip()]
    lines: list[str] = []
    for block in raw_blocks:
        block_lines = [clean_line(line) for line in block.splitlines()]
        for line in block_lines:
            if line:
                lines.append(line)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current and current_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))

    return [chunk for chunk in chunks if len(chunk) >= 40]


def infer_tags(relative_path: Path) -> list[str]:
    tags: list[str] = []
    for part in list(relative_path.parts[:-1]) + [relative_path.stem]:
        cleaned = re.sub(r"[_\-]+", " ", part).strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags[:8]


def determine_review_status(relative_path: Path) -> str:
    path_text = relative_path.as_posix().lower()
    if any(token in path_text for token in ("调查问卷", "textbook", "laboratory evaluations", "概论", "基础教材")):
        return "reference_only"
    return "reviewed"


def determine_evidence_level(relative_path: Path) -> str:
    suffix = relative_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return "local_structured_reference"
    if suffix == ".docx":
        return "local_protocol_document"
    if suffix == ".pdf":
        return "local_book_reference"
    return "local_reference_material"


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", W_NS):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", W_NS)]
        merged = clean_line("".join(texts))
        if merged:
            paragraphs.append(merged)
    return "\n\n".join(paragraphs)


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = normalize_whitespace(page.extract_text() or "")
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return max(index - 1, 0)


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", X_NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", X_NS))
        values.append(clean_line(text))
    return values


def workbook_sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {}
    for rel in rel_root.findall("pkg:Relationship", X_NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rel_id and target:
            if not target.startswith("xl/"):
                target = f"xl/{target.lstrip('/')}"
            rel_map[rel_id] = target

    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall("main:sheets/main:sheet", X_NS):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rel_id or "")
        if target:
            sheets.append((name, target))
    return sheets


def parse_sheet_rows(archive: zipfile.ZipFile, target: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(target))
    rows: list[list[str]] = []
    for row in root.findall(".//main:sheetData/main:row", X_NS):
        values_by_column: dict[int, str] = {}
        for cell in row.findall("main:c", X_NS):
            ref = cell.attrib.get("r", "A1")
            idx = column_index(ref)
            cell_type = cell.attrib.get("t")
            value = ""
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//main:t", X_NS))
            else:
                raw = cell.findtext("main:v", default="", namespaces=X_NS)
                if cell_type == "s":
                    try:
                        value = shared_strings[int(raw)]
                    except (ValueError, IndexError):
                        value = raw
                else:
                    value = raw
            value = clean_line(value)
            if value:
                values_by_column[idx] = value
        if not values_by_column:
            continue
        width = max(values_by_column) + 1
        rows.append([values_by_column.get(i, "") for i in range(width)])
    return rows


def extract_xlsx_rows(path: Path) -> list[tuple[str, list[list[str]]]]:
    sheets: list[tuple[str, list[list[str]]]] = []
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        for sheet_name, target in workbook_sheet_targets(archive):
            rows = parse_sheet_rows(archive, target, shared_strings)
            if rows:
                sheets.append((sheet_name, rows))
    return sheets


def row_to_statement_text(sheet_name: str, header: list[str], row: list[str]) -> str:
    pairs: list[str] = []
    for index, value in enumerate(row):
        if not value:
            continue
        label = header[index] if index < len(header) and header[index] else f"字段{index + 1}"
        if label == value:
            continue
        pairs.append(f"{label}: {value}")
    if len(pairs) >= 2:
        return f"工作表 {sheet_name}；" + "；".join(pairs)
    compact = [value for value in row if value]
    return f"工作表 {sheet_name}；" + "；".join(compact[:10])


def build_xlsx_chunks(path: Path) -> list[str]:
    chunks: list[str] = []
    for sheet_name, rows in extract_xlsx_rows(path):
        meaningful = [[cell for cell in row if cell] for row in rows if any(cell for cell in row)]
        if not meaningful:
            continue
        header = rows[0]
        body_rows = rows[1:] if len(rows) > 1 else rows
        for row in body_rows:
            text = row_to_statement_text(sheet_name, header, row)
            if len(text) >= 25:
                chunks.append(text[:900])
    return chunks


def make_statement(relative_path: Path, index: int, text: str) -> dict[str, object]:
    identifier = sha1(f"{relative_path.as_posix()}::{index}".encode("utf-8")).hexdigest()[:16]
    tags = infer_tags(relative_path)
    topic = tags[-1] if tags else relative_path.stem
    return {
        "statement_id": f"ailearn_{identifier}",
        "topic": topic,
        "normalized_text": text,
        "evidence_level": determine_evidence_level(relative_path),
        "source_doc_id": relative_path.as_posix(),
        "source_path": relative_path.as_posix(),
        "source_type": {
            ".pdf": "pdf",
            ".docx": "document",
            ".xlsx": "spreadsheet",
            ".xls": "spreadsheet",
        }.get(relative_path.suffix.lower(), "local_text"),
        "review_status": determine_review_status(relative_path),
        "reviewed_by": "local-ai-learning-import",
        "version": "2026-04-ai-learning-v1",
        "tags": tags,
        "topic_tags": tags[:4],
        "related_markers": [],
        "related_goals": [],
        "related_skus": [],
        "lifestyle_actions": [],
        "contraindications": [],
    }


def validate_statement_shape(items: list[dict[str, object]], root: Path) -> None:
    import sys

    sys.path.insert(0, str(root / "backend"))
    from app.domain.models import KnowledgeStatement  # type: ignore

    for item in items:
        KnowledgeStatement.model_validate(item)


def import_materials() -> dict[str, object]:
    root = project_root()
    source_root = discover_source_root(root)
    knowledge_path, report_path, failure_path = data_paths(root)

    existing = json.loads(knowledge_path.read_text(encoding="utf-8-sig"))
    retained = [
        item
        for item in existing
        if not str(item.get("source_path", "")).startswith(f"{source_root.name}/")
    ]

    imported_items: list[dict[str, object]] = []
    imported_files: list[dict[str, object]] = []
    skipped_files: list[dict[str, object]] = []
    failed_files: list[dict[str, object]] = []

    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        relative_path = path.relative_to(root)
        suffix = path.suffix.lower()
        try:
            if suffix == ".docx":
                text = extract_docx_text(path)
                chunks = split_text_chunks(text)
                if not chunks:
                    failed_files.append({"path": relative_path.as_posix(), "reason": "docx_no_text"})
                    continue
            elif suffix in {".xlsx", ".xls"}:
                chunks = build_xlsx_chunks(path)
                if not chunks:
                    failed_files.append({"path": relative_path.as_posix(), "reason": "spreadsheet_no_rows"})
                    continue
            elif suffix == ".pdf":
                text = extract_pdf_text(path)
                chunks = split_text_chunks(text)
                if not chunks:
                    skipped_files.append({
                        "path": relative_path.as_posix(),
                        "reason": "pdf_unreadable_without_ocr",
                        "note": "按要求跳过未能直接抽取文字的书籍/参考 PDF",
                    })
                    continue
            else:
                skipped_files.append({"path": relative_path.as_posix(), "reason": "unsupported_suffix"})
                continue

            file_items = [make_statement(relative_path, index, chunk) for index, chunk in enumerate(chunks, start=1)]
            imported_items.extend(file_items)
            imported_files.append(
                {
                    "path": relative_path.as_posix(),
                    "statement_count": len(file_items),
                    "review_status": determine_review_status(relative_path),
                    "source_type": file_items[0]["source_type"] if file_items else "unknown",
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed_files.append(
                {
                    "path": relative_path.as_posix(),
                    "reason": "exception",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    validate_statement_shape(imported_items, root)

    final_items = retained + imported_items
    knowledge_path.write_text(json.dumps(final_items, ensure_ascii=False, indent=2), encoding="utf-8")

    failure_payload = {
        "source_root": str(source_root),
        "skipped_files": skipped_files,
        "failed_files": failed_files,
    }
    failure_path.write_text(json.dumps(failure_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report_payload = {
        "source_root": str(source_root),
        "retained_existing_count": len(retained),
        "imported_statement_count": len(imported_items),
        "final_statement_count": len(final_items),
        "imported_file_count": len(imported_files),
        "skipped_file_count": len(skipped_files),
        "failed_file_count": len(failed_files),
        "imported_files": imported_files,
    }
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_payload


if __name__ == "__main__":
    summary = import_materials()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
