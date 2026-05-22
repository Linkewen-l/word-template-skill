from __future__ import annotations

import copy
import re
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


def normalize_generated_text(text: str) -> str:
    """Remove wrappers that should not be inserted into Word."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
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
    """Split model output into Word paragraphs while keeping simple line breaks."""
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
    lines = text.split("\n")
    run = paragraph.add_run(lines[0] if lines else "")
    copy_run_format(run, reference_run)
    for line in lines[1:]:
        run.add_break(WD_BREAK.LINE)
        run.add_text(line)


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
    """Copy low-level paragraph properties but avoid numbering definitions."""
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
