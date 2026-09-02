from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable
from typing import Any

TELEGRAM_SAFE_LIMIT = 3500


def _normalize_markdown(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"(?m)^\s*#{1,6}\s+(.+?)\s*$", r"\1", value)
    value = re.sub(r"(?m)^\s*[-=_]{3,}\s*$", "", value)
    value = _remove_markdown_tables(value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _remove_markdown_tables(text: str) -> str:
    output: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if "|" not in line:
            output.append(raw_line)
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        if len(cells) == 1:
            output.append(cells[0])
        else:
            output.append("• " + " — ".join(cells))
    return "\n".join(output)


def _split_plain_text(text: str, max_len: int) -> list[str]:
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""

    def push_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_len:
            current = candidate
            continue
        push_current()
        if len(paragraph) <= max_len:
            current = paragraph
            continue
        sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
        if len(sentences) == 1:
            sentences = paragraph.split()
        line = ""
        for item in sentences:
            candidate = f"{line} {item}".strip() if line else item
            if len(candidate) <= max_len:
                line = candidate
            else:
                if line:
                    chunks.append(line)
                line = item[:max_len]
                while len(item) > max_len:
                    chunks.append(item[:max_len])
                    item = item[max_len:]
                    line = item
        if line:
            current = line
    push_current()
    return chunks or [""]


def _format_inline(text: str) -> str:
    text = text.replace("**", "\u0000BOLD\u0000")
    parts = text.split("\u0000BOLD\u0000")
    formatted: list[str] = []
    bold_open = False
    for part in parts:
        escaped = _format_code(html.escape(part, quote=False))
        if bold_open:
            formatted.append(f"<b>{escaped}</b>")
        else:
            formatted.append(escaped)
        bold_open = not bold_open
    if len(parts) % 2 == 0:
        return "".join(formatted).replace("<b>", "").replace("</b>", "")
    return "".join(formatted)


def _format_code(escaped_text: str) -> str:
    parts = escaped_text.split("`")
    if len(parts) == 1:
        return escaped_text
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1 and part:
            out.append(f"<code>{part}</code>")
        else:
            out.append(part)
    if len(parts) % 2 == 0:
        return "".join(out).replace("<code>", "").replace("</code>", "")
    return "".join(out)


def _format_html_chunk(text: str) -> str:
    lines = text.split("\n")
    output: list[str] = []
    code_lines: list[str] = []
    in_code = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                output.append(f"<pre>{html.escape(chr(10).join(code_lines), quote=False)}</pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered = re.match(r"^(\d+[.)])\s+(.+)$", stripped)
        if bullet:
            output.append(f"• {_format_inline(bullet.group(1))}")
        elif numbered:
            output.append(f"{html.escape(numbered.group(1), quote=False)} {_format_inline(numbered.group(2))}")
        else:
            output.append(_format_inline(stripped))

    if code_lines:
        output.append(f"<pre>{html.escape(chr(10).join(code_lines), quote=False)}</pre>")
    return "\n".join(output).strip()


def telegram_html_chunks(text: str, max_len: int = TELEGRAM_SAFE_LIMIT) -> list[str]:
    normalized = _normalize_markdown(text)
    plain_chunks = _split_plain_text(normalized, max_len=max_len - 300)
    html_chunks: list[str] = []
    for chunk in plain_chunks:
        formatted = _format_html_chunk(chunk)
        if len(formatted) <= max_len:
            html_chunks.append(formatted)
            continue
        html_chunks.extend(_format_html_chunk(part) for part in _split_plain_text(chunk, max_len=max_len - 300))
    return [chunk for chunk in html_chunks if chunk.strip()] or ["OK"]


async def send_telegram_chunks(
    answer: Callable[[str], Awaitable[Any]],
    text: str,
    *,
    logger: Any,
    agent: str,
    kind: str,
    task_id: int | None = None,
) -> None:
    chunks = telegram_html_chunks(text)
    for index, chunk in enumerate(chunks, start=1):
        await answer(chunk)
        logger.info(
            f"[{agent}] reply chunk sent",
            agent=agent,
            kind=kind,
            task_id=task_id,
            chunk_index=index,
            chunk_count=len(chunks),
            length=len(chunk),
        )
