from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from config import ConfigError, load_deepseek_config, load_environment
from deepseek_client import DeepSeekClient, GenerationResult
from docx_reader import collect_document_facts, load_docx
from docx_writer import SectionWriteRequest, write_generated_sections
from heading_detector import DetectionMode, HeadingNode, detect_headings, is_common_skip_title
from prompt_builder import SectionContext, build_outline, build_section_messages, infer_writing_type
from utils import (
    default_log_path,
    default_run_log_path,
    ensure_output_path,
    now_iso,
    parse_bool,
    parse_skip_sections,
    resolve_docx_path,
    setup_logging,
    write_json_log,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate content section-by-section from a Word template using DeepSeek.",
    )
    parser.add_argument("--template", required=True, help="Path to the input .docx template.")
    parser.add_argument("--topic", required=True, help="Topic, technical proposal, patent, or report request.")
    parser.add_argument("--output", required=True, help="Path to the generated .docx output.")
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
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="DeepSeek request timeout in seconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="DeepSeek retry count per section.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    load_environment()

    template_path = resolve_docx_path(args.template)
    requested_output = resolve_docx_path(args.output)
    overwrite = parse_bool(args.overwrite)
    output_path = ensure_output_path(template_path, requested_output, overwrite)
    json_log_path = default_log_path(output_path)
    run_log_path = default_run_log_path(output_path)
    logger = setup_logging(run_log_path)

    run_log: dict[str, Any] = {
        "created_at": now_iso(),
        "template": str(template_path),
        "output": str(output_path),
        "topic": args.topic,
        "model": args.model,
        "section_mode": args.section_mode,
        "status": "started",
        "headings": [],
        "sections": [],
    }

    try:
        config = load_deepseek_config(
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout,
            retries=args.retries,
        )
        run_log["model"] = config.model

        document = load_docx(template_path)
        facts = collect_document_facts(document)
        headings = detect_headings(document.paragraphs, mode=args.section_mode)
        run_log["document_facts"] = facts.__dict__
        run_log["headings"] = [heading.to_log_dict() for heading in headings]

        if not headings:
            raise RuntimeError(
                "No headings were detected. Use Word Heading styles or try --section-mode text/all."
            )

        logger.info("Detected %s heading(s):", len(headings))
        for heading in headings:
            logger.info(
                "  [%s] paragraph %s: %s",
                heading.level,
                heading.paragraph_index,
                heading.title,
            )

        writing_type = infer_writing_type(args.topic, headings)
        outline = build_outline(headings)
        client = DeepSeekClient(config, logger=logger)
        skip_titles = parse_skip_sections(args.skip_sections)

        generation_results: dict[int, GenerationResult] = {}
        write_requests: list[SectionWriteRequest] = []

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
            )
            messages = build_section_messages(topic=args.topic, context=context)
            result = client.generate_section(section_title=heading.title, messages=messages)
            generation_results[heading.paragraph_index] = result
            section_log["model_call"] = result.to_log_dict()

            if result.ok:
                section_log["status"] = "generated"
                write_requests.append(SectionWriteRequest(heading=heading, content=result.response))
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

        failed_count = sum(1 for item in run_log["sections"] if item["status"] == "failed")
        run_log["status"] = "completed_with_failures" if failed_count else "completed"
        run_log["log_file"] = str(json_log_path)
        run_log["run_log_file"] = str(run_log_path)
        write_json_log(json_log_path, run_log)

        logger.info("JSON log: %s", json_log_path)
        if write_requests:
            logger.info("Output: %s", output_path)
        return 1 if failed_count else 0

    except Exception as exc:  # noqa: BLE001 - CLI should always produce a readable log
        run_log["status"] = "failed"
        run_log["error"] = str(exc)
        write_json_log(json_log_path, run_log)
        logger.exception("Run failed: %s", exc)
        if isinstance(exc, ConfigError):
            return 2
        return 1


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


if __name__ == "__main__":
    raise SystemExit(main())
