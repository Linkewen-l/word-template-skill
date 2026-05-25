from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from docx_reader import DocumentFacts
from heading_detector import HeadingNode


WritingType = Literal[
    "patent",
    "course_report",
    "project_plan",
    "technical_spec",
    "general_report",
]


PATENT_TITLES = {
    "说明书摘要",
    "摘要附图",
    "技术领域",
    "技术背景",
    "背景技术",
    "发明内容",
    "附图说明",
    "具体实施方式",
    "权利要求书",
    "摘要",
}


@dataclass(frozen=True)
class SectionContext:
    heading: HeadingNode
    parent_title: Optional[str]
    previous_title: Optional[str]
    next_title: Optional[str]
    outline: str
    writing_type: WritingType
    document_facts: DocumentFacts
    workflow_context: str = ""


def infer_writing_type(topic: str, headings: list[HeadingNode]) -> WritingType:
    joined = " ".join([topic] + [heading.title for heading in headings])
    if any(title in joined for title in PATENT_TITLES) or "专利" in joined or "权利要求" in joined:
        return "patent"
    if any(keyword in joined for keyword in ("课程", "实验报告", "课程设计", "毕业论文")):
        return "course_report"
    if any(keyword in joined for keyword in ("项目方案", "实施方案", "建设方案", "解决方案", "技术方案")):
        return "project_plan"
    if any(keyword in joined for keyword in ("说明书", "技术规范", "接口规范", "操作手册")):
        return "technical_spec"
    return "general_report"


def build_outline(headings: list[HeadingNode]) -> str:
    lines: list[str] = []
    for heading in headings:
        indent = "  " * max(heading.level - 1, 0)
        lines.append(f"{indent}- L{heading.level}: {heading.title}")
    return "\n".join(lines)


def build_section_messages(
    *,
    topic: str,
    context: SectionContext,
) -> list[dict[str, str]]:
    heading = context.heading
    type_rules = _type_rules(context.writing_type, heading.title, context.document_facts)
    sample_text = heading.sample_text.strip() or "无。"
    workflow_context = context.workflow_context.strip() or "无。"

    system = (
        "你是严谨的中文 Word 文档写作助手。你的输出会被直接插入 Word 模板的当前标题之后。"
        "必须只写当前标题对应的正文，不重复标题，不写其他章节，不使用 Markdown 标题或代码块。"
    )

    user = f"""写作主题:
{topic}

自动判断的文档类型:
{_type_label(context.writing_type)}

当前标题:
{heading.title}

当前标题层级:
{heading.level}

上级标题:
{context.parent_title or "无"}

前一个标题:
{context.previous_title or "无"}

后一个标题:
{context.next_title or "无"}

模板事实:
{context.document_facts.to_prompt_text()}

文档整体标题结构:
{context.outline}

当前标题下模板已有示例文字或占位内容:
{sample_text}

资料分析与问答补充上下文:
{workflow_context}

当前章节写作规则:
{type_rules}

硬性输出要求:
1. 只输出当前标题下应插入的正文内容。
2. 不要重复输出当前标题。
3. 不要生成其他章节内容。
4. 不要使用 Markdown 一级标题、二级标题、列表标题或 ``` 代码块。
5. 不要输出“以下是”“好的”“根据要求”等解释性话语。
6. 内容要适合直接插入 Word，段落之间用空行分隔。
7. 如果模板示例文字明显是占位符，可以按正式正文重写；如果是有效示例，只参考其语气和格式，不要照抄。
8. 中文表达应正式、清晰、连贯，严格围绕当前标题和写作主题。
9. 不要输出 HTML 标签或 Markdown/LaTeX 公式标记；涉及变量符号时使用 H_T、H_F、g_T、H_Fusion、R^B 等纯文本写法，系统会在写入 Word 时自动转换上下标。
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _type_rules(writing_type: WritingType, title: str, facts: DocumentFacts) -> str:
    normalized_title = title.strip().rstrip(":：")
    if writing_type == "patent":
        if normalized_title in {"说明书摘要", "摘要"}:
            return "按中国发明专利说明书摘要习惯，概括技术方案、核心步骤和有益效果，篇幅克制，不写营销化表述。"
        if normalized_title == "摘要附图":
            return "按中国发明专利摘要附图习惯，输出最能代表技术方案的图号或极简说明；如未给出明确附图，优先选择图1，不编造不存在的图形细节。"
        if normalized_title == "技术领域":
            return "按中国发明专利写作习惯，说明本发明所属技术领域，避免夸张宣传。"
        if normalized_title in {"背景技术", "技术背景"}:
            return "按中国发明专利写作习惯，客观描述现有技术及其不足，突出需要解决的技术问题。"
        if normalized_title == "发明内容":
            return "按中国发明专利写作习惯，写明发明目的、技术方案和有益效果，逻辑完整。"
        if normalized_title == "附图说明":
            return (
                "按中国发明专利写作习惯说明附图。"
                f"模板检测到的内嵌图像/形状数量为 {facts.inline_shape_count}，不要假设固定附图数量。"
                "如模板或用户主题没有给出明确图号，仅生成克制、可调整的附图说明。"
            )
        if normalized_title == "具体实施方式":
            return "按中国发明专利写作习惯，围绕主题写完整实施例，步骤和部件关系要清楚。"
        if normalized_title == "权利要求书":
            return "按中国发明专利权利要求格式撰写，使用编号条款，独立权利要求在前，从属权利要求在后。"
        return "整体按中国发明专利申请文件语气撰写，技术效果和技术特征要对应。"

    if writing_type == "course_report":
        return "按课程报告、实验报告或论文式语气撰写，结构清晰，不要过度专利化。"
    if writing_type == "project_plan":
        return "按项目方案语气撰写，突出目标、方法、实施路径、风险和交付价值。"
    if writing_type == "technical_spec":
        return "按技术说明书语气撰写，表达准确、可执行，术语一致，避免空泛口号。"
    return "按普通论文或报告语气撰写，层次清楚，内容与当前标题严格对应。"


def _type_label(writing_type: WritingType) -> str:
    labels = {
        "patent": "专利申请文件",
        "course_report": "课程报告",
        "project_plan": "项目方案",
        "technical_spec": "技术说明书",
        "general_report": "普通论文/报告",
    }
    return labels[writing_type]
