from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .workflow_runner import (
    TOPIC_ROOT,
    collect_artifacts,
    collect_topic_files,
    ensure_topic_workspace,
    is_safe_topic_artifact,
    list_templates,
    list_topics,
    run_generate_workflow,
    run_question_workflow,
)

JobStatus = Literal["pending", "running", "waiting_answers", "completed", "failed"]


@dataclass
class Job:
    id: str
    topic: str
    status: JobStatus = "pending"
    stage: str = "queued"
    message: str = "Queued."
    dry_run: bool = False
    output_mode: str | None = None
    template_name: str | None = None
    topic_dir: str | None = None
    questions: list[str] = field(default_factory=list)
    artifacts: list[dict[str, str | int | float | bool]] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class AnswerRequest(BaseModel):
    answers: list[str] = Field(min_length=5, max_length=5)
    output_mode: Literal["draft", "template"] = "template"
    template_name: str | None = None
    dry_run: bool | None = None


app = FastAPI(title="Word Template Skill API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


@app.get("/api/templates")
def api_templates() -> list[dict[str, str | int | float]]:
    return list_templates()


@app.get("/api/topics")
def api_topics() -> list[dict[str, str | float | bool]]:
    return list_topics()


@app.get("/api/topics/detail")
def api_topic_detail(name: str) -> dict[str, object]:
    try:
        return collect_topic_files(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs")
def api_jobs() -> list[dict[str, object]]:
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda item: item.updated_at, reverse=True)
    return [_job_payload(job) for job in jobs]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict[str, object]:
    return _job_payload(_get_job(job_id))


@app.post("/api/workflows/questions")
async def api_start_questions(
    topic: str = Form(...),
    concept: str = Form(...),
    template_name: str | None = Form(default=None),
    dry_run: bool = Form(default=False),
    materials: list[UploadFile] = File(default=[]),
) -> dict[str, str]:
    topic = topic.strip()
    concept = concept.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required.")
    if not concept:
        raise HTTPException(status_code=400, detail="concept is required.")
    if not materials:
        raise HTTPException(status_code=400, detail="At least one material file is required.")

    topic_dir = ensure_topic_workspace(topic)
    await _save_materials(materials, topic_dir / "materials")

    job = Job(
        id=str(uuid.uuid4()),
        topic=topic,
        dry_run=dry_run,
        template_name=template_name,
        topic_dir=str(topic_dir),
        stage="question",
        message="正在生成 5 个专利写作问题。",
    )
    _put_job(job)

    loop = asyncio.get_running_loop()
    loop.run_in_executor(_executor, _run_questions_job, job.id, topic, concept, dry_run)
    return {"job_id": job.id}


@app.post("/api/workflows/{job_id}/answers")
async def api_submit_answers(job_id: str, request: AnswerRequest) -> dict[str, str]:
    job = _get_job(job_id)
    if job.status not in {"waiting_answers", "failed", "completed"}:
        raise HTTPException(status_code=409, detail=f"Job is not waiting for answers: {job.status}")
    if request.output_mode == "template" and not request.template_name:
        raise HTTPException(status_code=400, detail="template_name is required for template output.")

    dry_run = job.dry_run if request.dry_run is None else request.dry_run
    _update_job(
        job_id,
        status="pending",
        stage="generate",
        message="已提交最终 Word 文档生成。",
        output_mode=request.output_mode,
        template_name=request.template_name,
        dry_run=dry_run,
        error=None,
    )

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        _executor,
        _run_generate_job,
        job_id,
        job.topic,
        request.answers,
        request.output_mode,
        request.template_name,
        dry_run,
    )
    return {"job_id": job_id}


@app.get("/api/artifacts/download")
def api_download(path: str) -> FileResponse:
    candidate = Path(path)
    if not is_safe_topic_artifact(candidate):
        raise HTTPException(status_code=403, detail="Only artifacts under the topics directory can be downloaded.")
    return FileResponse(candidate.resolve(), filename=candidate.name)


async def _save_materials(files: list[UploadFile], materials_dir: Path) -> None:
    materials_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        if not upload.filename:
            continue
        target = _unique_destination(materials_dir, Path(upload.filename).name)
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                handle.write(chunk)
        await upload.close()


def _run_questions_job(job_id: str, topic: str, concept: str, dry_run: bool) -> None:
    _update_job(job_id, status="running", stage="question", message="正在分析材料并提炼专利写作问题。")
    try:
        result = run_question_workflow(topic=topic, concept=concept, dry_run=dry_run)
        if result.exit_code != 0:
            detail = result.error or f"Question workflow failed with exit code {result.exit_code}."
            raise RuntimeError(detail)
        _update_job(
            job_id,
            status="waiting_answers",
            stage="waiting_answers",
            message="5 个专利写作问题已生成。",
            questions=result.questions,
            artifacts=result.artifacts,
            topic_dir=str(result.topic_dir),
        )
    except Exception as exc:  # noqa: BLE001
        _mark_failed(job_id, exc)


