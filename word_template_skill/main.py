from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from config import ConfigError, load_deepseek_config, load_environment
from deepseek_client import DeepSeekClient, GenerationResult
from docx_reader import DocumentFacts, collect_document_facts, load_docx
from docx_writer import SectionWriteRequest, write_generated_sections
from heading_detector import HeadingNode, detect_headings, is_common_skip_title
from prompt_builder import SectionContext, build_outline, build_section_messages, infer_writing_type
from utils import (
    default_log_path,
    default_run_log_path,
    ensure_output_path,
    now_iso,
    parse_bool,
    parse_skip_sections,
    prepare_topic_workspace,
    resolve_docx_path,
    resolve_template_argument,
    sanitize_path_component,
    setup_logging,
    write_json_log,
)
from workflow_support import (
    PatentPointExtraction,
    WorkflowAnalysis,
    WorkflowAnswers,
    WorkflowPlan,
    analyze_workflow_inputs,
    build_patent_extraction_messages,
    build_five_questions,
    build_generation_plan,
    build_workflow_prompt_context,
    copy_material_files,
    format_analysis_markdown,
    format_answers_markdown,
    format_patent_points_markdown,
    format_plan_markdown,
    format_questions_markdown,
    load_answers_file,
    load_existing_materials,
    parse_patent_extraction_response,
    parse_numbered_items,
    read_text_file,
    resolve_concept_text,
    save_text_artifact,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_DIR = WORKSPACE_ROOT / "templates"
DEFAULT_TOPIC_ROOT = WORKSPACE_ROOT / "topics"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate content section-by-section from a Word template using DeepSeek.",
    )
    parser.add_argument("--template", default=None, help="Path to the input .docx template.")
    parser.add_argument(
        "--template-name",
        default=None,
        help="Name of a .docx template inside --template-dir. The .docx suffix is optional.",
    )
    parser.add_argument(
        "--template-dir",
        default=str(DEFAULT_TEMPLATE_DIR),
        help=f"Directory that stores reusable templates. Default: {DEFAULT_TEMPLATE_DIR}",
    )
    parser.add_argument("--topic", required=True, help="Topic, technical proposal, patent, or report request.")
    parser.add_argument(
        "--topic-root",
        default=str(DEFAULT_TOPIC_ROOT),
        help=f"Directory that stores per-topic workspaces. Default: {DEFAULT_TOPIC_ROOT}",
    )
    parser.add_argument(
        "--topic-folder",
        default=None,
        help="Optional folder name for this topic workspace. Defaults to a sanitized topic name.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to the generated output. If omitted, an output path is created under --topic-root.",
    )
    parser.add_argument("--model", default=None, help="DeepSeek model name. Defaults to DEEPSEEK_MODEL.")
    parser.add_argument("--temperature", type=float, default=0.3, help="Model temperature.")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens per section.")
    parser.add_argument(
        "--section-mode",
        choices=["auto", "style", "text", "all"],
        default="auto",
        help="Heading detection mode.",
    )
    parser.add_argument(
        "--overwrite",
        default="false",
        help="Whether to overwrite an existing output file. Default: false.",
    )
    parser.add_argument(
        "--skip-sections",
        default="",
        help="Comma-separated section titles to keep unchanged, in addition to cover/TOC/references/thanks.",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="DeepSeek request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="DeepSeek retry count per section.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the workflow without calling the model API. Useful for end-to-end local testing.",
    )
    parser.add_argument(
        "--workflow-mode",
        choices=["classic", "question", "generate"],
        default="classic",
        help="classic: old template-only flow; question: ingest materials and output 5 questions; generate: use materials and answers to generate patent content.",
    )
    parser.add_argument(
        "--output-mode",
        choices=["template", "draft"],
        default="template",
        help="template: write back into docx template; draft: generate markdown draft.",
    )
    parser.add_argument("--concept", default=None, help="Patent concept text.")
    parser.add_argument("--concept-file", default=None, help="Path to a text file that stores the patent concept.")
    parser.add_argument(
        "--materials",
        nargs="*",
        default=None,
        help="One or more local material files to save under topics/<topic>/materials/.",
    )
    parser.add_argument(
        "--answers-file",
        default=None,
        help="Path to a text or JSON file that provides answers to the 5 workflow questions.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    load_environment()

    template_dir = Path(args.template_dir).expanduser().resolve()
    topic_root = Path(args.topic_root).expanduser().resolve()
    overwrite = parse_bool(args.overwrite)

    topic_dir: Path | None = None
    template_path: Path | None = None
    requested_output: Path | None = None

    needs_template = args.workflow_mode == "classic" or (
        args.workflow_mode == "generate" and args.output_mode == "template"
    )

    if needs_template:
        template_path = resolve_template_argument(
            template=args.template,
            template_name=args.template_name,
            template_dir=template_dir,
        )

    if args.output:
        requested_output = resolve_docx_path(args.output)
    else:
        if template_path is not None:
            requested_output, topic_dir = prepare_topic_workspace(
                topic=args.topic,
                template_path=template_path,
                topic_root=topic_root,
                topic_folder=args.topic_folder,
            )
        else:
            topic_dir = _prepare_topic_only_workspace(args.topic, topic_root, args.topic_folder)

    if topic_dir is None:
        topic_dir = _infer_topic_dir_from_output(requested_output or Path.cwd(), args.topic, topic_root, args.topic_folder)

    notes_dir = topic_dir / "notes"
    materials_dir = topic_dir / "materials"
    outputs_dir = topic_dir / "outputs"
    notes_dir.mkdir(parents=True, exist_ok=True)
    materials_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    log_anchor = requested_output if requested_output is not None else notes_dir / f"{sanitize_path_component(args.topic)}_{args.workflow_mode}.docx"
    json_log_path = default_log_path(log_anchor)
    run_log_path = default_run_log_path(log_anchor)
    logger = setup_logging(run_log_path)

    run_log: dict[str, Any] = {
        "created_at": now_iso(),
        "workflow_mode": args.workflow_mode,
        "output_mode": args.output_mode,
        "dry_run": args.dry_run,
        "template_dir": str(template_dir),
        "topic": args.topic,
        "topic_root": str(topic_root),
        "topic_dir": str(topic_dir),
        "materials_dir": str(materials_dir),
        "notes_dir": str(notes_dir),
        "outputs_dir": str(outputs_dir),
        "model": args.model,
        "status": "started",
        "sections": [],
    }

    try:
        if args.workflow_mode == "classic":
            assert template_path is not None
            assert requested_output is not None
            output_path = ensure_output_path(template_path, requested_output, overwrite)
            run_log["template"] = str(template_path)
            run_log["output"] = str(output_path)
            return _run_template_generation(
                args=args,
                template_path=template_path,
                output_path=output_path,
                logger=logger,
                run_log=run_log,
                workflow_prompt_context="",
                json_log_path=json_log_path,
            )

        concept_text = resolve_concept_text(concept=args.concept, concept_file=args.concept_file)
        if not concept_text:
            latest_concept = notes_dir / "concept_latest.md"
            if latest_concept.exists():
                concept_text = read_text_file(latest_concept).strip()
        if not concept_text:
            raise ValueError("Workflow mode requires --concept or --concept-file, or an existing notes/concept_latest.md.")

        save_text_artifact(notes_dir, "concept", concept_text.strip() + "\n", latest_name="concept_latest.md")

        if args.materials:
            saved_materials = copy_material_files(args.materials, materials_dir)
        else:
            saved_materials = load_existing_materials(materials_dir)
        if not saved_materials:
            raise ValueError("Workflow mode requires uploaded code/material files, or existing files under topics/<topic>/materials/.")

        analysis = analyze_workflow_inputs(topic=args.topic, concept_text=concept_text, saved_materials=saved_materials)
        analysis_path = save_text_artifact(
            notes_dir,
            "materials_analysis",
            format_analysis_markdown(analysis),
            latest_name="materials_analysis_latest.md",
        )
        run_log["analysis_note"] = str(analysis_path)
        run_log["materials"] = [item.saved_path for item in saved_materials]

        extraction = _extract_patent_points(args=args, analysis=analysis, logger=logger, run_log=run_log)
        patent_points_path = save_text_artifact(
            notes_dir,
            "patent_points",
            format_patent_points_markdown(extraction),
            latest_name="patent_points_latest.md",
        )
        run_log["patent_points_note"] = str(patent_points_path)

        questions = build_five_questions(extraction)
        questions_path = save_text_artifact(
            notes_dir,
            "questions",
            format_questions_markdown(questions),
            latest_name="questions_latest.md",
        )
        run_log["questions_note"] = str(questions_path)
        run_log["questions"] = questions

        if args.workflow_mode == "question":
            run_log["status"] = "questions_generated"
            write_json_log(json_log_path, run_log)
            logger.info("Questions note: %s", questions_path)
            for index, question in enumerate(questions, start=1):
                print(f"{index}. {question}")
            return 0

        answers = _resolve_answers(args.answers_file, notes_dir)
        answers_path = save_text_artifact(
            notes_dir,
            "answers_normalized",
            format_answers_markdown(answers),
            latest_name="answers_latest.md",
        )
        plan = build_generation_plan(analysis, extraction, answers)
        plan_path = save_text_artifact(
            notes_dir,
            "generation_plan",
            format_plan_markdown(plan),
            latest_name="generation_plan_latest.md",
        )
        run_log["answers_note"] = str(answers_path)
        run_log["generation_plan_note"] = str(plan_path)

        workflow_prompt_context = build_workflow_prompt_context(
            analysis=analysis,
            extraction=extraction,
            answers=answers,
            plan=plan,
        )
        run_log["workflow_prompt_context_preview"] = workflow_prompt_context[:1500]

        if args.output_mode == "draft":
            output_path = _resolve_markdown_output(topic_dir=topic_dir, topic=args.topic, overwrite=overwrite, requested_output=args.output)
            run_log["output"] = str(output_path)
            return _run_draft_generation(
                args=args,
                output_path=output_path,
                logger=logger,
                run_log=run_log,
                workflow_prompt_context=workflow_prompt_context,
                json_log_path=json_log_path,
            )

        assert template_path is not None
        assert requested_output is not None
        output_path = ensure_output_path(template_path, requested_output, overwrite)
        run_log["template"] = str(template_path)
        run_log["output"] = str(output_path)
        return _run_template_generation(
            args=args,
            template_path=template_path,
            output_path=output_path,
            logger=logger,
            run_log=run_log,
            workflow_prompt_context=workflow_prompt_context,
            json_log_path=json_log_path,
        )
    except Exception as exc:  # noqa: BLE001
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        write_json_log(json_log_path, run_log)
        logger.exception("Run failed: %s", exc)
        if isinstance(exc, ConfigError):
            return 2
        return 1


def _run_template_generation(
    *,
    args: argparse.Namespace,
    template_path: Path,
    output_path: Path,
    logger,
    run_log: dict[str, Any],
    workflow_prompt_context: str,
    json_log_path: Path,
) -> int:
    config = load_deepseek_config(
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        retries=args.retries,
        require_api_key=not args.dry_run,
    )
    run_log["model"] = config.model

    document = load_docx(template_path)
    facts = collect_document_facts(document)
    headings = detect_headings(document.paragraphs, mode=args.section_mode)
    run_log["document_facts"] = facts.__dict__
    run_log["headings"] = [heading.to_log_dict() for heading in headings]

    if not headings:
        raise RuntimeError("No headings were detected. Use Word Heading styles or try --section-mode text/all.")

    logger.info("Detected %s heading(s):", len(headings))
    for heading in headings:
        logger.info("  [%s] paragraph %s: %s", heading.level, heading.paragraph_index, heading.title)

    writing_type = infer_writing_type(args.topic, headings)
    outline = build_outline(headings)
    client = _build_generation_client(args=args, config=config, logger=logger)
    skip_titles = parse_skip_sections(args.skip_sections)
    write_requests: list[SectionWriteRequest] = []
    figure_prompt_notes: list[str] = []

    for heading in headings:
        section_log: dict[str, Any] = {
            "title": heading.title,
            "level": heading.level,
            "paragraph_index": heading.paragraph_index,
            "status": "pending",
            "reason": "",
            "inserted_paragraphs": 0,
        }

        if is_common_skip_title(heading.title, skip_titles):
            section_log["status"] = "skipped"
            section_log["reason"] = "Common non-body section or user-specified skipped section."
            run_log["sections"].append(section_log)
            logger.info("Skipped section: %s", heading.title)
            continue

        parent_title = headings[heading.parent_index].title if heading.parent_index is not None else None
        context = SectionContext(
            heading=heading,
            parent_title=parent_title,
            previous_title=heading.previous_title,
            next_title=heading.next_title,
            outline=outline,
            writing_type=writing_type,
            document_facts=facts,
            workflow_context=workflow_prompt_context,
        )
        messages = build_section_messages(topic=args.topic, context=context)
        result = client.generate_section(section_title=heading.title, messages=messages)
        section_log["model_call"] = result.to_log_dict()

        if result.ok:
            section_log["status"] = "generated"
            cleaned_response, section_figure_notes = _extract_frontend_figure_notes(
                section_title=heading.title,
                text=result.response,
            )
            if section_figure_notes:
                figure_prompt_notes.extend(section_figure_notes)
                section_log["frontend_figure_notes"] = section_figure_notes
            write_requests.append(SectionWriteRequest(heading=heading, content=cleaned_response))
        else:
            section_log["status"] = "failed"
            section_log["reason"] = result.error or "DeepSeek returned empty content."
            logger.error("Failed section: %s - %s", heading.title, section_log["reason"])

        run_log["sections"].append(section_log)

    if write_requests:
        write_results = write_generated_sections(
            template_path=template_path,
            output_path=output_path,
            headings=headings,
            sections=write_requests,
            logger=logger,
        )
        _merge_write_results(run_log["sections"], write_results)
    else:
        logger.warning("No generated sections to write. Output file was not created.")

    if figure_prompt_notes:
        notes_dir = Path(str(run_log["notes_dir"]))
        figure_prompt_path = save_text_artifact(
            notes_dir,
            "figure_prompts",
            _format_frontend_figure_notes(args.topic, figure_prompt_notes),
            latest_name="figure_prompts_latest.md",
        )
        run_log["figure_prompts_note"] = str(figure_prompt_path)

    failed_count = sum(1 for item in run_log["sections"] if item["status"] == "failed")
    run_log["status"] = "completed_with_failures" if failed_count else "completed"
    run_log["log_file"] = str(json_log_path)
    run_log["run_log_file"] = str(default_run_log_path(output_path))
    write_json_log(json_log_path, run_log)
    logger.info("JSON log: %s", json_log_path)
    if write_requests:
        logger.info("Output: %s", output_path)
    return 1 if failed_count else 0


def _extract_patent_points(
    *,
    args: argparse.Namespace,
    analysis: WorkflowAnalysis,
    logger,
    run_log: dict[str, Any],
) -> PatentPointExtraction:
    config = load_deepseek_config(
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        retries=args.retries,
        require_api_key=not args.dry_run,
    )
    client = _build_generation_client(args=args, config=config, logger=logger)
    messages = build_patent_extraction_messages(analysis)
    result = client.generate_section(section_title="patent_point_extraction", messages=messages)
    run_log["patent_point_extraction_model_call"] = result.to_log_dict()
    if not result.ok:
        raise RuntimeError(result.error or "Patent point extraction failed.")
    extraction = parse_patent_extraction_response(result.response)
    run_log["patent_point_extraction_summary"] = extraction.summary
    run_log["patent_point_count"] = {
        "core": len(extraction.core_patent_points),
        "optional": len(extraction.optional_patent_points),
        "non_claim": len(extraction.non_claim_details),
        "questions": len(extraction.code_questions),
        "claim_mainline": len(extraction.claim_mainline),
    }
    return extraction


def _run_draft_generation(
    *,
    args: argparse.Namespace,
    output_path: Path,
    logger,
    run_log: dict[str, Any],
    workflow_prompt_context: str,
    json_log_path: Path,
) -> int:
    config = load_deepseek_config(
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        retries=args.retries,
        require_api_key=not args.dry_run,
    )
    run_log["model"] = config.model
    client = _build_generation_client(args=args, config=config, logger=logger)

    headings = _build_default_patent_headings()
    facts = DocumentFacts(paragraph_count=0, table_count=0, inline_shape_count=0, section_count=0, has_toc_like_text=False)
    outline = build_outline(headings)
    sections_md: list[str] = [f"# {args.topic}"]
    figure_prompt_notes: list[str] = []

    for heading in headings:
        section_log: dict[str, Any] = {
            "title": heading.title,
            "level": heading.level,
            "paragraph_index": heading.paragraph_index,
            "status": "pending",
            "reason": "",
        }
        parent_title = headings[heading.parent_index].title if heading.parent_index is not None else None
        context = SectionContext(
            heading=heading,
            parent_title=parent_title,
            previous_title=heading.previous_title,
            next_title=heading.next_title,
            outline=outline,
            writing_type="patent",
            document_facts=facts,
            workflow_context=workflow_prompt_context,
        )
        messages = build_section_messages(topic=args.topic, context=context)
        result = client.generate_section(section_title=heading.title, messages=messages)
        section_log["model_call"] = result.to_log_dict()
        if result.ok:
            section_log["status"] = "generated"
            cleaned_response, section_figure_notes = _extract_frontend_figure_notes(
                section_title=heading.title,
                text=result.response,
            )
            if section_figure_notes:
                figure_prompt_notes.extend(section_figure_notes)
                section_log["frontend_figure_notes"] = section_figure_notes
            sections_md.append(f"\n## {heading.title}\n")
            sections_md.append(cleaned_response.strip())
        else:
            section_log["status"] = "failed"
            section_log["reason"] = result.error or "DeepSeek returned empty content."
            logger.error("Failed section: %s - %s", heading.title, section_log["reason"])
        run_log["sections"].append(section_log)

    output_path.write_text("\n\n".join(sections_md).strip() + "\n", encoding="utf-8")
    if figure_prompt_notes:
        notes_dir = Path(str(run_log["notes_dir"]))
        figure_prompt_path = save_text_artifact(
            notes_dir,
            "figure_prompts",
            _format_frontend_figure_notes(args.topic, figure_prompt_notes),
            latest_name="figure_prompts_latest.md",
        )
        run_log["figure_prompts_note"] = str(figure_prompt_path)
    failed_count = sum(1 for item in run_log["sections"] if item["status"] == "failed")
    run_log["status"] = "completed_with_failures" if failed_count else "completed"
    run_log["log_file"] = str(json_log_path)
    write_json_log(json_log_path, run_log)
    logger.info("Draft output: %s", output_path)
    return 1 if failed_count else 0


def _resolve_answers(answers_file: str | None, notes_dir: Path) -> WorkflowAnswers:
    if answers_file:
        return load_answers_file(Path(answers_file).expanduser().resolve())
    latest = notes_dir / "answers_latest.md"
    if latest.exists():
        return load_answers_file(latest)
    raise ValueError("Generate mode requires --answers-file, or an existing notes/answers_latest.md.")


def _prepare_topic_only_workspace(topic: str, topic_root: Path, topic_folder: str | None) -> Path:
    topic_dir_name = sanitize_path_component(topic_folder or topic, fallback="topic")
    topic_dir = topic_root.expanduser().resolve() / topic_dir_name
    for folder in (topic_dir, topic_dir / "outputs", topic_dir / "materials", topic_dir / "notes"):
        folder.mkdir(parents=True, exist_ok=True)
    return topic_dir


def _infer_topic_dir_from_output(output_path: Path, topic: str, topic_root: Path, topic_folder: str | None) -> Path:
    if output_path.parent.name in {"outputs", "notes", "materials"}:
        return output_path.parent.parent
    return _prepare_topic_only_workspace(topic, topic_root, topic_folder)


def _resolve_markdown_output(*, topic_dir: Path, topic: str, overwrite: bool, requested_output: str | None) -> Path:
    if requested_output:
        output_path = resolve_docx_path(requested_output)
        if output_path.suffix.lower() != ".md":
            output_path = output_path.with_suffix(".md")
    else:
        output_path = topic_dir / "outputs" / f"{sanitize_path_component(topic)}_专利草案.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not output_path.exists():
        return output_path
    stem = output_path.stem
    suffix = output_path.suffix
    for number in range(1, 1000):
        candidate = output_path.parent / f"{stem}_{number}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free markdown output name near {output_path}")


