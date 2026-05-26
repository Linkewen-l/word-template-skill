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
    "说明书附图",
    "技术领域",
    "技术背景",
    "背景技术",
    "发明内容",
    "附图说明",
    "具体实施方式",
    "权利要求书",
    "摘要",
}


FIGURE_PROMPT_STYLE_RULES = (
    "参考用户示例图的论文级技术机制图效果，而不是普通流程图。白色背景，16:9 横版，"
    "整体为高密度、多分区、可发表论文 Figure 风格：中间放核心主流程或总架构，周围布置若干带小标题的功能分区，"
    "分区使用灰色或彩色虚线边框、浅色底纹和细黑线，形成类似(a)(b)(c)或【时域分支】【频域分支】【融合模块】【分类模块】的结构。"
    "必须包含输入、预处理/特征提取、关键算法模块、融合/判别、输出结果之间的箭头关系，并根据当前主题和代码事实生成具体模块名，"
    "不要使用泛泛的“模块1、模块2”。在分区内部绘制小型子模块、嵌套框、符号节点、加号/乘号/门控/权重/掩码等机制元素；"
    "可嵌入小曲线图、热力图、频谱图、矩阵块、局部放大框、公式说明框或图例，用于表现算法细节。"
    "配色采用克制的学术配色：时域/主干可用浅蓝，频域可用浅橙，融合可用浅绿，分类或输出可用浅紫，"
    "重要路径用蓝色或绿色箭头强调，实验结果或高亮区域可用红色虚线框标出。"
    "文字全部使用简体中文，字体接近思源黑体/微软雅黑，标签清晰锐利；线条细、箭头方向明确、布局紧凑但不拥挤。"
    "整体像深度学习模型结构图、信号处理网络示意图或专利技术方案机制图，具有局部细节和层次感。"
    "不要生成3D效果、卡通风、照片风、渐变炫光、深色背景、装饰性背景、孤立大图标或低信息量扁平海报。"
    "高清矢量质感，分辨率不低于3840×2160。"
)


FIGURE_CLASSIFICATION_RULES = (
    "附图生成规则：\n"
    "1. 先根据写作主题、资料分析、代码符号、当前图题或模板占位内容，逐图判断附图性质，不要把图的主题写死为某个固定领域。\n"
    "2. 方法流程图、系统结构图、模型结构图、算法模块机制图、训练策略示意图、数据处理流程图等概念性图，输出图号、图名、简短说明和“图片生成提示词：”。\n"
    "3. 训练曲线、损失/准确率/F1曲线、混淆矩阵、热力图、t-SNE/UMAP散点图、样本波形、频谱图、掩码可视化、预测结果图等由代码实际运行产生的实验结果图，不要生成图片提示词，只写“图片来源：由代码运行生成”；如上下文能看出函数名或输出文件名，可在括号中补充。\n"
    "4. 对需要提示词的图，提示词必须写成可直接交给图片生成模型的完整描述，不能只写一句“画流程图”。提示词中的模块名称、箭头关系、分区名称、公式说明和小图内容必须来自当前主题、代码事实和章节上下文；视觉风格统一采用以下约束："
    f"{FIGURE_PROMPT_STYLE_RULES}\n"
    "5. 如果主题涉及深度学习、故障诊断、信号处理、传感器数据、时序预测、分类识别或多分支网络，优先组织成类似示例图的“输入-多分支特征提取-融合模块-输出判别”论文机制图。\n"
    "6. 不要输出 Markdown 表格、代码块或项目符号；每张图使用自然段格式，便于直接写入 Word。"
)


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
    section_specific_rules = _section_specific_rules(heading.title, sample_text)

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
{section_specific_rules}

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
            return (
                "按中国发明专利摘要附图习惯，选择最能代表技术方案的附图。"
                "若该图属于概念性图，应给出图片生成提示词；若属于实验结果图，仅标注由代码运行生成。"
            )
        if normalized_title == "技术领域":
            return "按中国发明专利写作习惯，说明本发明所属技术领域，避免夸张宣传。"
        if normalized_title in {"背景技术", "技术背景"}:
            return "按中国发明专利写作习惯，客观描述现有技术及其不足，突出需要解决的技术问题。"
        if normalized_title == "发明内容":
            return "按中国发明专利写作习惯，写明发明目的、技术方案和有益效果，逻辑完整。"
        if normalized_title in {"附图说明", "说明书附图"}:
            return (
                "按中国发明专利写作习惯说明附图。"
                f"模板检测到的内嵌图像/形状数量为 {facts.inline_shape_count}，不要假设固定附图数量。"
                "如模板或用户主题没有给出明确图号，仅生成克制、可调整的附图说明。"
                "需要根据当前主题和代码事实智能区分概念性附图与实验结果附图。"
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


def _section_specific_rules(title: str, sample_text: str) -> str:
    normalized_title = title.strip().rstrip(":：")
    sample = sample_text or ""
    rules: list[str] = []

    if normalized_title in {"附图说明", "说明书附图", "摘要附图"}:
        rules.append(FIGURE_CLASSIFICATION_RULES)

    if normalized_title in {"说明书摘要", "摘要"} and ("摘要附图" in sample or "在此插入摘要附图" in sample):
        rules.append(
            "摘要附图补充规则：当前摘要区域包含摘要附图占位。摘要正文仍只写专利摘要正文，不要把摘要附图提示词写入摘要正文。"
            "摘要正文之后另起一段输出“【前端附图信息】”，在该标记后写摘要附图的图号、说明、图片生成提示词或代码运行来源；"
            "该标记块仅供前端展示，系统写入 Word 前会自动移除。不要照抄模板占位文字。"
        )
        rules.append(FIGURE_CLASSIFICATION_RULES)

    return "\n".join(rules).strip()


def _type_label(writing_type: WritingType) -> str:
    labels = {
        "patent": "专利申请文件",
        "course_report": "课程报告",
        "project_plan": "项目方案",
        "technical_spec": "技术说明书",
        "general_report": "普通论文/报告",
    }
    return labels[writing_type]