def _run_generate_job(
    job_id: str,
    topic: str,
    answers: list[str],
    output_mode: Literal["draft", "template"],
    template_name: str | None,
    dry_run: bool,
) -> None:
    _update_job(job_id, status="running", stage="generate", message="正在生成最终文档。")
    try:
        result = run_generate_workflow(
            topic=topic,
            answers=answers,
            output_mode=output_mode,
            template_name=template_name,
            dry_run=dry_run,
        )
        if result.exit_code != 0:
            detail = result.error or f"Generate workflow failed with exit code {result.exit_code}."
            raise RuntimeError(detail)
        _update_job(
            job_id,
            status="completed",
            stage="completed",
            message="最终文档已生成。",
            questions=result.questions,
            artifacts=result.artifacts,
            topic_dir=str(result.topic_dir),
        )
    except Exception as exc:  # noqa: BLE001
        _mark_failed(job_id, exc)


def _mark_failed(job_id: str, exc: Exception) -> None:
    job = _get_job(job_id)
    artifacts = collect_artifacts(Path(job.topic_dir)) if job.topic_dir else []
    _update_job(
        job_id,
        status="failed",
        stage="failed",
        message="Workflow failed.",
        error=str(exc),
        artifacts=artifacts,
    )


def _job_payload(job: Job) -> dict[str, object]:
    payload = asdict(job)
    artifacts = job.artifacts
    progress_artifacts = artifacts
    if job.topic_dir:
        topic_dir = Path(job.topic_dir)
        if topic_dir.exists():
            started_at = _job_started_at(job)
            progress_artifacts = collect_artifacts(topic_dir, since=started_at)
            artifacts = collect_artifacts(topic_dir, since=started_at, public_only=True)
            payload["artifacts"] = artifacts
    payload["progress"] = _build_progress(job, progress_artifacts)
    return payload


def _job_started_at(job: Job) -> datetime | None:
    try:
        return datetime.fromisoformat(job.created_at)
    except ValueError:
        return None


def _build_progress(job: Job, artifacts: list[dict[str, str | int | float | bool]]) -> dict[str, object]:
    names = {str(item.get("name", "")) for item in artifacts}
    output_count = sum(1 for item in artifacts if item.get("kind") == "outputs")

    if job.stage in {"generate", "completed"} or job.output_mode:
        definitions = [
            ("answers", "整理 5 个回答", bool(names & {"answers_from_ui.md", "answers_latest.md"})),
            ("plan", "生成专利写作计划", "generation_plan_latest.md" in names),
            ("sections", "逐节生成正文", output_count > 0 or any("_generate.log.json" in name for name in names)),
            ("save", "保存输出和日志", job.status == "completed"),
        ]
    else:
        definitions = [
            ("materials", "保存上传材料", bool(job.topic_dir)),
            ("analysis", "分析材料内容", "materials_analysis_latest.md" in names),
            ("patent_points", "抽取专利点", "patent_points_latest.md" in names),
            ("questions", "生成 5 个专利写作问题", "questions_latest.md" in names or bool(job.questions)),
            ("waiting", "等待填写回答", job.status in {"waiting_answers", "completed"}),
        ]

    first_pending_index = next((index for index, item in enumerate(definitions) if not item[2]), len(definitions))
    steps: list[dict[str, str]] = []
    for index, (step_id, label, done) in enumerate(definitions):
        if job.status == "failed" and index == first_pending_index:
            status = "failed"
        elif done:
            status = "done"
        elif job.status in {"pending", "running"} and index == first_pending_index:
            status = "active"
        else:
            status = "pending"
        steps.append({"id": step_id, "label": label, "status": status})

    done_count = sum(1 for _, _, done in definitions if done)
    if job.status == "completed":
        percent = 100
    elif job.status == "failed":
        percent = max(5, round(done_count / len(definitions) * 100))
    else:
        percent = max(8, min(95, round((done_count + 0.35) / len(definitions) * 100)))

    current_step = next((step["label"] for step in steps if step["status"] in {"active", "failed"}), steps[-1]["label"])
    recent_artifacts = sorted(
        [item for item in artifacts if str(item.get("kind", "")) == "outputs"],
        key=lambda item: str(item.get("updated_at", "")),
        reverse=True,
    )[:5]

    return {
        "percent": percent,
        "current_step": current_step,
        "steps": steps,
        "artifact_count": len(artifacts),
        "output_count": output_count,
        "recent_artifacts": recent_artifacts,
    }


def _get_job(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job


def _put_job(job: Job) -> None:
    with _jobs_lock:
        _jobs[job.id] = job


def _update_job(job_id: str, **changes: object) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now().isoformat(timespec="seconds")


def _unique_destination(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        alternative = directory / f"{stem}_{index}{suffix}"
        if not alternative.exists():
            return alternative
    raise RuntimeError(f"Could not find a free file name for {file_name}.")
