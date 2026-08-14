"""Platform-owned offline renderer for common office artifacts.

The Agent writes content, not executable code.  This script is frozen in the
runner image and is the only entrypoint used by generic artifact jobs.
"""
import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path


def safe_name(value: str, extension: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|]+", "-", value or "generated").strip(" .-")
    base = base[:100] or "generated"
    return base if base.lower().endswith("." + extension) else base + "." + extension


def markdown_lines(content: str):
    for line in content.replace("\r\n", "\n").split("\n"):
        yield line.rstrip()


def is_duplicate_title_heading(line: str, title: str) -> bool:
    """Return true only for a leading Markdown H1 that repeats the artifact title."""
    heading = re.match(r"^#\s+(.+?)\s*$", line or "")
    return heading is not None and heading.group(1).strip() == (title or "").strip()


def strip_duplicate_html_title(content: str, title: str) -> str:
    """The renderer owns the document H1, so remove an identical leading body H1."""
    escaped_title = re.escape(html.escape((title or "").strip()))
    if not escaped_title:
        return content
    return re.sub(r"^\s*<h1(?:\s[^>]*)?>\s*" + escaped_title + r"\s*</h1>\s*", "", content,
                  count=1, flags=re.IGNORECASE)


def parse_table(lines):
    rows = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        rows.append(cells)
    return rows


def inline_markdown(value: str) -> str:
    """Render the safe inline Markdown subset accepted by artifact generation."""
    text = html.escape(value or "")
    # Escape first, then only introduce renderer-owned tags. This keeps source
    # HTML inert while preserving the Markdown formatting users expect in PDFs.
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda match: "<strong>" + (match.group(1) or match.group(2)) + "</strong>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)", lambda match: "<em>" + (match.group(1) or match.group(2)) + "</em>", text)

    def link(match):
        label, url = match.group(1), html.unescape(match.group(2)).strip()
        if re.match(r"https?://[^\s<]+$", url, re.IGNORECASE):
            return '<a href="' + html.escape(url, quote=True) + '">' + label + "</a>"
        return label

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)


HTML_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "strong", "b", "em", "i", "u", "s", "del", "br", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "hr", "pre", "code", "a"}
VOID_HTML_TAGS = {"br", "hr"}
BLOCKED_HTML_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "form", "input", "button", "video", "audio", "canvas", "head", "title"}
SAFE_STYLE_PROPERTIES = {"color", "background-color", "font-size", "font-weight", "font-style", "text-align", "margin", "margin-top", "margin-right", "margin-bottom", "margin-left", "padding", "padding-top", "padding-right", "padding-bottom", "padding-left", "border", "border-collapse", "width", "height", "line-height", "white-space"}


def sanitize_style(value: str) -> str:
    safe = []
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, css_value = (part.strip() for part in declaration.split(":", 1))
        lowered = css_value.lower()
        if name.lower() not in SAFE_STYLE_PROPERTIES or any(token in lowered for token in ("url(", "@import", "expression(", "javascript:", "behavior:")):
            continue
        safe.append(name.lower() + ":" + css_value)
    return ";".join(safe)


