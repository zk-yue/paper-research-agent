"""Run the real search pipeline without importing or modifying application tasks.

Example: python scripts/run_search_evaluation.py --topic 'robot memory' --year-from 2021
The output includes exact prompts, source diagnostics, intermediate papers and final results.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.deep_search_agent import run_deep_search  # noqa: E402
from llm import get_llm, model_metadata  # noqa: E402


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "result.json").exists() or (output / "progress.json").exists():
        raise ValueError(f"Refusing to overwrite existing evaluation: {output}")
    model = get_llm()
    started = time.monotonic()
    metadata = {
        "topic": args.topic,
        "year_from": args.year_from,
        "quick_mode": args.quick,
        "max_rounds": args.rounds,
        "started_at": now(),
        **model_metadata(model),
        "pid": os.getpid(),
        "source_hashes": {
            name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
            for name in ("backend/deep_search_agent.py", "backend/retrieval.py", "tools.py")
        },
    }
    log = []
    calls = []
    progress = {"status": "running", "metadata": metadata, "steps": {}, "snapshot": {}}

    def persist():
        progress["updated_at"] = now()
        progress["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save_json(output / "progress.json", progress)
        save_json(output / "prompt-log.json", calls)
        (output / "log.txt").write_text("\n".join(log), encoding="utf-8")

    def set_step(index, status, detail):
        progress["steps"][str(index)] = {"status": status, "detail": detail, "updated_at": now()}
        print(f"{now()} STEP {index} {status}: {detail}", flush=True)
        persist()

    def on_plan(plan):
        progress["snapshot"]["topic_plan"] = plan
        persist()

    def on_progress(snapshot):
        progress["snapshot"] = snapshot
        print(
            f"{now()} ROUND snapshot: {len(snapshot.get('papers', []))} direct; {snapshot.get('screening', {}).get('counts', {})}",
            flush=True,
        )
        persist()

    async def invoke(messages):
        call = {
            "id": len(calls) + 1,
            "started_at": now(),
            "status": "running",
            "messages": [
                {"role": message.type, "content": message.content} for message in messages
            ],
        }
        calls.append(call)
        persist()
        call_started = time.monotonic()
        try:
            result = await asyncio.to_thread(model.invoke, messages)
            call.update(status="completed", response=result.content)
            return result
        except Exception as exc:
            call.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            call.update(
                completed_at=now(), elapsed_seconds=round(time.monotonic() - call_started, 2)
            )
            print(
                f"{now()} LLM {call['id']} {call['status']} {call['elapsed_seconds']}s", flush=True
            )
            persist()

    persist()
    print(f"Started real search evaluation pid={os.getpid()} output={output}", flush=True)
    try:
        result = await run_deep_search(
            topic=args.topic,
            year_from=args.year_from,
            max_rounds=args.rounds,
            quick_mode=args.quick,
            llm_invoke=invoke,
            set_step=set_step,
            is_cancelled=lambda: False,
            log=log,
            on_plan=on_plan,
            on_progress=on_progress,
        )
        result["evaluation"] = {
            **metadata,
            "completed_at": now(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "model_call_count": len(calls),
        }
        save_json(output / "result.json", result)
        progress.update(status="completed", snapshot=result)
        print(
            f"Completed: direct={len(result['papers'])}, screening={result.get('screening', {}).get('counts', {})}, coverage={result.get('coverage_status')}",
            flush=True,
        )
    except Exception as exc:
        progress.update(
            status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()
        )
        raise
    finally:
        persist()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--year-from", type=int, default=2021)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", default="outputs/search-wide-live")
    asyncio.run(run(parser.parse_args()))