def _build_default_patent_headings() -> list[HeadingNode]:
    titles = [
        "摘要",
        "权利要求书",
        "技术领域",
        "技术背景",
        "发明内容",
        "附图说明",
        "具体实施方式",
    ]
    headings: list[HeadingNode] = []
    for index, title in enumerate(titles):
        headings.append(
            HeadingNode(
                title=title,
                level=1,
                paragraph_index=index,
                start_index=index + 1,
                end_index=index,
                style_name="",
                paragraph=None,  # type: ignore[arg-type]
            )
        )
    for index, heading in enumerate(headings):
        heading.previous_title = headings[index - 1].title if index > 0 else None
        heading.next_title = headings[index + 1].title if index + 1 < len(headings) else None
    return headings


def _merge_write_results(section_logs: list[dict[str, Any]], write_results: list[Any]) -> None:
    by_index = {result.paragraph_index: result for result in write_results}
    for section_log in section_logs:
        result = by_index.get(section_log.get("paragraph_index"))
        if result is None:
            continue
        section_log["status"] = result.status
        section_log["inserted_paragraphs"] = result.inserted_paragraphs
        section_log["insertion_mode"] = result.insertion_mode
        if result.reason:
            section_log["reason"] = result.reason


FRONTEND_FIGURE_MARKER = "【前端附图信息】"
FIGURE_NOTE_PATTERN = re.compile(r"(图片生成提示词|图片来源)\s*[:：]\s*(.+)")
FIGURE_LABEL_PATTERN = re.compile(r"(图\s*\d+)")
ACADEMIC_FIGURE_STYLE_PROMPT = (
    "参考用户示例图的论文级技术机制图效果，不画普通单线流程图。白色背景，16:9横版，高密度多分区结构；"
    "中间放核心主流程或总架构，周围布置带小标题的功能分区，分区使用灰色或彩色虚线边框、浅色底纹和细黑线。"
    "图中包含输入、预处理/特征提取、关键算法模块、融合/判别、输出结果之间的箭头关系，模块名称必须具体。"
    "分区内部绘制小型子模块、嵌套框、符号节点、加号/乘号/门控/权重/掩码等机制元素；"
    "可嵌入小曲线图、热力图、频谱图、矩阵块、局部放大框、公式说明框或图例。"
    "配色克制：时域/主干浅蓝，频域浅橙，融合浅绿，分类或输出浅紫，重点路径用蓝色或绿色箭头，"
    "关键高亮区域可用红色虚线框。全部使用简体中文标签，字体接近思源黑体/微软雅黑，线条细，箭头清晰，"
    "布局紧凑但不拥挤，高清矢量质感，分辨率不低于3840×2160。不要3D、不要卡通、不要照片风、不要深色背景、不要装饰性海报。"
)


