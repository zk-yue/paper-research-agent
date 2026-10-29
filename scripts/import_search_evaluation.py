"""Import a completed local evaluation as a new task without rerunning the model.

Restart an idle backend afterward to load the new task. Existing tasks are not
loaded, modified or replaced by this command.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.executor import STEP_LABELS, _papers_to_task_payload  # noqa: E402
from backend.prompt_log import LOG_DIR  # noqa: E402
from backend.schemas import StepInfo, TaskInfo  # noqa: E402
from backend.task_paths import ensure_task_dir, task_metadata_file  # noqa: E402
from storage import atomic_write_json  # noqa: E402


def import_result(directory: Path) -> str:
    result = json.loads((directory / "result.json").read_text())
    evaluation = result["evaluation"]
    if not evaluation.get("completed_at"):
        raise ValueError("Evaluation is incomplete")
    topic = evaluation["topic"]
    task_id = uuid.uuid4().hex[:12]
    summary = result.get("summary") or ""
    results = {
        key: result[key]
        for key in ("topic_plan", "iteration_log", "screening", "coverage_status")
        if key in result
    }
    if summary:
        results["deep_research_summary"] = summary
    iterations = result.get("iteration_log") or []
    papers = result.get("papers") or []
    counts = (result.get("screening") or {}).get("counts") or {}
    details = [
        f"已确认 {len((result.get('topic_plan') or {}).get('seed_queries', []))} 条查询词",
        f"完成 {len(iterations)} 轮检索，详见结果中的来源记录",
        f"直接相关 {len(papers)} 篇，补充阅读 {counts.get('supporting', 0)} 篇，待复核 {counts.get('pending', 0)} 篇",
        f"基于真实纳入文献生成总结（{len(summary)} 字符）" if summary else "未生成总结",
        "已导入本地真实评估产物，保留完整来源与筛选记录",
    ]
    task = TaskInfo(
        id=task_id,
        topic=topic,
        keywords=[topic],
        search_mode="deep",
        year_from=evaluation["year_from"],
        quick_mode=evaluation["quick_mode"],
        deep_rounds=len(iterations),
        enable_literature=True,
        created_at=datetime.fromisoformat(evaluation["started_at"])
        .astimezone()
        .replace(tzinfo=None)
        .isoformat(),
        completed_at=datetime.fromisoformat(evaluation["completed_at"])
        .astimezone()
        .replace(tzinfo=None)
        .isoformat(),
        status="completed",
        papers=_papers_to_task_payload(papers),
        results=results,
        steps=[
            StepInfo(
                label=label, status="done" if i != 3 or summary else "skipped", detail=details[i]
            )
            for i, label in enumerate(STEP_LABELS)
        ],
    )
    root = ensure_task_dir(task_id, task.keywords)
    atomic_write_json(root / "evaluation.json", result)
    calls_file = directory / "prompt-log.json"
    if calls_file.exists():
        calls = json.loads(calls_file.read_text())
        entries = []
        for call in calls:
            messages = call.get("messages") or []
            entries.append(
                {
                    "id": str(call["id"]),
                    "agent": "深度检索评估",
                    "system_prompt": "\n".join(
                        m["content"] for m in messages if m["role"] in ("system",)
                    ),
                    "user_prompt": "\n".join(
                        m["content"] for m in messages if m["role"] in ("human", "user")
                    ),
                    "response_preview": call.get("response") or call.get("error") or "",
                    "duration_ms": int(call.get("elapsed_seconds", 0) * 1000),
                    "status": "success" if call["status"] == "completed" else "error",
                    "timestamp": datetime.fromisoformat(call["started_at"]).timestamp(),
                }
            )
        atomic_write_json(LOG_DIR / f"{task_id}.json", entries)
    atomic_write_json(task_metadata_file(task_id, task.keywords), task.model_dump())
    return task_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(import_result(args.directory.resolve()))
