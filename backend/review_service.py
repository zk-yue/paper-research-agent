"""Background review jobs with durable stages and resumable section checkpoints."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Event

from langchain_core.messages import HumanMessage, SystemMessage

from backend.prompt_log import record_prompt
from backend.schemas import TaskInfo
from backend.task_manager import task_manager
from backend.task_paths import get_task_root
from codex_llm import CodexCLIChat
from llm import get_llm, model_metadata
from storage import atomic_write_json, contained_path, validate_identifier

STAGES = [
    ("evidence", "准备原文证据"),
    ("planning", "组织主题与提纲"),
    ("writing", "分章撰写"),
    ("auditing", "核对论证与引用"),
    ("synthesizing", "跨章节综合"),
    ("final_audit", "终稿证据审校"),
    ("complete", "完成与导出"),
]
RELATED_WORK_STAGES = [
    ("evidence", "准备论文资料"),
    ("planning", "组织主题与引用"),
    ("writing", "撰写 Related Work"),
    ("auditing", "核对引用使用"),
    ("complete", "完成与导出"),
]
INPUT_VERSION = 1
logger = logging.getLogger(__name__)
_jobs: dict[tuple[str, str], asyncio.Task] = {}
_cancellations: dict[tuple[str, str], Event] = {}


class ReviewCheckpointError(ValueError):
    """A saved input or execution checkpoint cannot be safely reused."""


def _snapshot_digest(snapshot):
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _save_input_snapshot(task, review):
    # Only retrieval inputs belong here. In particular, never copy generated
    # reviews (which may themselves refer to older input snapshots).
    source = task.model_dump(exclude={"results"})
    source["results"] = {
        key: copy.deepcopy(task.results[key])
        for key in ("screening", "topic_plan", "coverage_status")
        if key in (task.results or {})
    }
    snapshot = {
        "version": INPUT_VERSION,
        "task_id": task.id,
        "task": source,
        "review": {
            key: copy.deepcopy(review[key])
            for key in ("paper_indices", "instructions", "template_content", "mode", "language")
        },
    }
    atomic_write_json(review_root(task.id, review["id"]) / "input.json", snapshot)
    return {"version": INPUT_VERSION, "file": "input.json", "sha256": _snapshot_digest(snapshot)}


def _read_checkpoint(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewCheckpointError(f"无法读取综述检查点 {path.name}：{error}") from error


def _load_input_snapshot(task_id, review, root):
    metadata = review.get("input_snapshot")
    if not isinstance(metadata, dict):
        raise ReviewCheckpointError("综述缺少原始输入快照，无法安全恢复；请新建综述")
    snapshot = _read_checkpoint(root / "input.json")
    if not isinstance(snapshot, dict) or snapshot.get("version") != INPUT_VERSION:
        raise ReviewCheckpointError("综述输入快照版本无效；请新建综述")
    if metadata.get("sha256") != _snapshot_digest(snapshot):
        raise ReviewCheckpointError("综述输入快照校验失败；请新建综述")
    try:
        task = TaskInfo.model_validate(snapshot["task"])
        options = snapshot["review"]
        indices = options["paper_indices"]
        valid = (
            snapshot["task_id"] == task_id == task.id
            and isinstance(indices, list)
            and bool(indices)
            and all(type(i) is int and 0 <= i < len(task.papers) for i in indices)
            and len(set(indices)) == len(indices)
            and options["mode"] in {"survey", "related_work"}
            and options["language"] in {"zh", "en"}
            and isinstance(options["instructions"], str)
            and isinstance(options["template_content"], str)
        )
        if not valid:
            raise ValueError("任务、论文选择或写作配置无效")
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewCheckpointError(f"综述输入快照无效：{error}") from error
    return task, options


def _load_model_calls(path):
    if not path.exists():
        return []
    calls = _read_checkpoint(path)
    if not isinstance(calls, list) or any(not isinstance(call, dict) for call in calls):
        raise ReviewCheckpointError("综述检查点 model-calls.json 必须为调用记录列表")
    return calls


def review_root(task_id, review_id) -> Path:
    return contained_path(get_task_root(task_id), "reviews", validate_identifier(review_id))


def get_review(task_id, review_id):
    task = task_manager.get_task(task_id)
    if task is None:
        raise KeyError("Task not found")
    review = next(
        (r for r in (task.results or {}).get("custom_reviews", []) if r["id"] == review_id), None
    )
    if review is None:
        raise KeyError("Review not found")
    return review


def update_review(task_id, review_id, patch=None, **kwargs):
    patch = {**(patch or {}), **kwargs}
    with task_manager._lock:
        task = task_manager.get_task(task_id)
        if task is None:
            return
        review = get_review(task_id, review_id)
        review.update(copy.deepcopy(patch), updated_at=datetime.now().isoformat())
        review["resumable"] = (
            review["status"] in {"failed", "cancelled", "interrupted"}
            and review.get("mode") == "survey"
            and not review.get("checkpoint_error")
        )
        stages = RELATED_WORK_STAGES if review.get("mode") == "related_work" else STAGES
        position = next((i for i, (key, _) in enumerate(stages) if key == review.get("stage")), 0)
        review["stages"] = [
            {
                "key": key,
                "label": label,
                "status": "done"
                if review["status"] == "done" or i < position
                else (
                    "failed"
                    if review["status"] == "failed"
                    else "interrupted"
                    if review["status"] in {"cancelled", "interrupted"}
                    else "running"
                )
                if i == position
                else "pending",
                "detail": review.get("message", "") if i == position else "",
            }
            for i, (key, label) in enumerate(stages)
        ]
        task_manager.update_task(task_id, results=task.results)
        atomic_write_json(review_root(task_id, review_id) / "state.json", review)


def _model_info(model):
    return model_metadata(model)


def start_review(
    task_id, paper_indices, instructions="", template_content="", mode="survey", language="zh"
):
    task = task_manager.get_task(task_id)
    if task is None:
        raise KeyError("Task not found")
    indices = list(dict.fromkeys(paper_indices or range(len(task.papers))))
    if not indices or any(i < 0 or i >= len(task.papers) for i in indices):
        raise ValueError("请选择有效的论文")
    # Avoid concurrent rewrites of the same research workspace and duplicate clicks.
    if any(key[0] == task_id and not job.done() for key, job in _jobs.items()):
        raise RuntimeError("本任务已有综述正在生成，请先完成或停止")
    model = get_llm()
    review_id = uuid.uuid4().hex[:12]
    review = {
        "id": review_id,
        "mode": mode,
        "language": language,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "paper_indices": indices,
        "paper_titles": [task.papers[i].title for i in indices],
        "instructions": instructions,
        "template_content": template_content,
        "content": "",
        "status": "queued",
        "stage": "evidence",
        "message": "综述任务已创建",
        "sections": [],
        "progress": {"completed": 0, "total": len(indices)},
        "resumable": False,
        "model": _model_info(model),
    }
    with task_manager._lock:
        review["input_snapshot"] = _save_input_snapshot(task, review)
        reviews = list((task.results or {}).get("custom_reviews", []))
        reviews.append(review)
        task_manager.update_task(
            task_id, results={**(task.results or {}), "custom_reviews": reviews}
        )
    update_review(task_id, review_id)
    _launch(task_id, review_id, model)
    return review_id


def _launch(task_id, review_id, model):
    key = (task_id, review_id)
    event = Event()
    _cancellations[key] = event
    job = asyncio.create_task(
        _execute(task_id, review_id, model, event), name=f"review-{task_id}-{review_id}"
    )
    _jobs[key] = job

    def finished(done):
        _jobs.pop(key, None)
        _cancellations.pop(key, None)
        if not done.cancelled():
            error = done.exception()
            if error is not None:
                logger.error(
                    "Cannot persist review job %s/%s failure: %s", task_id, review_id, error
                )

    job.add_done_callback(finished)


async def _execute(task_id, review_id, model, event):
    calls = []
    calls_path = None

    def update(patch=None, **kwargs):
        update_review(task_id, review_id, patch, **kwargs)

    async def invoke(system, user, label):
        if event.is_set():
            raise asyncio.CancelledError
        entry = record_prompt(task_id, f"综述/{label}", system, user, status="running")
        task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": entry})
        call = {
            "id": len(calls) + 1,
            "stage": label,
            "started_at": datetime.now().isoformat(),
            "system": system,
            "user": user,
            "status": "running",
        }
        calls.append(call)
        atomic_write_json(calls_path, calls)
        started = time.monotonic()
        try:
            messages = [SystemMessage(content=system), HumanMessage(content=user)]
            response = await asyncio.to_thread(
                model.invoke,
                messages,
                **({"cancel_event": event} if isinstance(model, CodexCLIChat) else {}),
            )
            if event.is_set():
                raise asyncio.CancelledError
            content = response.content
            call.update(
                status="done",
                response=content,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            record_prompt(
                task_id,
                f"综述/{label}",
                "",
                "",
                content,
                call["duration_ms"],
                "success",
                entry_id=entry["id"],
            )
            return content
        except BaseException as exc:
            call.update(
                status="cancelled" if event.is_set() else "failed",
                error=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            record_prompt(
                task_id,
                f"综述/{label}",
                "",
                "",
                str(exc),
                call["duration_ms"],
                "error",
                entry_id=entry["id"],
            )
            if event.is_set():
                raise asyncio.CancelledError from exc
            raise
        finally:
            atomic_write_json(calls_path, calls)

    try:
        root = review_root(task_id, review_id)
        review = copy.deepcopy(get_review(task_id, review_id))
        task, options = _load_input_snapshot(task_id, review, root)
        review.update(options)
        calls_path = root / "model-calls.json"
        calls = _load_model_calls(calls_path)
        if event.is_set():
            raise asyncio.CancelledError
        update(
            **options,
            paper_titles=[task.papers[i].title for i in options["paper_indices"]],
            status="running",
            error=None,
            checkpoint_error=None,
            message="准备原文证据",
        )
        if review["mode"] == "related_work":
            from backend.review_writer import write_review

            await write_review(
                task_id,
                review["paper_indices"],
                review["instructions"]
                + ("\n请用中文撰写。" if review["language"] == "zh" else "\nWrite in English."),
                review.get("template_content", ""),
                review_id=review_id,
                cancel_event=event,
                task_snapshot=task,
                on_progress=update,
            )
        else:
            from backend.survey_writer import generate_survey

            await generate_survey(task, review, root, invoke, update, event.is_set)
        if event.is_set():
            raise asyncio.CancelledError
        update(
            status="done",
            stage="complete",
            completed_at=datetime.now().isoformat(),
            message="综述生成完成",
        )
    except asyncio.CancelledError:
        update(status="cancelled", message="已停止，完成的章节与证据已保存，可继续生成")
    except Exception as exc:
        if event.is_set():
            update(status="cancelled", message="已停止，完成的章节与证据已保存，可继续生成")
        else:
            update(
                status="failed",
                error=str(exc),
                checkpoint_error=str(exc) if isinstance(exc, ReviewCheckpointError) else None,
                message=(
                    "综述检查点不可用，请查看错误并新建综述"
                    if isinstance(exc, ReviewCheckpointError)
                    else "生成未完成，已保留证据与成功章节"
                ),
            )


def resume_review(task_id, review_id):
    review = get_review(task_id, review_id)
    if any(key[0] == task_id and not job.done() for key, job in _jobs.items()):
        raise RuntimeError("已有综述仍在运行或停止中")
    if not review.get("resumable") or review.get("mode") != "survey":
        raise ValueError("此综述不能继续；可以新建一次生成")
    model = get_llm()
    if review.get("model") != _model_info(model):
        raise ValueError("模型配置已变化，请新建综述以保持来源一致")
    update_review(
        task_id, review_id, status="queued", error=None, message="将复用已完成章节继续生成"
    )
    _launch(task_id, review_id, model)
    return review_id


def cancel_review(task_id, review_id):
    review = get_review(task_id, review_id)
    event = _cancellations.get((task_id, review_id))
    if event:
        event.set()
        update_review(task_id, review_id, message="正在停止当前调用，已完成章节将保留")
    elif review["status"] in {"queued", "running"}:
        update_review(
            task_id, review_id, status="interrupted", message="进程已中断，已完成章节可继续"
        )
    return get_review(task_id, review_id)


def recover_reviews():
    for task in task_manager.list_tasks():
        for review in (task.results or {}).get("custom_reviews", []):
            if review.get("status") in {"queued", "running"}:
                update_review(
                    task.id,
                    review["id"],
                    status="interrupted",
                    message="服务重启，已保存的综述章节可继续",
                )


async def shutdown_reviews():
    jobs = list(_jobs.values())
    for event in _cancellations.values():
        event.set()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)


def review_is_active(task_id, review_id=None):
    return any(
        key[0] == task_id and (review_id is None or key[1] == review_id) and not job.done()
        for key, job in _jobs.items()
    )
