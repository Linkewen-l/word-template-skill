from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

MODULE_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = MODULE_DIR.parents[0]
TEMPLATE_DIR = WORKSPACE_ROOT / "templates"
TOPIC_ROOT = WORKSPACE_ROOT / "topics"

if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from main import main as cli_main  # noqa: E402
from utils import sanitize_path_component  # noqa: E402
from workflow_support import parse_numbered_items  # noqa: E402

OutputMode = Literal["draft", "template"]


@dataclass(frozen=True)
class WorkflowResult:
    exit_code: int
    topic_dir: Path
    questions: list[str]
    artifacts: list[dict[str, str | int | float | bool]]
    error: str | None = None


def topic_dir_for(topic: str) -> Path:
    return TOPIC_ROOT / sanitize_path_component(topic, fallback="topic")


def ensure_topic_workspace(topic: str) -> Path:
    topic_dir = topic_dir_for(topic)
    for child in ("materials", "notes", "outputs"):
        (topic_dir / child).mkdir(parents=True, exist_ok=True)
    return topic_dir


def list_templates() -> list[dict[str, str | int | float]]:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    templates: list[dict[str, str | int | float]] = []
    for path in sorted(TEMPLATE_DIR.glob("*.docx")):
        stat = path.stat()
        templates.append(
            {
                "name": path.stem,
                "file_name": path.name,
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return templates


def list_topics() -> list[dict[str, str | float | bool]]:
    if not TOPIC_ROOT.exists():
        return []
    topics: list[dict[str, str | float | bool]] = []
    for path in sorted(TOPIC_ROOT.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        stat = path.stat()
        notes_dir = path / "notes"
        outputs_dir = path / "outputs"
        topics.append(
            {
                "name": path.name,
                "path": str(path),
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "has_notes": notes_dir.exists() and any(notes_dir.iterdir()),
                "has_outputs": outputs_dir.exists() and any(outputs_dir.iterdir()),
            }
        )
    return topics


def run_question_workflow(*, topic: str, concept: str, dry_run: bool) -> WorkflowResult:
    topic_dir = ensure_topic_workspace(topic)
    argv = [
        "--workflow-mode",
        "question",
        "--topic",
        topic,
        "--concept",
        concept,
        "--topic-root",
        str(TOPIC_ROOT),
    ]
    if dry_run:
        argv.append("--dry-run")

    exit_code = cli_main(argv)
    questions = _load_questions(topic_dir)
    return WorkflowResult(
        exit_code=exit_code,
        topic_dir=topic_dir,
        questions=questions,
        artifacts=collect_artifacts(topic_dir),
        error=_latest_error(topic_dir, "question"),
    )


def run_generate_workflow(
    *,
    topic: str,
    answers: list[str],
    output_mode: OutputMode,
    template_name: str | None,
    dry_run: bool,
) -> WorkflowResult:
    topic_dir = ensure_topic_workspace(topic)
    answers_file = topic_dir / "notes" / "answers_from_ui.md"
    answers_file.write_text(_format_answers(answers), encoding="utf-8")

    argv = [
        "--workflow-mode",
        "generate",
        "--topic",
        topic,
        "--answers-file",
        str(answers_file),
        "--output-mode",
        output_mode,
        "--topic-root",
        str(TOPIC_ROOT),
    ]
    if output_mode == "template":
        if not template_name:
            raise ValueError("template_name is required when output_mode is template.")
        argv.extend(["--template-name", template_name])
    if dry_run:
        argv.append("--dry-run")

    exit_code = cli_main(argv)
    return WorkflowResult(
        exit_code=exit_code,
        topic_dir=topic_dir,
        questions=_load_questions(topic_dir),
        artifacts=collect_artifacts(topic_dir),
        error=_latest_error(topic_dir, "generate"),
    )


def collect_artifacts(
    topic_dir: Path,
    *,
    since: datetime | None = None,
    public_only: bool = False,
) -> list[dict[str, str | int | float | bool]]:
    topic_dir = topic_dir.resolve()
    artifacts: list[dict[str, str | int | float | bool]] = []
    since_timestamp = since.timestamp() if since is not None else None
    for folder_name in ("outputs", "notes"):
        folder = topic_dir / folder_name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            stat = path.stat()
            if since_timestamp is not None and stat.st_mtime < since_timestamp:
                continue
            if public_only and not _is_public_artifact(path, folder_name):
                continue
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "kind": folder_name,
                    "size_bytes": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "downloadable": path.suffix.lower() in {".docx", ".md", ".json", ".log"},
                }
            )
    return artifacts


def _is_public_artifact(path: Path, kind: str) -> bool:
    if kind != "outputs":
        return False
    return path.suffix.lower() == ".docx"


def collect_topic_files(topic_name: str) -> dict[str, object]:
    topic_dir = _resolve_topic_folder(topic_name)
    return {
        "name": topic_dir.name,
        "path": str(topic_dir),
        "materials": _collect_files(topic_dir / "materials", "materials"),
        "notes": _collect_files(topic_dir / "notes", "notes"),
        "outputs": _collect_files(topic_dir / "outputs", "outputs"),
    }


def is_safe_topic_artifact(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        resolved.relative_to(TOPIC_ROOT.resolve())
    except ValueError:
        return False
    return resolved.is_file()


def _resolve_topic_folder(topic_name: str) -> Path:
    candidate = (TOPIC_ROOT / topic_name).resolve()
    try:
        candidate.relative_to(TOPIC_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Topic must stay inside the topics directory.") from exc
    if not candidate.is_dir():
        raise FileNotFoundError(f"Topic not found: {topic_name}")
    return candidate


def _collect_files(folder: Path, kind: str) -> list[dict[str, str | int | float | bool]]:
    if not folder.exists():
        return []
    files: list[dict[str, str | int | float | bool]] = []
    for path in sorted(folder.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "kind": kind,
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "downloadable": path.suffix.lower() in {".docx", ".md", ".json", ".log", ".txt", ".py"},
            }
        )
    return files


def _load_questions(topic_dir: Path) -> list[str]:
    latest = topic_dir / "notes" / "questions_latest.md"
    if not latest.exists():
        return []
    questions = parse_numbered_items(latest.read_text(encoding="utf-8"))
    return [item for item in questions if not item.lstrip().startswith("#")][:5]


def _format_answers(answers: list[str]) -> str:
    lines: list[str] = []
    for index, answer in enumerate(answers[:5], start=1):
        lines.append(f"{index}. {answer.strip()}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _latest_error(topic_dir: Path, mode: str) -> str | None:
    notes_dir = topic_dir / "notes"
    if not notes_dir.exists():
        return None
    logs = sorted(notes_dir.glob(f"*_{mode}.log.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not logs:
        return None
    try:
        import json

        payload = json.loads(logs[0].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    error = payload.get("error")
    return str(error) if error else None