class SafeHtml(HTMLParser):
    """Keep layout-only HTML; discard code, resources and active attributes."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.blocked_depth = [], 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in BLOCKED_HTML_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in HTML_TAGS:
            return
        allowed = []
        for name, value in attrs:
            name, value = name.lower(), value or ""
            if name == "style":
                cleaned = sanitize_style(value)
                if cleaned:
                    allowed.append((name, cleaned))
            elif tag == "a" and name == "href" and re.fullmatch(r"https?://[^\s<>]+", value, re.IGNORECASE):
                allowed.append((name, value))
        rendered = "".join(" %s=\"%s\"" % (name, html.escape(value, quote=True)) for name, value in allowed)
        self.parts.append("<" + tag + rendered + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCKED_HTML_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in HTML_TAGS and tag not in VOID_HTML_TAGS:
            self.parts.append("</" + tag + ">")

    def handle_data(self, data):
        if not self.blocked_depth:
            self.parts.append(html.escape(data))


def looks_like_html(content: str) -> bool:
    return bool(re.search(r"<!doctype\s+html|<(?:html|body|h[1-6]|p|div|span|table|ul|ol|blockquote)\b", content or "", re.IGNORECASE))


def sanitize_html(content: str) -> str:
    parser = SafeHtml()
    parser.feed(content or "")
    parser.close()
    return "".join(parser.parts)


def render_docx(title: str, content: str, output: Path):
    from docx import Document
    document = Document()
    document.add_heading(title, 0)
    lines = list(markdown_lines(content))
    index = 0
    while index < len(lines):
        line = lines[index]
        if index == 0 and is_duplicate_title_heading(line, title):
            index += 1
            continue
        if line.startswith("#"):
            heading = re.match(r"^(#{1,6})\s*(.*)$", line)
            if heading:
                document.add_heading(heading.group(2), min(len(heading.group(1)), 4))
            index += 1
            continue
        if line.strip().startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index]); index += 1
            rows = parse_table(table_lines)
            if rows:
                table = document.add_table(rows=0, cols=max(len(row) for row in rows))
                table.style = "Table Grid"
                for row in rows:
                    cells = table.add_row().cells
                    for col, value in enumerate(row): cells[col].text = value
            continue
        if line.startswith("- ") or line.startswith("* "):
            document.add_paragraph(line[2:], style="List Bullet")
        elif re.match(r"^\d+\.\s+", line):
            document.add_paragraph(re.sub(r"^\d+\.\s+", "", line), style="List Number")
        elif line.strip():
            document.add_paragraph(line)
        index += 1
    document.save(output)


def render_xlsx(title: str, content: str, document_plan: dict, output: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    workbook = Workbook(); workbook.remove(workbook.active)
    sheets = document_plan.get("sheets") if isinstance(document_plan, dict) else None
    if not isinstance(sheets, list) or not sheets:
        rows = parse_table(list(markdown_lines(content))) or [[line] for line in markdown_lines(content) if line.strip()]
        sheets = [{"name": title[:31] or "Sheet1", "rows": rows}]
    for sheet_def in sheets:
        worksheet = workbook.create_sheet(str(sheet_def.get("name") or "Sheet")[:31])
        rows = sheet_def.get("rows") or []
        for row in rows: worksheet.append(row if isinstance(row, list) else [row])
        if worksheet.max_row:
            for cell in worksheet[1]: cell.font = Font(bold=True)
        for column in worksheet.columns:
            width = min(48, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            worksheet.column_dimensions[column[0].column_letter].width = width
    workbook.save(output)


def render_pdf(title: str, content: str, output: Path):
    from weasyprint import HTML
    if looks_like_html(content):
        render_pdf_html(title, content, output)
        return
    blocks, lines, index = [], list(markdown_lines(content)), 0
    unordered = re.compile(r"^\s*[-+*]\s+(.+)$")
    ordered = re.compile(r"^\s*(?:\d+[.)]|[一二三四五六七八九十]+、)\s*(.+)$")
    while index < len(lines):
        line = lines[index]
        if index == 0 and is_duplicate_title_heading(line, title):
            index += 1
            continue
        if line.strip().startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index]); index += 1
            rows = parse_table(table_lines)
            if rows:
                header, body = rows[0], rows[1:]
                blocks.append("<table><thead><tr>" + "".join("<th>" + inline_markdown(cell) + "</th>" for cell in header) + "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + inline_markdown(cell) + "</td>" for cell in row) + "</tr>" for row in body) + "</tbody></table>")
            continue
        if unordered.match(line):
            items = []
            while index < len(lines) and unordered.match(lines[index]):
                items.append("<li>" + inline_markdown(unordered.match(lines[index]).group(1)) + "</li>"); index += 1
            blocks.append("<ul>" + "".join(items) + "</ul>"); continue
        if ordered.match(line):
            items = []
            while index < len(lines) and ordered.match(lines[index]):
                items.append("<li>" + inline_markdown(ordered.match(lines[index]).group(1)) + "</li>"); index += 1
            blocks.append("<ol>" + "".join(items) + "</ol>"); continue
        heading = re.match(r"^(#{1,4})\s*(.*)$", line)
        if heading:
            level = len(heading.group(1)); blocks.append(f"<h{level}>" + inline_markdown(heading.group(2)) + f"</h{level}>")
        elif line.strip(): blocks.append("<p>" + inline_markdown(line) + "</p>")
        index += 1
    style = """@page{size:A4;margin:18mm 15mm}body{font-family:'Noto Sans CJK SC','Noto Sans',sans-serif;color:#1f2937;font-size:10.5pt;line-height:1.65}h1{font-size:22pt;margin:0 0 16pt;border-bottom:2px solid #2563eb;padding-bottom:8pt}h2{font-size:16pt;margin:20pt 0 8pt;color:#1e3a8a}h3{font-size:13pt;margin:15pt 0 6pt}p{margin:0 0 7pt}ul,ol{margin:4pt 0 9pt;padding-left:20pt}strong{font-weight:700}em{font-style:italic}del{color:#6b7280;text-decoration:line-through}code{font-family:monospace;background:#f1f5f9;border-radius:3pt;padding:1pt 3pt;color:#9f1239}a{color:#2563eb;text-decoration:underline}table{width:100%;border-collapse:collapse;margin:10pt 0 14pt;font-size:8.5pt;table-layout:auto;page-break-inside:auto}thead{display:table-header-group;background:#eaf2ff}tr{page-break-inside:avoid}th,td{border:1px solid #9ca3af;padding:5pt 6pt;vertical-align:top;word-break:break-word}th{font-weight:700;color:#173b75}"""
    HTML(string="<meta charset='utf-8'><style>" + style + "</style><h1>" + html.escape(title) + "</h1>" + "".join(blocks)).write_pdf(output)


def render_pdf_html(title: str, content: str, output: Path):
    """Render agent-provided HTML only after removing active and remote-capable parts."""
    from weasyprint import HTML
    style = """@page{size:A4;margin:18mm 15mm}body{font-family:'Noto Sans CJK SC','Noto Sans',sans-serif;color:#1f2937;font-size:10.5pt;line-height:1.65}h1{font-size:22pt;margin:0 0 16pt;border-bottom:2px solid #2563eb;padding-bottom:8pt}h2{font-size:16pt;margin:20pt 0 8pt;color:#1e3a8a}h3{font-size:13pt;margin:15pt 0 6pt}p{margin:0 0 7pt}ul,ol{margin:4pt 0 9pt;padding-left:20pt}table{width:100%;border-collapse:collapse;margin:10pt 0 14pt;font-size:8.5pt}thead{display:table-header-group;background:#eaf2ff}tr{page-break-inside:avoid}th,td{border:1px solid #9ca3af;padding:5pt 6pt;vertical-align:top;word-break:break-word}th{font-weight:700;color:#173b75}a{color:#2563eb;text-decoration:underline}pre{white-space:pre-wrap;background:#f8fafc;padding:8pt}code{font-family:monospace}"""
    safe_body = strip_duplicate_html_title(sanitize_html(content), title)
    HTML(string="<meta charset='utf-8'><style>" + style + "</style><h1>" + html.escape(title) + "</h1>" + safe_body).write_pdf(output)


def main():
    payload = json.loads(os.environ.get("AETHER_INPUT_JSON") or "{}")
    format_name = str(payload.get("format") or "").lower()
    if format_name not in {"docx", "xlsx", "pdf"}: raise ValueError("unsupported artifact format")
    title = str(payload.get("title") or "generated")
    output = Path(os.environ["AETHER_OUTPUT_DIR"]); output.mkdir(parents=True, exist_ok=True)
    target = output / safe_name(str(payload.get("fileName") or title), format_name)
    content = str(payload.get("content") or "")
    if format_name == "docx": render_docx(title, content, target)
    elif format_name == "xlsx": render_xlsx(title, content, payload.get("document") or {}, target)
    else: render_pdf(title, content, target)
    print("generated=" + target.name)


if __name__ == "__main__": main()
