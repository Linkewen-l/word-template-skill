from __future__ import annotations

import ast
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


QUESTION_KEYS = [
    "core_innovation_priority",
    "signal_definition",
    "module_granularity",
    "claim_scope_preference",
    "technical_effect_focus",
]


@dataclass(frozen=True)
class SavedMaterial:
    source_path: str
    saved_path: str
    file_name: str
    kind: str
    size_bytes: int


@dataclass(frozen=True)
class MaterialAnalysis:
    file_name: str
    saved_path: str
    kind: str
    summary: str
    symbols: list[str]
    highlights: list[str]


@dataclass(frozen=True)
class WorkflowAnalysis:
    topic: str
    concept_text: str
    saved_materials: list[SavedMaterial]
    material_analyses: list[MaterialAnalysis]
    concept_summary: str
    key_findings: list[str]

    def to_prompt_text(self, *, limit: int = 4000) -> str:
        parts = [
            f"主题: {self.topic}",
            "专利构想摘要:",
            self.concept_summary,
        ]
        if self.key_findings:
            parts.append("代码与构想提炼出的关键点:")
            for item in self.key_findings:
                parts.append(f"- {item}")
        if self.material_analyses:
            parts.append("资料摘要:")
            for item in self.material_analyses:
                parts.append(f"- 文件: {item.file_name}")
                parts.append(f"  类型: {item.kind}")
                parts.append(f"  摘要: {item.summary}")
                if item.symbols:
                    parts.append(f"  关键符号: {', '.join(item.symbols[:12])}")
                if item.highlights:
                    parts.append(f"  关键实现点: {'; '.join(item.highlights[:6])}")
        text = "\n".join(parts).strip()
        return text[:limit]


@dataclass(frozen=True)
class WorkflowAnswers:
    raw_items: list[str]
    normalized: dict[str, str]

    def to_prompt_text(self) -> str:
        lines: list[str] = []
        for index, key in enumerate(QUESTION_KEYS, start=1):
            value = self.normalized.get(key, "").strip() or "未提供"
            lines.append(f"{index}. {key}: {value}")
        return "\n".join(lines)


@dataclass(frozen=True)
class WorkflowPlan:
    summary: str
    independent_claim_focus: str
    dependent_claim_points: list[str]
    implementation_points: list[str]
    effects_focus: list[str]

    def to_prompt_text(self, *, limit: int = 2500) -> str:
        lines = [
            "专利生成规划:",
            f"- 独立权利要求主线: {self.independent_claim_focus}",
        ]
        if self.dependent_claim_points:
            lines.append("- 从属权利要求补强点:")
            lines.extend(f"  - {item}" for item in self.dependent_claim_points)
        if self.implementation_points:
            lines.append("- 实施方式重点:")
            lines.extend(f"  - {item}" for item in self.implementation_points)
        if self.effects_focus:
            lines.append("- 技术效果重点:")
            lines.extend(f"  - {item}" for item in self.effects_focus)
        lines.append(f"- 计划摘要: {self.summary}")
        return "\n".join(lines)[:limit]


@dataclass(frozen=True)
class PatentPointExtraction:
    summary: str
    core_patent_points: list[str]
    optional_patent_points: list[str]
    non_claim_details: list[str]
    code_questions: list[str]
    claim_mainline: list[str]
    raw_response: str = ""

    def to_prompt_text(self, *, limit: int = 3200) -> str:
        lines = ["大模型提炼的专利点:"]
        if self.summary:
            lines.append(f"- 抽取摘要: {self.summary}")
        if self.core_patent_points:
            lines.append("- 核心专利点:")
            lines.extend(f"  - {item}" for item in self.core_patent_points)
        if self.optional_patent_points:
            lines.append("- 可选专利点:")
            lines.extend(f"  - {item}" for item in self.optional_patent_points)
        if self.non_claim_details:
            lines.append("- 不建议进入权利要求的工程细节:")
            lines.extend(f"  - {item}" for item in self.non_claim_details)
        if self.claim_mainline:
            lines.append("- 后续权利要求主线:")
            lines.extend(f"  - {item}" for item in self.claim_mainline)
        return "\n".join(lines)[:limit]


