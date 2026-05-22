from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def resolve_docx_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def sanitize_path_component(value: str, fallback: str = "item", limit: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    cleaned = cleaned.strip("._ ")
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:limit].rstrip("._ ")
    return cleaned or fallback


def resolve_template_from_library(template_name: str, template_dir: Path) -> Path:
    base_dir = template_dir.expanduser().resolve()
    name_path = Path(template_name)
    candidate = (base_dir / name_path).resolve()

    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(f"Template name must stay inside template directory: {base_dir}") from exc

    if candidate.suffix and candidate.suffix.lower() != ".docx":
        raise ValueError(f"Template must be a .docx file: {candidate.name}")
    if not candidate.suffix:
        candidate = candidate.with_suffix(".docx")

    if not candidate.exists():
        available = sorted(path.name for path in base_dir.glob("*.docx")) if base_dir.exists() else []
        hint = f" Available templates: {compact_list(available)}" if available else " No .docx templates found."
        raise FileNotFoundError(f"Template not found: {candidate}.{hint}")

    return candidate


def resolve_template_argument(
    *,
    template: str | None,
    template_name: str | None,
    template_dir: Path,
) -> Path:
    if template and template_name:
        raise ValueError("Use either --template or --template-name, not both.")
    if template:
        return resolve_docx_path(template)
    if not template_name:
        raise ValueError("Provide --template or --template-name.")
    return resolve_template_from_library(template_name, template_dir)


def prepare_topic_workspace(
    *,
    topic: str,
    template_path: Path,
    topic_root: Path,
    topic_folder: str | None = None,
) -> tuple[Path, Path]:
    topic_dir_name = sanitize_path_component(topic_folder or topic, fallback="topic")
    topic_dir = topic_root.expanduser().resolve() / topic_dir_name
    output_dir = topic_dir / "outputs"

    for folder in (topic_dir, output_dir, topic_dir / "materials", topic_dir / "notes"):
        folder.mkdir(parents=True, exist_ok=True)

    topic_part = sanitize_path_component(topic, fallback="topic")
    template_part = sanitize_path_component(template_path.stem, fallback="template")
    return output_dir / f"{topic_part}_{template_part}.docx", topic_dir


def ensure_output_path(template_path: Path, requested_output: Path, overwrite: bool) -> Path:
    if requested_output.suffix.lower() != ".docx":
        raise ValueError(f"Output must be a .docx file: {requested_output}")
    if requested_output.resolve() == template_path.resolve():
        raise ValueError("Output path must not be the same as the template path.")
    requested_output.parent.mkdir(parents=True, exist_ok=True)

    if overwrite or not requested_output.exists():
        return requested_output

    stem = requested_output.stem
    suffix = requested_output.suffix
    parent = requested_output.parent
    for number in range(1, 1000):
        candidate = parent / f"{stem}_{number}{suffix}"
        if candidate.resolve() == template_path.resolve():
            continue
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free output file name near {requested_output}")


def default_log_path(output_path: Path) -> Path:
    return output_path.with_suffix(".log.json")


def default_run_log_path(output_path: Path) -> Path:
    return output_path.with_suffix(".run.log")


def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("word_template_skill")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream_handler)

    return logger


def write_json_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_skip_sections(raw: str | None) -> set[str]:
    if not raw:
        return set()
    parts = re.split(r"[,，;；\n]+", raw)
    return {part.strip() for part in parts if part.strip()}


def compact_list(items: Iterable[str], limit: int = 80) -> str:
    text = ", ".join(items)
    return text if len(text) <= limit else text[: limit - 3] + "..."
