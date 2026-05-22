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