def _extract_frontend_figure_notes(*, section_title: str, text: str) -> tuple[str, list[str]]:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    notes: list[str] = []

    if FRONTEND_FIGURE_MARKER in normalized:
        before, _, after = normalized.partition(FRONTEND_FIGURE_MARKER)
        marker_notes = _format_figure_note_block(section_title, after.strip())
        if marker_notes:
            notes.append(marker_notes)
        normalized = before.strip()

    cleaned_lines: list[str] = []
    context_lines: list[str] = []
    for line in normalized.splitlines():
        stripped = line.strip()
        match = FIGURE_NOTE_PATTERN.search(stripped)
        if match:
            prefix = match.group(1)
            value = match.group(2).strip()
            context = _latest_figure_context(context_lines)
            notes.append(_format_figure_note_block(section_title, f"{context}\n{prefix}：{value}".strip()))
            before = stripped[: match.start()].strip()
            if before:
                cleaned_lines.append(before)
                context_lines.append(before)
            continue
        cleaned_lines.append(line.rstrip())
        if stripped:
            context_lines.append(stripped)

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, [note for note in notes if note.strip()]


def _latest_figure_context(lines: list[str]) -> str:
    for line in reversed(lines[-4:]):
        if FIGURE_LABEL_PATTERN.search(line):
            return line
    return ""


