"use client";

import { ElementType, useRef } from "react";

export type MarkdownViewMode = "edit" | "split" | "preview";

type MarkdownEditorProps = {
  value: string;
  onChange: (value: string) => void;
  mode: MarkdownViewMode;
  onModeChange: (mode: MarkdownViewMode) => void;
  expanded?: boolean;
};

function inlineText(text: string) {
  const chunks = text.split(/(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g).filter(Boolean);
  return chunks.map((chunk, index) => {
    if (chunk.startsWith("`") && chunk.endsWith("`")) return <code key={index}>{chunk.slice(1, -1)}</code>;
    if ((chunk.startsWith("**") && chunk.endsWith("**")) || (chunk.startsWith("__") && chunk.endsWith("__"))) return <strong key={index}>{chunk.slice(2, -2)}</strong>;
    if ((chunk.startsWith("*") && chunk.endsWith("*")) || (chunk.startsWith("_") && chunk.endsWith("_"))) return <em key={index}>{chunk.slice(1, -1)}</em>;
    return <span key={index}>{chunk}</span>;
  });
}

export function MarkdownPreview({ value }: { value: string }) {
  const lines = value.split(/\r?\n/);
  const blocks: React.ReactNode[] = [];
  let list: Array<{ text: string; heading: boolean }> = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`list-${blocks.length}`} className="markdown-preview__list">{list.map((item, index) => <li key={index} className={item.heading ? "markdown-preview__list-heading" : ""}>{item.heading ? <strong>{inlineText(item.text)}</strong> : inlineText(item.text)}</li>)}</ul>);
      list = [];
    }
    if (paragraph.length) {
      blocks.push(<p key={`p-${blocks.length}`}>{paragraph.map((line, index) => <span key={index}>{index ? <br /> : null}{inlineText(line)}</span>)}</p>);
      paragraph = [];
    }
  };

  lines.forEach((line, index) => {
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length, 4);
      const Tag = `h${level}` as ElementType;
      blocks.push(<Tag key={`h-${index}`} className={`markdown-preview__heading markdown-preview__heading--${level}`}>{inlineText(heading[2])}</Tag>);
    } else if (bullet) {
      if (paragraph.length) flush();
      const nestedHeading = bullet[1].match(/^#{1,4}\s+(.+)$/);
      list.push({ text: nestedHeading ? nestedHeading[1] : bullet[1], heading: Boolean(nestedHeading) });
    } else if (line.trim() === "") {
      flush();
    } else if (/^>\s?/.test(line)) {
      flush();
      blocks.push(<blockquote key={`quote-${index}`}>{inlineText(line.replace(/^>\s?/, ""))}</blockquote>);
    } else {
      paragraph.push(line);
    }
  });
  flush();
  return <div className="markdown-preview">{blocks.length ? blocks : <p className="muted">暂无可预览内容</p>}</div>;
}

export function MarkdownEditor({ value, onChange, mode, onModeChange, expanded = false }: MarkdownEditorProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const insert = (before: string, after = "", fallback = "文本") => {
    const textarea = ref.current;
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = value.slice(start, end) || fallback;
    const next = `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`;
    onChange(next);
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(start + before.length, start + before.length + selected.length);
    });
  };

  return <div className={`markdown-editor ${expanded ? "markdown-editor--expanded" : ""}`}>
    <div className="markdown-editor__toolbar">
      <div className="markdown-editor__tools">
        <button type="button" onClick={() => insert("## ", "", "小标题")}>H2</button>
        <button type="button" onClick={() => insert("**", "**")}>粗体</button>
        <button type="button" onClick={() => insert("- ", "", "列表项")}>列表</button>
        <button type="button" onClick={() => insert("> ", "", "提示内容")}>引用</button>
        <button type="button" onClick={() => insert("---\n", "")}>分隔线</button>
      </div>
      <div className="markdown-editor__modes" role="tablist" aria-label="报告编辑模式">
        {([["edit", "编辑"], ["split", "并排"], ["preview", "预览"]] as const).map(([key, label]) => <button key={key} type="button" className={mode === key ? "is-active" : ""} onClick={() => onModeChange(key)}>{label}</button>)}
      </div>
    </div>
    <div className={`markdown-editor__body markdown-editor__body--${mode}`}>
      {mode !== "preview" ? <textarea ref={ref} className="markdown-editor__textarea" value={value} onChange={(event) => onChange(event.target.value)} aria-label="Markdown 编辑内容" /> : null}
      {mode !== "edit" ? <MarkdownPreview value={value} /> : null}
    </div>
    <div className="markdown-editor__footer"><span>Markdown 内容会实时同步到右侧预览</span><span>{value.length} 字符</span></div>
  </div>;
}
