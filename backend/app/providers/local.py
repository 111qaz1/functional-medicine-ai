from __future__ import annotations

import base64
import json
import re
import unicodedata
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from pypdf import PdfReader

from app.core.settings import LLMConfig, llm_config_validation_error
from app.domain.models import KnowledgeStatement, SourceSpan
from app.providers.base import DraftCompositionInput, DraftCompositionResult, KnowledgeHit, OCRExtraction


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _tokenize(value: str) -> set[str]:
    tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", value.lower()))
    return {token for token in tokens if len(token) > 1}


class DemoOCRProvider:
    """Hybrid OCR provider that prefers real text extraction and safe vision OCR."""

    _TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json"}
    _IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
    _PPTX_MIME_TYPES = {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    }
    _WORDML_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    _WORDML_BODY_TAG = f"{_WORDML_NS}body"
    _WORDML_TABLE_TAG = f"{_WORDML_NS}tbl"
    _WORDML_ROW_TAG = f"{_WORDML_NS}tr"
    _WORDML_CELL_TAG = f"{_WORDML_NS}tc"
    _WORDML_PARAGRAPH_TAG = f"{_WORDML_NS}p"
    _WORDML_TEXT_TAG = f"{_WORDML_NS}t"
    _WORDML_TAB_TAG = f"{_WORDML_NS}tab"
    _WORDML_BREAK_TAG = f"{_WORDML_NS}br"
    _DRAWINGML_TEXT_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
    _DRAWINGML_PARAGRAPH_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}p"
    _DRAWINGML_BREAK_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}br"
    _PDF_TEXT_LAYER_MIN_LINES = 3
    _PDF_TEXT_LAYER_MIN_CHARS = 40
    _PDF_SCAN_MIN_IMAGE_BYTES = 20_000

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_style: str = "auto",
        timeout_seconds: float = 45.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model = model
        self.api_style = api_style.strip().lower()
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client

    def extract(self, filename: str, content_type: str, content: bytes) -> OCRExtraction:
        suffix = Path(filename).suffix.lower()
        page_lines: list[tuple[int, str]] = []
        confidence = 0.0
        error_message: str | None = None

        if suffix == ".docx":
            page_lines = [(1, line) for line in self._split_lines(self._extract_docx_text(content))]
            confidence = 0.9 if page_lines else 0.12
        elif suffix == ".pptx" or content_type in self._PPTX_MIME_TYPES:
            page_lines = self._extract_pptx_lines(content)
            confidence = 0.88 if page_lines else 0.1
        elif suffix == ".pdf" or content_type == "application/pdf":
            page_lines = self._extract_pdf_lines(content)
            confidence = 0.92 if page_lines else 0.12
        elif content_type.startswith("text/") or suffix in self._TEXT_SUFFIXES:
            decoded = self._decode_text(content)
            page_lines = [(1, line) for line in self._split_lines(decoded)]
            confidence = 0.9 if page_lines else 0.12
        elif content_type.startswith("image/") or suffix in self._IMAGE_SUFFIXES:
            extracted, error_message = self._extract_image_text_with_error(
                content=content,
                content_type=content_type or self._guess_mime_type(suffix),
            )
            page_lines = [(1, line) for line in self._split_lines(extracted)]
            confidence = 0.82 if page_lines else 0.1
        else:
            decoded = self._decode_text(content)
            page_lines = [(1, line) for line in self._split_lines(decoded)]
            confidence = 0.65 if page_lines else 0.08

        spans = [
            SourceSpan(file_name=filename, page=page, line_number=index + 1, snippet=line)
            for index, (page, line) in enumerate(page_lines)
        ]
        text = "\n".join(line for _, line in page_lines)
        return OCRExtraction(text=text, spans=spans, confidence=confidence, error_message=error_message if not text else None)

    def _extract_docx_text(self, content: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
        except Exception:
            return self._decode_text(content)

        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return self._fallback_docx_text(xml.decode("utf-8", errors="ignore"))

        body = root.find(self._WORDML_BODY_TAG)
        if body is None:
            return self._fallback_docx_text(xml.decode("utf-8", errors="ignore"))

        lines: list[str] = []
        for child in body:
            if child.tag == self._WORDML_PARAGRAPH_TAG:
                line = self._docx_paragraph_text(child)
                if line:
                    lines.append(line)
            elif child.tag == self._WORDML_TABLE_TAG:
                lines.extend(self._docx_table_lines(child))

        return "\n".join(lines)

    def _fallback_docx_text(self, xml: str) -> str:
        cleaned = re.sub(r"</w:p>", "\n", xml)
        cleaned = re.sub(r"</w:tc>", " | ", cleaned)
        cleaned = re.sub(r"</w:tr>", "\n", cleaned)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        return cleaned

    def _docx_table_lines(self, table: ET.Element) -> list[str]:
        rows: list[str] = []
        for row in table.findall(self._WORDML_ROW_TAG):
            cells: list[str] = []
            for cell in row.findall(self._WORDML_CELL_TAG):
                cell_lines = [
                    self._docx_paragraph_text(paragraph)
                    for paragraph in cell.findall(f".//{self._WORDML_PARAGRAPH_TAG}")
                ]
                cells.append(self._clean_docx_line(" ".join(line for line in cell_lines if line)))

            line = " | ".join(cells).strip()
            if re.sub(r"[|\s]", "", line):
                rows.append(line)
        return rows

    def _docx_paragraph_text(self, paragraph: ET.Element) -> str:
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == self._WORDML_TEXT_TAG and node.text:
                parts.append(node.text)
            elif node.tag == self._WORDML_TAB_TAG:
                parts.append(" ")
            elif node.tag == self._WORDML_BREAK_TAG:
                parts.append("\n")
        return self._clean_docx_line("".join(parts))

    def _clean_docx_line(self, value: str) -> str:
        value = value.replace("\u3000", " ").replace("\xa0", " ")
        return re.sub(r"\s+", " ", value).strip()

    def _extract_pdf_lines(self, content: bytes) -> list[tuple[int, str]]:
        try:
            reader = PdfReader(BytesIO(content))
        except Exception:
            return []

        page_lines: list[tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            lines = self._split_lines(text)
            if self._should_try_pdf_page_ocr(lines, page):
                ocr_lines = self._extract_pdf_page_image_lines(page)
                if ocr_lines:
                    lines = ocr_lines
            for line in lines:
                page_lines.append((page_number, line))
        return page_lines

    def _should_try_pdf_page_ocr(self, text_lines: list[str], page: object) -> bool:
        if not getattr(page, "images", None):
            return False
        if not (self.base_url and self.api_key and self.model):
            return False
        char_count = sum(len(line) for line in text_lines)
        return len(text_lines) < self._PDF_TEXT_LAYER_MIN_LINES or char_count < self._PDF_TEXT_LAYER_MIN_CHARS

    def _extract_pdf_page_image_lines(self, page: object) -> list[str]:
        try:
            raw_images = list(getattr(page, "images", []) or [])
        except Exception:
            return []

        images = [image for image in raw_images if len(getattr(image, "data", b"")) >= self._PDF_SCAN_MIN_IMAGE_BYTES]
        if not images:
            images = raw_images[:1]

        images = sorted(images, key=lambda image: len(getattr(image, "data", b"")), reverse=True)
        seen_payloads: set[tuple[str, int]] = set()
        best_lines: list[str] = []
        for image in images[:2]:
            data = getattr(image, "data", b"")
            if not data:
                continue
            signature = (getattr(image, "name", ""), len(data))
            if signature in seen_payloads:
                continue
            seen_payloads.add(signature)

            content_type = self._guess_embedded_image_mime_type(image)
            extracted = self._extract_image_text(content=data, content_type=content_type)
            lines = self._split_lines(extracted)
            if len(lines) > len(best_lines):
                best_lines = lines
            if best_lines:
                break
        return best_lines

    def _guess_embedded_image_mime_type(self, image: object) -> str:
        image_name = getattr(image, "name", "")
        suffix = Path(image_name).suffix.lower()
        if suffix in self._IMAGE_SUFFIXES:
            return self._guess_mime_type(suffix)

        data = getattr(image, "data", b"")
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data.startswith(b"BM"):
            return "image/bmp"
        if data.startswith((b"II*\x00", b"MM\x00*")):
            return "image/tiff"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    def _extract_pptx_lines(self, content: bytes) -> list[tuple[int, str]]:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                slide_paths = sorted(
                    [
                        name
                        for name in archive.namelist()
                        if name.startswith("ppt/slides/slide")
                        and name.endswith(".xml")
                        and "/_rels/" not in name
                    ],
                    key=self._slide_sort_key,
                )
                page_lines: list[tuple[int, str]] = []
                for slide_number, slide_path in enumerate(slide_paths, start=1):
                    xml = archive.read(slide_path)
                    for line in self._extract_pptx_slide_lines(xml):
                        page_lines.append((slide_number, line))
                return page_lines
        except Exception:
            return []

    def _slide_sort_key(self, path: str) -> tuple[int, str]:
        match = re.search(r"slide(\d+)\.xml$", path)
        return (int(match.group(1)), path) if match else (10**9, path)

    def _extract_pptx_slide_lines(self, xml: bytes) -> list[str]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []

        paragraphs: list[str] = []
        for paragraph in root.iter(self._DRAWINGML_PARAGRAPH_TAG):
            fragments: list[str] = []
            for node in paragraph.iter():
                if node.tag == self._DRAWINGML_TEXT_TAG and node.text:
                    fragments.append(node.text)
                elif node.tag == self._DRAWINGML_BREAK_TAG:
                    fragments.append("\n")

            merged = "".join(fragments)
            merged_lines = [self._clean_line(line) for line in merged.splitlines()]
            for line in merged_lines:
                if line:
                    paragraphs.append(line)

        return self._split_lines("\n".join(paragraphs))

    def _extract_image_text(self, *, content: bytes, content_type: str) -> str:
        text, _ = self._extract_image_text_with_error(content=content, content_type=content_type)
        return text

    def _extract_image_text_with_error(self, *, content: bytes, content_type: str) -> tuple[str, str | None]:
        if not (self.base_url and self.api_key and self.model):
            return "", "图片 OCR 尚未配置：请先在大模型配置中填写可用的服务地址、API Key 和模型。"
        validation_error = llm_config_validation_error(
            LLMConfig(base_url=self.base_url, api_key=self.api_key, model=self.model, api_style=self.api_style)
        )
        if validation_error:
            return "", f"图片 OCR 配置错误：{validation_error}"

        data_uri = self._to_data_uri(content, content_type)
        client = self.http_client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self.http_client is None
        try:
            if self.api_style in {"auto", "responses"}:
                try:
                    response = client.post(
                        f"{self.base_url}/responses",
                        headers=self._headers(),
                        json=self._build_responses_payload(data_uri),
                        timeout=self.timeout_seconds,
                    )
                    response.raise_for_status()
                    return self._parse_ocr_response(self._extract_response_text(response.json())), None
                except httpx.HTTPStatusError as exc:
                    if self.api_style == "responses":
                        return "", self._format_ocr_http_error(exc)
                except httpx.HTTPError as exc:
                    if self.api_style == "responses":
                        return "", self._format_ocr_request_error(exc)
                except (KeyError, ValueError, json.JSONDecodeError):
                    if self.api_style == "responses":
                        return "", "图片 OCR 服务返回格式无法解析，请检查当前模型是否支持图片识别。"

            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._build_chat_payload(data_uri),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return self._parse_ocr_response(self._extract_response_text(response.json())), None
        except httpx.HTTPStatusError as exc:
            return "", self._format_ocr_http_error(exc)
        except httpx.HTTPError as exc:
            return "", self._format_ocr_request_error(exc)
        except (KeyError, ValueError, json.JSONDecodeError):
            return "", "图片 OCR 服务返回格式无法解析，请检查当前模型是否支持图片识别。"
        finally:
            if close_client:
                client.close()

    def _format_ocr_http_error(self, exc: httpx.HTTPStatusError) -> str:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "图片 OCR 认证失败：API Key 无效、过期或无权限，请在大模型配置中更新后重试。"
        if status_code == 404:
            return "图片 OCR 接口不可用：请检查服务地址、接口类型和模型配置。"
        if status_code == 429:
            return "图片 OCR 调用受限：额度不足或请求过于频繁，请稍后重试或更换可用配置。"
        if status_code == 400:
            return "图片 OCR 请求被拒绝：请确认当前模型支持图片识别，并检查接口类型配置。"
        if 500 <= status_code:
            return "图片 OCR 服务暂时不可用：远端服务返回异常，请稍后重试。"
        return f"图片 OCR 调用失败：远端服务返回 HTTP {status_code}。"

    def _format_ocr_request_error(self, exc: httpx.HTTPError) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "图片 OCR 请求超时：请稍后重试，或调大 OCR 超时时间。"
        return "图片 OCR 服务连接失败：请检查网络、服务地址和代理配置。"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_responses_payload(self, data_uri: str) -> dict[str, object]:
        return {
            "model": self.model,
            "temperature": 0,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": self._ocr_prompt()},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                }
            ],
        }

    def _build_chat_payload(self, data_uri: str) -> dict[str, object]:
        return {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._ocr_prompt()},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        }

    def _ocr_prompt(self) -> str:
        return (
            "你是医学文档 OCR 引擎。请只做逐字抄录，不要总结，不要解释，不要改写。"
            "输出必须是 JSON 对象，包含两个字段：text_lines 和 confidence。"
            "其中 text_lines 必须是字符串数组，数组里的每一项都是图片里真实存在的一整行文字；"
            "如果没有识别到文字，就返回空数组 []。"
            'confidence 只能填 "high"、"medium" 或 "low"。'
            "不要把格式说明、占位词、示例文本写进 text_lines。"
        )

    def _extract_response_text(self, payload: dict[str, object]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}).get("content")
            if isinstance(message, str) and message.strip():
                return message
            if isinstance(message, list):
                joined = "".join(
                    part.get("text", "")
                    for part in message
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ).strip()
                if joined:
                    return joined

        output = payload.get("output")
        if isinstance(output, list):
            chunks = self._collect_text_fragments(output)
            joined = "".join(chunks).strip()
            if joined:
                return joined

        raise ValueError("OCR response is empty")

    def _parse_ocr_response(self, raw_response: str) -> str:
        try:
            payload = json.loads(self._extract_first_json_object(raw_response))
            text_lines = payload.get("text_lines", [])
            if isinstance(text_lines, list):
                normalized_lines = []
                for line in text_lines:
                    if isinstance(line, str):
                        cleaned = self._clean_line(line)
                        if cleaned:
                            normalized_lines.append(cleaned)
                if normalized_lines:
                    return "\n".join(normalized_lines)
        except (ValueError, json.JSONDecodeError):
            pass

        fallback_lines = self._extract_text_lines_fallback(raw_response)
        return "\n".join(fallback_lines)

    def _extract_first_json_object(self, raw_response: str) -> str:
        start = raw_response.find("{")
        if start < 0:
            raise ValueError("OCR response does not contain JSON")

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

        raise ValueError("OCR response returned unterminated JSON")

    def _collect_text_fragments(self, value: object) -> list[str]:
        chunks: list[str] = []
        if isinstance(value, str):
            if value.strip():
                chunks.append(value)
            return chunks

        if isinstance(value, dict):
            for key in ("text", "output_text", "content"):
                nested = value.get(key)
                if nested is not None:
                    chunks.extend(self._collect_text_fragments(nested))
            summary = value.get("summary")
            if summary is not None:
                chunks.extend(self._collect_text_fragments(summary))
            return chunks

        if isinstance(value, list):
            for item in value:
                chunks.extend(self._collect_text_fragments(item))
        return chunks

    def _extract_text_lines_fallback(self, raw_response: str) -> list[str]:
        match = re.search(r"text_lines\s*[:：]\s*\[", raw_response)
        if not match:
            match = re.search(r'"text_lines"\s*:\s*\[', raw_response)
        if not match:
            return []

        array_start = raw_response.find("[", match.start())
        if array_start < 0:
            return []

        array_block = self._extract_balanced_block(raw_response, array_start, "[", "]")
        if not array_block:
            return []

        try:
            parsed = json.loads(array_block)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        normalized_lines: list[str] = []
        for line in parsed:
            if isinstance(line, str):
                cleaned = self._clean_line(line)
                if cleaned:
                    normalized_lines.append(cleaned)
        return normalized_lines

    def _extract_balanced_block(self, value: str, start: int, open_char: str, close_char: str) -> str:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(value)):
            char = value[index]
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
            elif char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return value[start : index + 1]
        return ""

    def _to_data_uri(self, content: bytes, content_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def _decode_text(self, content: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return ""

    def _split_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        for raw_line in text.splitlines():
            cleaned = self._clean_line(raw_line)
            if not cleaned or cleaned in seen:
                continue
            if not self._is_readable_line(cleaned):
                continue
            lines.append(cleaned)
            seen.add(cleaned)
        return lines

    def _clean_line(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value)
        value = value.translate(str.maketrans({"⻬": "齐", "⻅": "见", "⻝": "食"}))
        value = value.replace("\x00", " ")
        value = re.sub(r"[\u200b-\u200f\uFEFF]", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        placeholder_patterns = (
            "逐行文本",
            "逐行的每个放数组里",
            "放数组里",
            "text_lines",
            "confidence",
        )
        normalized = value.lower()
        if any(pattern in value for pattern in placeholder_patterns) or any(pattern in normalized for pattern in ("text_lines", "confidence")):
            return ""
        return value

    def _is_readable_line(self, value: str) -> bool:
        if len(value) < 2:
            return False
        if "\ufffd" in value:
            return False
        allowed = sum(
            1
            for char in value
            if char.isalnum()
            or "\u4e00" <= char <= "\u9fff"
            or char in " .,:;/%+-()[]{}<>_=|#&*'\"，。；：、（）【】《》·"
        )
        if allowed / max(len(value), 1) < 0.88:
            return False
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", value):
            return False
        if re.search(r"[A-Za-z0-9]{20,}", value) and not re.search(r"\s", value):
            return False
        return True

    def _guess_mime_type(self, suffix: str) -> str:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".webp": "image/webp",
        }.get(suffix, "image/png")


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, content: bytes) -> str:
        target = self.root / f"{uuid.uuid4().hex}-{Path(filename).name}"
        target.write_bytes(content)
        return str(target)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._documents: list[KnowledgeStatement] = []

    def index(self, documents: list[KnowledgeStatement]) -> None:
        self._documents = list(documents)

    def search(self, query: str, *, top_k: int = 8) -> list[KnowledgeHit]:
        query_tokens = _tokenize(query)
        hits: list[KnowledgeHit] = []

        for statement in self._documents:
            haystack = " ".join(
                [
                    statement.topic,
                    statement.normalized_text,
                    " ".join(statement.tags),
                    " ".join(statement.topic_tags),
                    " ".join(statement.related_markers),
                    " ".join(statement.related_goals),
                    " ".join(statement.related_skus),
                ]
            )
            doc_tokens = _tokenize(haystack)
            overlap = len(query_tokens & doc_tokens)
            if not overlap:
                continue

            score = overlap / max(len(query_tokens), 1)
            if any(sku in _normalize_text(query) for sku in statement.related_skus):
                score += 0.2
            hits.append(KnowledgeHit(statement=statement, score=round(score, 3)))

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


class GroundedDraftComposer:
    """Local deterministic composer used when no external LLM is configured."""

    def compose(self, draft_input: DraftCompositionInput) -> DraftCompositionResult:
        if draft_input.red_flags:
            return DraftCompositionResult(
                rationale=[f"案例触发人工升级规则: {flag}" for flag in draft_input.red_flags],
                lifestyle_actions=[
                    "在人工审核完成前，先暂停自动给出补充剂结论，优先确认高风险指标与既往病史。",
                ],
                confidence=0.12,
                abstain_reason="触发红旗风险，系统已切换为严格拒答并等待人工审核。",
            )

        blocking_missing = [item for item in draft_input.missing_info if "人工解析校对" in item]
        if blocking_missing:
            return DraftCompositionResult(
                rationale=["病例资料尚不完整，需要先补齐人工校对后再生成草案。"],
                lifestyle_actions=["先完成人工解析校对，再根据已确认的报告信息重新生成草案。"],
                confidence=0.08,
                abstain_reason="病例资料尚未达到可推荐条件，已转人工补全。",
            )

        if not draft_input.candidate_products:
            return DraftCompositionResult(
                rationale=["本地知识和产品规则未形成足够证据，暂不输出营养素推荐。"],
                lifestyle_actions=["优先补充健康目标、用药史、过敏史和关键实验室指标。"],
                confidence=0.08,
                abstain_reason="证据不足，已转人工复核。",
            )

        rationale = []
        lifestyle_actions: list[str] = []
        top_hits = draft_input.knowledge_hits[:4]
        for hit in top_hits:
            rationale.append(f"{hit.statement.topic}: {hit.statement.normalized_text} (证据 {hit.statement.statement_id})")
            lifestyle_actions.extend(hit.statement.lifestyle_actions)

        if not rationale:
            for product in draft_input.candidate_products[:3]:
                rationale.append(f"{product.display_name}: {product.formula_summary}")

        lifestyle_actions = list(dict.fromkeys(lifestyle_actions))[:6]
        if not lifestyle_actions:
            lifestyle_actions = [
                "围绕睡眠、压力、运动和饮食一致性做基础生活方式干预。",
                "若存在用药或过敏不明确，先补齐信息后再升级方案。",
            ]

        confidence = min(
            0.92,
            0.5 + len(top_hits) * 0.06 + min(len(draft_input.candidate_products), 3) * 0.05,
        )
        return DraftCompositionResult(
            rationale=rationale,
            lifestyle_actions=lifestyle_actions,
            confidence=round(confidence, 2),
        )


class JsonKnowledgeImporter:
    def load(self, path: Path) -> list[KnowledgeStatement]:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return [KnowledgeStatement.model_validate(item) for item in payload]
