from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass
from typing import Optional

from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run


EXPLANATION_PREFIXES = (
    "以下是",
    "好的",
    "根据要求",
    "根据你的要求",
    "下面是",
)

HTML_SCRIPT_PATTERN = re.compile(r"<\s*(sub|sup)\b[^>]*>(.*?)<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
HTML_BREAK_PATTERN = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
SCRIPTABLE_CHAR_CLASS = r"A-Za-zΑ-Ωα-ω"
SCRIPT_VALUE_CLASS = r"A-Za-z0-9Α-Ωα-ω"
SCRIPT_PATTERN = re.compile(
    rf"(?<![{SCRIPTABLE_CHAR_CLASS}])"
    rf"([{SCRIPTABLE_CHAR_CLASS}])\s*([_^])\s*(?:\{{([^{{}}]+)\}}|([{SCRIPT_VALUE_CLASS}]+))"
)


@dataclass(frozen=True)
class RichTextToken:
    text: str
    script: str = ""


def normalize_generated_text(text: str) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = html.unescape(cleaned)
    cleaned = HTML_BREAK_PATTERN.sub("\n", cleaned)
    cleaned = re.sub(r"```(?:\w+)?", "", cleaned)
    cleaned = cleaned.replace("```", "")
    lines = [line.rstrip() for line in cleaned.split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)

    if lines and any(lines[0].lstrip().startswith(prefix) for prefix in EXPLANATION_PREFIXES):
        lines.pop(0)

    result = "\n".join(lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def split_generated_paragraphs(text: str) -> list[str]:
    cleaned = normalize_generated_text(text)
    if not cleaned:
        return []

    raw_blocks = re.split(r"\n\s*\n", cleaned)
    paragraphs: list[str] = []
    for block in raw_blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if len(lines) > 1 and all(_looks_like_list_line(line) for line in lines):
            paragraphs.extend(lines)
        else:
            paragraphs.append("\n".join(lines))
    return paragraphs


def copy_paragraph_format(target: Paragraph, reference: Optional[Paragraph]) -> None:
    if reference is None:
        return
    try:
        target.style = reference.style
    except Exception:
        pass

    source_format = reference.paragraph_format
    target_format = target.paragraph_format
    for attr in (
        "alignment",
        "first_line_indent",
        "keep_together",
        "keep_with_next",
        "left_indent",
        "line_spacing",
        "line_spacing_rule",
        "page_break_before",
        "right_indent",
        "space_after",
        "space_before",
        "widow_control",
    ):
        try:
            setattr(target_format, attr, getattr(source_format, attr))
        except Exception:
            continue


def add_text_with_reference_run(paragraph: Paragraph, text: str, reference_run: Optional[Run]) -> None:
    for token in _parse_rich_text_tokens(text):
        _add_rich_text_token(paragraph, token, reference_run)


def clear_paragraph_content(paragraph: Paragraph) -> None:
    try:
        paragraph.clear()
        return
    except AttributeError:
        pass

    p_element = paragraph._p
    for child in list(p_element):
        if child.tag != qn("w:pPr"):
            p_element.remove(child)


def copy_run_format(target: Run, reference: Optional[Run]) -> None:
    if reference is None:
        return
    try:
        if reference.style is not None:
            target.style = reference.style
    except Exception:
        pass

    target.font.name = reference.font.name
    target.font.size = reference.font.size
    target.font.bold = reference.font.bold
    target.font.italic = reference.font.italic
    target.font.underline = reference.font.underline
    target.font.all_caps = reference.font.all_caps
    target.font.small_caps = reference.font.small_caps
    target.font.strike = reference.font.strike
    target.font.subscript = reference.font.subscript
    target.font.superscript = reference.font.superscript
    target.font.highlight_color = reference.font.highlight_color

    try:
        if reference.font.color.rgb is not None:
            target.font.color.rgb = reference.font.color.rgb
        if reference.font.color.theme_color is not None:
            target.font.color.theme_color = reference.font.color.theme_color
    except Exception:
        pass

    _copy_rfonts(target, reference)


def first_text_run(paragraph: Optional[Paragraph]) -> Optional[Run]:
    if paragraph is None:
        return None
    for run in paragraph.runs:
        if run.text.strip():
            return run
    return paragraph.runs[0] if paragraph.runs else None


def clone_paragraph_properties_without_numbering(target: Paragraph, reference: Optional[Paragraph]) -> None:
    if reference is None or reference._p.pPr is None:
        return
    cloned = copy.deepcopy(reference._p.pPr)
    for num_pr in cloned.xpath("./w:numPr"):
        cloned.remove(num_pr)

    target_p = target._p
    existing = target_p.pPr
    if existing is not None:
        target_p.remove(existing)
    target_p.insert(0, cloned)


def _copy_rfonts(target: Run, reference: Run) -> None:
    ref_rpr = reference._element.rPr
    if ref_rpr is None or ref_rpr.rFonts is None:
        return

    target_rpr = target._element.get_or_add_rPr()
    target_fonts = target_rpr.get_or_add_rFonts()
    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
        value = ref_rpr.rFonts.get(qn(f"w:{attr}"))
        if value:
            target_fonts.set(qn(f"w:{attr}"), value)


def _looks_like_list_line(line: str) -> bool:
    return bool(re.match(r"^([-•]\s+|\d+[\.、]\s+)", line.strip()))


def _parse_rich_text_tokens(text: str) -> list[RichTextToken]:
    text = html.unescape(HTML_BREAK_PATTERN.sub("\n", text or ""))
    tokens: list[RichTextToken] = []
    position = 0
    for match in HTML_SCRIPT_PATTERN.finditer(text):
        tokens.extend(_parse_plain_script_tokens(text[position : match.start()]))
        script = "sub" if match.group(1).lower() == "sub" else "sup"
        inner = HTML_TAG_PATTERN.sub("", html.unescape(match.group(2)))
        tokens.append(RichTextToken(inner, script))
        position = match.end()
    tokens.extend(_parse_plain_script_tokens(text[position:]))
    return _merge_rich_text_tokens(tokens)


def _parse_plain_script_tokens(text: str) -> list[RichTextToken]:
    text = HTML_TAG_PATTERN.sub("", text)
    tokens: list[RichTextToken] = []
    position = 0
    for match in SCRIPT_PATTERN.finditer(text):
        tokens.append(RichTextToken(text[position : match.start()]))
        base, marker, grouped, simple = match.groups()
        tokens.append(RichTextToken(base))
        tokens.append(RichTextToken(grouped or simple or "", "sub" if marker == "_" else "sup"))
        position = match.end()
    tokens.append(RichTextToken(text[position:]))
    return tokens


def _merge_rich_text_tokens(tokens: list[RichTextToken]) -> list[RichTextToken]:
    merged: list[RichTextToken] = []
    for token in tokens:
        if not token.text:
            continue
        if merged and merged[-1].script == token.script:
            merged[-1] = RichTextToken(merged[-1].text + token.text, token.script)
        else:
            merged.append(token)
    return merged


def _add_rich_text_token(paragraph: Paragraph, token: RichTextToken, reference_run: Optional[Run]) -> None:
    parts = token.text.split("\n")
    for index, part in enumerate(parts):
        if index:
            break_run = paragraph.add_run()
            copy_run_format(break_run, reference_run)
            break_run.add_break(WD_BREAK.LINE)
        if not part:
            continue
        run = paragraph.add_run(part)
        copy_run_format(run, reference_run)
        if token.script == "sub":
            run.font.subscript = True
            run.font.superscript = False
        elif token.script == "sup":
            run.font.superscript = True
            run.font.subscript = False