def _format_figure_note_block(section_title: str, content: str) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", (content or "").strip())
    if not cleaned:
        return ""
    return f"## {section_title}\n\n{cleaned}"


def _format_frontend_figure_notes(topic: str, notes: list[str]) -> str:
    unique_notes: list[str] = []
    seen: set[str] = set()
    for note in notes:
        normalized = note.strip()
        if not normalized or normalized in seen:
            continue
        unique_notes.append(normalized)
        seen.add(normalized)
    lines = [f"# 图片生成提示词", "", f"主题：{topic}", ""]
    lines.extend("\n\n".join(unique_notes).splitlines())
    return "\n".join(lines).strip() + "\n"


def _build_generation_client(*, args: argparse.Namespace, config, logger):
    if args.dry_run:
        return DryRunClient(config=config, logger=logger)
    return DeepSeekClient(config, logger=logger)


class DryRunClient:
    def __init__(self, config, logger) -> None:
        self.config = config
        self.logger = logger

    def generate_section(self, *, section_title: str, messages: list[dict[str, str]]) -> GenerationResult:
        self.logger.info("Dry-run generated section %s", section_title)
        content = _dry_run_content(section_title)
        return GenerationResult(
            section_title=section_title,
            prompt=messages,
            response=content,
            usage={"mode": "dry_run"},
            elapsed_seconds=0.0,
        )