def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def resolve_concept_text(*, concept: str | None, concept_file: str | None) -> str:
    if concept and concept_file:
        raise ValueError("Use either --concept or --concept-file, not both.")
    if concept:
        return concept.strip()
    if concept_file:
        return read_text_file(Path(concept_file).expanduser().resolve()).strip()
    return ""


def copy_material_files(material_paths: Iterable[str], materials_dir: Path) -> list[SavedMaterial]:
    materials_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SavedMaterial] = []
    for raw_path in material_paths:
        source = Path(raw_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Material file not found: {source}")
        target = _unique_destination(materials_dir, source.name)
        if source != target:
            shutil.copy2(source, target)
        saved.append(
            SavedMaterial(
                source_path=str(source),
                saved_path=str(target),
                file_name=target.name,
                kind=infer_material_kind(target),
                size_bytes=target.stat().st_size,
            )
        )
    return saved


def load_existing_materials(materials_dir: Path) -> list[SavedMaterial]:
    if not materials_dir.exists():
        return []
    files = sorted(path for path in materials_dir.iterdir() if path.is_file())
    return [
        SavedMaterial(
            source_path=str(path),
            saved_path=str(path),
            file_name=path.name,
            kind=infer_material_kind(path),
            size_bytes=path.stat().st_size,
        )
        for path in files
    ]


def analyze_workflow_inputs(
    *,
    topic: str,
    concept_text: str,
    saved_materials: list[SavedMaterial],
) -> WorkflowAnalysis:
    material_analyses = [_analyze_material(item) for item in saved_materials]
    concept_summary = summarize_concept_text(concept_text)
    key_findings = build_key_findings(concept_text, material_analyses)
    return WorkflowAnalysis(
        topic=topic,
        concept_text=concept_text,
        saved_materials=saved_materials,
        material_analyses=material_analyses,
        concept_summary=concept_summary,
        key_findings=key_findings,
    )


def build_patent_extraction_messages(analysis: WorkflowAnalysis) -> list[dict[str, str]]:
    system = (
        "你是一名擅长从代码实现和技术构想中提炼专利保护点的中文专利分析助手。"
        "你的任务不是复述代码，而是区分哪些实现细节值得保护、哪些只是工程常规。"
        "代码中能够直接判断的结构、输入输出、模块连接和训练细节应由你自行分析，不要再要求用户解释。"
        "只向用户追问会影响专利写作取舍的事项，例如保护范围、权利要求边界、替代实施方式和技术效果证据。"
        "你必须严格基于给定资料输出 JSON，不得输出 JSON 之外的解释。"
    )
    user = f"""请基于以下“代码事实”和“专利构想”，提炼真正值得保护的专利点。

要求：
1. 先判断哪些实现细节构成发明点，哪些只是工程实现。
2. 输出必须是一个 JSON 对象，不要加 Markdown 代码块，不要补充解释。
3. JSON 必须包含以下字段：
   - "summary": 字符串，概括最值得保护的创新链条。
   - "core_patent_points": 字符串数组，列出 3-5 个最核心、最适合进入独立权利要求或主从属体系的专利点。
   - "optional_patent_points": 字符串数组，列出 2-5 个可以作为从属权利要求或备选保护点的内容。
   - "non_claim_details": 字符串数组，列出 2-5 个不建议写入权利要求、只适合放到实施例或工程配置中的细节。
   - "code_questions": 字符串数组，输出 5 个面向专利写作决策的问题。字段名保持为 code_questions，但问题本身不要让用户解释代码细节。
   - "claim_mainline": 字符串数组，列出 3-5 条后续权利要求的主线组织思路。
4. 每个问题都要具体，优先围绕权利要求保护宽窄、哪些机制进入独立权利要求、哪些实现放入从属权利要求或实施例、替代方案覆盖、应用场景和技术效果证据。
5. 不要提出“某个网络内部结构是什么”“输入是 FFT 还是复数谱”“某个权重如何计算”这类可从代码分析得出的问题；如果代码里能判断，应直接纳入 patent_points 或 claim_mainline。
6. 不要把“有某个类/函数/常量”本身当作专利点，除非它承载了明确的技术机制。

代码事实与构想：
{analysis.to_prompt_text(limit=3800)}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_patent_extraction_response(text: str) -> PatentPointExtraction:
    payload = _extract_json_object(text)
    summary = _coerce_text(payload.get("summary"))
    core_patent_points = _coerce_text_list(payload.get("core_patent_points"))
    optional_patent_points = _coerce_text_list(payload.get("optional_patent_points"))
    non_claim_details = _coerce_text_list(payload.get("non_claim_details"))
    code_questions = _coerce_text_list(payload.get("code_questions"))
    claim_mainline = _coerce_text_list(payload.get("claim_mainline"))

    if len(code_questions) < 5:
        code_questions.extend(_fallback_questions_from_extraction(core_patent_points, claim_mainline, code_questions))
    code_questions = code_questions[:5]

    if not summary:
        summary = "围绕代码实现中的关键技术机制组织专利保护点，并区分核心发明点与工程性细节。"
    if not core_patent_points:
        raise ValueError("Patent point extraction is missing core_patent_points.")
    if not claim_mainline:
        claim_mainline = core_patent_points[:3]

    return PatentPointExtraction(
        summary=summary,
        core_patent_points=core_patent_points,
        optional_patent_points=optional_patent_points,
        non_claim_details=non_claim_details,
        code_questions=code_questions,
        claim_mainline=claim_mainline,
        raw_response=text.strip(),
    )


def build_five_questions(extraction: PatentPointExtraction) -> list[str]:
    return extraction.code_questions[:5]


def normalize_answers_from_text(text: str) -> WorkflowAnswers:
    raw_items = parse_numbered_items(text)
    if len(raw_items) < 5:
        raise ValueError("At least 5 answers are required to continue the workflow.")
    normalized = {
        QUESTION_KEYS[index]: raw_items[index].strip()
        for index in range(min(len(QUESTION_KEYS), len(raw_items)))
    }
    return WorkflowAnswers(raw_items=raw_items[:5], normalized=normalized)


def load_answers_file(path: Path) -> WorkflowAnswers:
    text = read_text_file(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        raw_items = _answers_from_json(payload)
        if len(raw_items) < 5:
            raise ValueError("JSON answers file must provide 5 answers.")
        return WorkflowAnswers(
            raw_items=raw_items[:5],
            normalized={QUESTION_KEYS[i]: raw_items[i].strip() for i in range(5)},
        )
    return normalize_answers_from_text(text)


def build_generation_plan(
    analysis: WorkflowAnalysis,
    extraction: PatentPointExtraction,
    answers: WorkflowAnswers,
) -> WorkflowPlan:
    core = answers.normalized.get("core_innovation_priority", "").strip() or (
        extraction.claim_mainline[0] if extraction.claim_mainline else extraction.summary
    )
    signal = answers.normalized.get("signal_definition", "").strip() or "保持对输入信号来源与同步方式的宽保护描述"
    granularity = answers.normalized.get("module_granularity", "").strip() or (
        extraction.core_patent_points[0] if extraction.core_patent_points else "关键模块按照代码中的技术机制进行细化"
    )
    claim_scope = answers.normalized.get("claim_scope_preference", "").strip() or (
        extraction.optional_patent_points[0]
        if extraction.optional_patent_points
        else "训练策略进入从属权利要求与实施方式"
    )
    effects = answers.normalized.get("technical_effect_focus", "").strip() or "突出弱故障、噪声鲁棒性与分类性能提升"

    dependent_points = []
    for item in [signal, granularity, claim_scope, *extraction.optional_patent_points[:3]]:
        normalized = item.strip()
        if normalized and normalized not in dependent_points:
            dependent_points.append(normalized)

    implementation_points = []
    for item in [*extraction.core_patent_points, *analysis.key_findings]:
        normalized = item.strip()
        if normalized and normalized not in implementation_points:
            implementation_points.append(normalized)
    if not implementation_points:
        implementation_points = ["结合代码中的网络结构、输入输出与训练策略撰写实施方式"]
    effects_focus = [item.strip() for item in effects.replace("；", "，").split("，") if item.strip()] or [effects]
    summary = (
        f"独立权利要求围绕“{core}”组织；从属权利要求结合{signal}、{granularity}与{claim_scope}展开；"
        f"说明书实施方式重点映射{extraction.summary}；技术效果重点为{effects}。"
    )
    return WorkflowPlan(
        summary=summary,
        independent_claim_focus=core,
        dependent_claim_points=dependent_points[:6],
        implementation_points=implementation_points[:6],
        effects_focus=effects_focus,
    )


def save_text_artifact(directory: Path, base_name: str, content: str, *, latest_name: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamped = directory / f"{base_name}_{timestamp_tag()}.md"
    stamped.write_text(content, encoding="utf-8")
    if latest_name:
        latest_path = directory / latest_name
        latest_path.write_text(content, encoding="utf-8")
    return stamped


def format_analysis_markdown(analysis: WorkflowAnalysis) -> str:
    lines = [
        "# 资料分析",
        "",
        f"主题：{analysis.topic}",
        "",
        "## 专利构想摘要",
        "",
        analysis.concept_summary,
        "",
        "## 代码与资料分析",
        "",
    ]
    for item in analysis.material_analyses:
        lines.append(f"### {item.file_name}")
        lines.append("")
        lines.append(f"- 类型：{item.kind}")
        lines.append(f"- 路径：{item.saved_path}")
        lines.append(f"- 摘要：{item.summary}")
        if item.symbols:
            lines.append(f"- 关键符号：{', '.join(item.symbols[:12])}")
        if item.highlights:
            lines.append(f"- 关键实现点：{'；'.join(item.highlights[:8])}")
        lines.append("")
    if analysis.key_findings:
        lines.append("## 候选实现特征")
        lines.append("")
        for finding in analysis.key_findings:
            lines.append(f"- {finding}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def format_patent_points_markdown(extraction: PatentPointExtraction) -> str:
    lines = [
        "# 专利点抽取",
        "",
        "## 抽取摘要",
        "",
        extraction.summary,
        "",
        "## 核心专利点",
        "",
    ]
    for item in extraction.core_patent_points:
        lines.append(f"- {item}")
    lines.extend(["", "## 可选专利点", ""])
    for item in extraction.optional_patent_points:
        lines.append(f"- {item}")
    lines.extend(["", "## 不建议写入权利要求的工程细节", ""])
    for item in extraction.non_claim_details:
        lines.append(f"- {item}")
    lines.extend(["", "## 5 个贴代码的问题", ""])
    for index, item in enumerate(extraction.code_questions[:5], start=1):
        lines.append(f"{index}. {item}")
    lines.extend(["", "## 后续权利要求主线", ""])
    for item in extraction.claim_mainline:
        lines.append(f"- {item}")
    if extraction.raw_response:
        lines.extend(["", "## 模型原始输出", "", "```json", extraction.raw_response, "```", ""])
    return "\n".join(lines)


def format_questions_markdown(questions: list[str]) -> str:
    lines = ["# 5个关键问题", ""]
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def format_answers_markdown(answers: WorkflowAnswers) -> str:
    lines = ["# 回答归一化", ""]
    for index, key in enumerate(QUESTION_KEYS, start=1):
        value = answers.normalized.get(key, "").strip() or "未提供"
        lines.append(f"{index}. {key}")
        lines.append("")
        lines.append(value)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def format_plan_markdown(plan: WorkflowPlan) -> str:
    lines = [
        "# 专利生成计划",
        "",
        f"- 独立权利要求主线：{plan.independent_claim_focus}",
        "",
        "## 从属权利要求补强点",
        "",
    ]
    for item in plan.dependent_claim_points:
        lines.append(f"- {item}")
    lines.extend(["", "## 实施方式重点", ""])
    for item in plan.implementation_points:
        lines.append(f"- {item}")
    lines.extend(["", "## 技术效果重点", ""])
    for item in plan.effects_focus:
        lines.append(f"- {item}")
    lines.extend(["", "## 计划摘要", "", plan.summary, ""])
    return "\n".join(lines)


def build_workflow_prompt_context(
    *,
    analysis: WorkflowAnalysis | None,
    extraction: PatentPointExtraction | None,
    answers: WorkflowAnswers | None,
    plan: WorkflowPlan | None,
    max_chars: int = 7000,
) -> str:
    parts: list[str] = []
    if analysis is not None:
        parts.append(analysis.to_prompt_text(limit=2600))
    if extraction is not None:
        parts.append(extraction.to_prompt_text(limit=2200))
    if answers is not None:
        parts.append("用户对5个关键问题的回答:\n" + answers.to_prompt_text())
    if plan is not None:
        parts.append(plan.to_prompt_text(limit=2500))
    text = "\n\n".join(part for part in parts if part.strip()).strip()
    return text[:max_chars]


def read_text_file(path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Could not read text file: {path}")


def parse_numbered_items(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    items: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                current.append("")
            continue
        marker = _leading_numbered_marker(stripped)
        if marker is not None:
            if current:
                items.append("\n".join(current).strip())
                current = []
            current.append(stripped[len(marker):].strip())
        else:
            current.append(stripped)
    if current:
        items.append("\n".join(current).strip())
    return [item for item in items if item]


def summarize_concept_text(text: str, *, limit: int = 900) -> str:
    cleaned = " ".join(text.replace("\r", "\n").split())
    return cleaned[:limit] if cleaned else "未提供专利构想摘要。"


def infer_material_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python_source"
    if suffix in {".md", ".txt"}:
        return "text_note"
    if suffix in {".json"}:
        return "structured_text"
    if suffix in {".csv"}:
        return "table_data"
    return suffix.lstrip(".") or "file"


def build_key_findings(concept_text: str, material_analyses: list[MaterialAnalysis]) -> list[str]:
    findings: list[str] = []
    concept_lower = concept_text.lower()
    if "fft" in concept_lower or "频域" in concept_text:
        findings.append("专利构想强调频域特征处理或频域抑噪。")
    if "lstm" in concept_lower or "时序" in concept_text:
        findings.append("专利构想强调长时序依赖建模。")
    if "融合" in concept_text or "fusion" in concept_lower:
        findings.append("专利构想强调多分支特征融合或门控加权。")
    if "focal" in concept_lower or "不平衡" in concept_text:
        findings.append("专利构想涉及不平衡样本训练优化。")

    for item in material_analyses:
        findings.extend(item.highlights[:4])

    deduped: list[str] = []
    seen: set[str] = set()
    for item in findings:
        normalized = item.strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return deduped[:12]


def _analyze_material(material: SavedMaterial) -> MaterialAnalysis:
    path = Path(material.saved_path)
    if material.kind == "python_source":
        return _analyze_python_file(path)
    text = read_text_file(path)
    compact = " ".join(text.split())
    summary = compact[:280] if compact else "文本内容为空。"
    return MaterialAnalysis(
        file_name=material.file_name,
        saved_path=material.saved_path,
        kind=material.kind,
        summary=summary,
        symbols=[],
        highlights=[],
    )


def _analyze_python_file(path: Path) -> MaterialAnalysis:
    text = read_text_file(path)
    tree = ast.parse(text)

    imports: list[str] = []
    class_names: list[str] = []
    function_names: list[str] = []
    constants: list[str] = []
    model_like_classes: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            class_names.append(node.name)
            if any(_expr_name(base).endswith("Module") for base in node.bases):
                model_like_classes.append(node.name)
        elif isinstance(node, ast.FunctionDef):
            function_names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append(target.id)

    detected_keywords = []
    for keyword in ("FFT", "LSTM", "GRU", "Transformer", "SE", "Attention", "FocalLoss", "OneCycleLR", "Fusion", "Mask"):
        if keyword.lower() in text.lower():
            detected_keywords.append(keyword)

    highlights: list[str] = []
    if model_like_classes:
        highlights.append(f"代码包含神经网络模块类：{', '.join(model_like_classes[:4])}。")
    if detected_keywords:
        highlights.append(f"实现中出现关键算法或组件：{', '.join(detected_keywords[:8])}。")
    if function_names:
        highlights.append(f"主要函数包括：{', '.join(function_names[:6])}。")
    if constants:
        highlights.append(f"存在可映射到实施方式或参数设置的常量：{', '.join(constants[:8])}。")

    summary_parts = []
    if model_like_classes:
        summary_parts.append(f"检测到模型类 {', '.join(model_like_classes[:4])}")
    if function_names:
        summary_parts.append(f"以及 {min(len(function_names), 6)} 个主要函数")
    if detected_keywords:
        summary_parts.append(f"关键词涉及 {', '.join(detected_keywords[:6])}")
    summary = "；".join(summary_parts) or "Python 源码已读取，可用于提炼专利中的算法流程与模块结构。"

    symbols = class_names[:8] + function_names[:8]
    return MaterialAnalysis(
        file_name=path.name,
        saved_path=str(path),
        kind="python_source",
        summary=summary,
        symbols=symbols[:12],
        highlights=highlights,
    )


def _answers_from_json(payload: object) -> list[str]:
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, dict):
        if "answers" in payload and isinstance(payload["answers"], list):
            return [str(item).strip() for item in payload["answers"] if str(item).strip()]
        ordered: list[str] = []
        for key in QUESTION_KEYS:
            value = payload.get(key)
            if value is not None:
                ordered.append(str(value).strip())
        if ordered:
            return ordered
    raise ValueError("Unsupported JSON answers format.")


def _collect_distinct_terms(analyses: list[MaterialAnalysis]) -> list[str]:
    terms: list[str] = []
    for item in analyses:
        for symbol in item.symbols:
            if symbol not in terms:
                terms.append(symbol)
    return terms


def _extract_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    candidates = [stripped]

    if "```" in stripped:
        for chunk in stripped.split("```"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.startswith("json"):
                candidates.append(chunk[4:].strip())
            else:
                candidates.append(chunk)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Patent point extraction response is not valid JSON.")


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip(" -") for item in text.replace("；", "\n").replace(";", "\n").splitlines() if item.strip(" -")]


def _fallback_questions_from_extraction(
    core_patent_points: list[str],
    claim_mainline: list[str],
    existing_questions: list[str],
) -> list[str]:
    questions: list[str] = []
    anchors = core_patent_points[:3] + claim_mainline[:2]
    templates = [
        "围绕“{anchor}”，你希望独立权利要求采用较宽的功能性概括，还是限定为较具体的步骤/模块组合？",
        "“{anchor}”中哪些机制必须作为发明必要特征写入独立权利要求，哪些更适合下放到从属权利要求？",
        "为覆盖竞品绕开，“{anchor}”应当包含哪些替代实现或等同方案，哪些工程细节只放入实施例即可？",
        "“{anchor}”最希望主张的技术效果是什么，现有材料中是否有对比实验、指标提升或适用工况可支撑？",
        "如果审查员认为相关模型或训练策略属于常规手段，你希望突出哪一段协同机制来证明创造性？",
    ]
    for anchor, template in zip(anchors, templates):
        question = template.format(anchor=anchor)
        if question not in existing_questions and question not in questions:
            questions.append(question)
    generic_questions = [
        "本申请最重要的保护目标是方法流程、模型结构、训练策略、诊断系统，还是其组合？请按优先级说明。",
        "独立权利要求应追求宽保护还是稳授权？如果需要稳授权，哪些限定特征可以接受写进去？",
        "哪些内容希望作为从属权利要求层层保护，哪些仅作为实施例用于支撑说明书充分公开？",
        "为防止他人改用等同模型或替代特征提取方式绕开，说明书中应覆盖哪些替代实现？",
        "最能体现创造性的技术效果是什么？请提供可写入说明书的实验指标、对比对象或应用场景。",
    ]
    for question in generic_questions:
        if question not in existing_questions and question not in questions:
            questions.append(question)
    return questions


def _expr_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr_name(node.value)
        return f"{base}.{node.attr}".strip(".")
    return ""


def _leading_numbered_marker(text: str) -> str | None:
    prefixes = []
    for number in range(1, 10):
        prefixes.extend([f"{number}.", f"{number}、", f"{number})", f"{number}）", f"问题{number}", f"Q{number}"])
    for prefix in prefixes:
        if text.startswith(prefix):
            return prefix
    return None


def _unique_destination(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for number in range(1, 1000):
        alt = directory / f"{stem}_{number}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError(f"Could not find free material file name for {file_name}")
