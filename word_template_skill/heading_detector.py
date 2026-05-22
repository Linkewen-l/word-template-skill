from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

try:
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover - surfaced at runtime by main
    Paragraph = object  # type: ignore[assignment,misc]


DetectionMode = Literal["auto", "style", "text", "all"]


STYLE_LEVELS = {
    "heading 1": 1,
    "heading1": 1,
    "标题 1": 1,
    "标题1": 1,
    "heading 2": 2,
    "heading2": 2,
    "标题 2": 2,
    "标题2": 2,
    "heading 3": 3,
    "heading3": 3,
    "标题 3": 3,
    "标题3": 3,
}

SPECIAL_PATENT_TITLES = {
    "技术领域",
    "背景技术",
    "发明内容",
    "附图说明",
    "具体实施方式",
    "权利要求书",
    "摘要",
}

COMMON_SKIP_TITLES = {
    "封面",
    "目录",
    "contents",
    "table of contents",
    "参考文献",
    "references",
    "致谢",
    "acknowledgements",
    "acknowledgments",
}


@dataclass
class HeadingNode:
    title: str
    level: int
    paragraph_index: int
    start_index: int
    end_index: int
    style_name: str
    paragraph: Paragraph
    parent_index: Optional[int] = None
    children: list[int] = field(default_factory=list)
    previous_title: Optional[str] = None
    next_title: Optional[str] = None
    sample_text: str = ""

    def to_log_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "level": self.level,
            "paragraph_index": self.paragraph_index,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "style_name": self.style_name,
            "parent_index": self.parent_index,
            "children": self.children,
        }


def detect_headings(paragraphs: Iterable[Paragraph], mode: DetectionMode = "auto") -> list[HeadingNode]:
    """Detect headings, preferring built-in Word heading styles in auto mode."""
    paragraph_list = list(paragraphs)
    style_headings = _detect_by_style(paragraph_list)

    if mode == "style":
        headings = style_headings
    elif mode == "text":
        headings = _detect_by_text(paragraph_list)
    elif mode == "all":
        headings = _merge_headings(style_headings, _detect_by_text(paragraph_list))
    else:
        headings = style_headings if style_headings else _detect_by_text(paragraph_list)

    headings = sorted(headings, key=lambda item: item.paragraph_index)
    _fill_ranges_and_tree(headings, len(paragraph_list))
    _fill_sample_text(headings, paragraph_list)
    return headings


def is_common_skip_title(title: str, extra_skip_titles: Optional[set[str]] = None) -> bool:
    normalized = _normalize_title(title).lower()
    if normalized in COMMON_SKIP_TITLES:
        return True
    if extra_skip_titles and normalized in {_normalize_title(item).lower() for item in extra_skip_titles}:
        return True
    return False


def _detect_by_style(paragraphs: list[Paragraph]) -> list[HeadingNode]:
    results: list[HeadingNode] = []
    for index, paragraph in enumerate(paragraphs):
        title = _clean_text(paragraph.text)
        if not title:
            continue
        style_name = _style_name(paragraph)
        level = STYLE_LEVELS.get(_normalize_style(style_name))
        if level is None:
            continue
        results.append(
            HeadingNode(
                title=title,
                level=level,
                paragraph_index=index,
                start_index=index + 1,
                end_index=index,
                style_name=style_name,
                paragraph=paragraph,
            )
        )
    return results


def _detect_by_text(paragraphs: list[Paragraph]) -> list[HeadingNode]:
    results: list[HeadingNode] = []
    for index, paragraph in enumerate(paragraphs):
        title = _clean_text(paragraph.text)
        if not _looks_like_heading_text(title):
            continue
        level = _text_heading_level(title)
        results.append(
            HeadingNode(
                title=title,
                level=level,
                paragraph_index=index,
                start_index=index + 1,
                end_index=index,
                style_name=_style_name(paragraph),
                paragraph=paragraph,
            )
        )
    return results


def _merge_headings(style_headings: list[HeadingNode], text_headings: list[HeadingNode]) -> list[HeadingNode]:
    by_index = {heading.paragraph_index: heading for heading in text_headings}
    by_index.update({heading.paragraph_index: heading for heading in style_headings})
    return list(by_index.values())


def _fill_ranges_and_tree(headings: list[HeadingNode], paragraph_count: int) -> None:
    for current_pos, heading in enumerate(headings):
        end_index = paragraph_count - 1
        for later in headings[current_pos + 1 :]:
            if later.level <= heading.level:
                end_index = later.paragraph_index - 1
                break
        heading.start_index = heading.paragraph_index + 1
        heading.end_index = max(heading.paragraph_index, end_index)
        heading.previous_title = headings[current_pos - 1].title if current_pos > 0 else None
        heading.next_title = headings[current_pos + 1].title if current_pos + 1 < len(headings) else None

    stack: list[tuple[int, HeadingNode]] = []
    for index, heading in enumerate(headings):
        while stack and stack[-1][1].level >= heading.level:
            stack.pop()
        if stack:
            parent_index = stack[-1][0]
            heading.parent_index = parent_index
            headings[parent_index].children.append(index)
        stack.append((index, heading))


def _fill_sample_text(
    headings: list[HeadingNode],
    paragraphs: list[Paragraph],
    *,
    max_chars: int = 1200,
) -> None:
    heading_indexes = {heading.paragraph_index for heading in headings}
    for heading in headings:
        samples: list[str] = []
        for index in range(heading.start_index, heading.end_index + 1):
            if index in heading_indexes:
                continue
            text = _clean_text(paragraphs[index].text)
            if text:
                samples.append(text)
            if sum(len(item) for item in samples) >= max_chars:
                break
        sample_text = "\n".join(samples)
        heading.sample_text = sample_text[:max_chars]


def _looks_like_heading_text(text: str) -> bool:
    if not text:
        return False
    normalized = _normalize_title(text)
    if normalized in SPECIAL_PATENT_TITLES or normalized.lower() in COMMON_SKIP_TITLES:
        return True
    if len(text) > 80:
        return False
    patterns = (
        r"^[一二三四五六七八九十百]+、\S+",
        r"^（[一二三四五六七八九十百]+）\S+",
        r"^\([一二三四五六七八九十百]+\)\S+",
        r"^\d+(?:\.\d+){0,3}[\.、]?\s+\S+",
        r"^第\s*\d+\s*章\s+\S+",
        r"^第\s*[一二三四五六七八九十百]+\s*章\s+\S+",
    )
    return any(re.match(pattern, normalized) for pattern in patterns)


def _text_heading_level(text: str) -> int:
    normalized = _normalize_title(text)
    if normalized in SPECIAL_PATENT_TITLES or normalized.lower() in COMMON_SKIP_TITLES:
        return 1
    if re.match(r"^（[一二三四五六七八九十百]+）", normalized) or re.match(
        r"^\([一二三四五六七八九十百]+\)", normalized
    ):
        return 2
    number_match = re.match(r"^(\d+(?:\.\d+){0,3})", normalized)
    if number_match:
        return min(number_match.group(1).count(".") + 1, 3)
    return 1


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _style_name(paragraph: Paragraph) -> str:
    try:
        return paragraph.style.name or ""
    except Exception:
        return ""


def _normalize_style(style_name: str) -> str:
    return re.sub(r"\s+", " ", style_name or "").strip().lower()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip()