def _dry_run_content(section_title: str) -> str:
    examples = {
        "patent_point_extraction": """{
  "summary": "核心创新链条是将多源振动信号经过频域特征提取、时序依赖建模与跨分支融合后完成故障识别，并将真正决定识别性能的机制与普通训练调参区分开。",
  "core_patent_points": [
    "围绕多路输入信号构建频域特征提取与时序特征建模的联合诊断流程。",
    "利用融合模块对不同分支特征进行协同加权，以增强弱故障特征保留能力。",
    "将频域分析、时序建模和融合判别按可执行步骤组织为一体化故障诊断方法。"
  ],
  "optional_patent_points": [
    "将注意力或门控机制作为融合阶段的从属保护点。",
    "将类别不平衡处理或特定损失设计作为训练侧补强点。",
    "将输入信号同步方式和中间特征输出定义为实施方式中的可选限定。"
  ],
  "non_claim_details": [
    "具体学习率、batch size、epoch 数量等训练超参数。",
    "日志打印、路径组织、配置文件命名等工程实现细节。",
    "单次实验中使用的固定随机种子或临时阈值。"
  ],
  "code_questions": [
    "本申请的独立权利要求应优先保护完整诊断流程，还是优先保护频域编码、时序建模与融合判别之间的协同机制？",
    "为了兼顾授权稳定性和保护范围，你能接受把哪些必要限定写入独立权利要求，哪些内容希望放到从属权利要求？",
    "说明书中是否需要覆盖 FFT、其他频域变换、LSTM 或其他时序单元等替代实现，以防止竞品绕开？",
    "类别不平衡处理、损失函数或训练策略在本案中是创造性的一部分，还是仅作为实施例支撑模型训练？",
    "最希望主张的技术效果是什么？请提供可用于说明书的对比对象、指标提升、弱故障/噪声工况或应用场景。"
  ],
  "claim_mainline": [
    "先定义多源信号输入及预处理，再定义频域与时序特征提取的组合步骤。",
    "再定义跨分支特征融合与故障判别步骤，突出各模块的协同关系。",
    "最后在从属权利要求中补充融合机制、训练优化和输入同步约束。"
  ]
}""",
        "摘要": (
            "本发明公开了一种用于测试工作流的占位摘要内容，用于验证资料入库、问题澄清和模板写回流程是否正常。\n\n"
            "【前端附图信息】\n\n"
            "摘要附图：图1。\n\n"
            "图1为资料驱动型文档生成流程的摘要附图。\n\n"
            "图片生成提示词：绘制资料驱动型文档生成流程示意图，包含资料输入、代码事实分析、问题澄清、答案归一化、逐章节生成、Word模板写回和输出文档等模块；"
            f"{ACADEMIC_FIGURE_STYLE_PROMPT}"
        ),
        "权利要求书": "1. 一种用于测试工作流的专利生成方法，其特征在于，包括资料入库、问题澄清、答案归一化以及模板生成步骤。\n\n2. 根据权利要求1所述的方法，其特征在于，资料入库步骤包括将代码文件保存到对应主题目录的materials中。",
        "技术领域": "本发明属于专利文本自动生成与文档模板写回技术领域。",
        "技术背景": "现有流程通常仅基于主题字符串直接生成文稿，难以充分利用代码文件和用户补充构想。",
        "发明内容": "本发明提供一种资料驱动型专利生成流程，通过先分析代码和构想，再提出关键问题并依据回答生成专利内容，提高内容一致性与可迭代性。",
        "附图说明": (
            "图1为资料驱动型专利生成工作流示意图。\n\n"
            "图1示意从资料输入、代码事实分析、问题澄清、答案归一化到逐章节生成和Word模板写回的完整流程。\n\n"
            "图片生成提示词：绘制资料驱动型专利生成工作流示意图，包含资料输入、代码事实分析、专利点提取、问题澄清、答案归一化、逐章节生成、Word模板写回和输出文档等模块；"
            f"{ACADEMIC_FIGURE_STYLE_PROMPT}\n\n"
            "图2为模型训练效果曲线示意图。\n\n"
            "图2用于展示训练过程中损失值、准确率或宏平均F1值随轮次变化的实验结果。\n\n"
            "图片来源：由代码运行生成（例如由 plot_training_history 函数输出训练曲线图片）。"
        ),
        "具体实施方式": "在一个实施例中，系统先创建主题目录并保存代码文件，再分析代码和构想，输出5个问题，在获得回答后生成专利草案并写回模板。",
        "说明书摘要": "本发明公开了一种资料驱动型专利生成方法，用于测试当前工作流的完整链路。",
        "摘要附图": (
            "图1。\n\n"
            "图1为最能代表本技术方案的整体流程示意图。\n\n"
            "图片生成提示词：绘制当前主题的整体技术方案流程示意图，模块名称和箭头关系根据主题、代码结构和章节上下文确定；"
            f"{ACADEMIC_FIGURE_STYLE_PROMPT}"
        ),
    }
    return examples.get(section_title, f"这是“{section_title}”章节的 dry-run 占位内容，用于测试状态机改造后的完整流程。")


if __name__ == "__main__":
    raise SystemExit(main())
