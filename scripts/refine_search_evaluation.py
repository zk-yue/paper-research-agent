"""Append an evidence-based supplemental round to a completed local evaluation.

This never retrieves papers, imports application tasks, or changes the base run.
Only genuine new candidates and pending records with richer abstracts are screened.
Cached real-model rechecks may update already-discovered papers with verified quotes.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.deep_search_agent import (  # noqa: E402
    RELEVANCE_WITH_REASON_SYSTEM,
    filter_papers_with_reason,
    generate_deep_summary,
    screening_summary,
)
from backend.retrieval import SearchResults, retrieve_references  # noqa: E402
from llm import get_llm, model_metadata  # noqa: E402
from storage import atomic_write_json  # noqa: E402
from tools import (  # noqa: E402
    filter_papers_by_year,
    find_matching_paper_index,
    identify_representative_papers,
    merge_paper_lists,
    merge_paper_metadata,
    parse_llm_json,
)

GROUPS = ("included", "supporting", "pending", "excluded")
VERDICT_FIELDS = (
    "relevance_score",
    "relevance_reason",
    "relevance_match_scope",
    "relevance_evidence",
    "screening_status",
    "matched_directions",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_status(paper):
    paper = dict(paper)
    if paper.get("screening_status") == "pending":
        return paper
    score = paper.get("relevance_score")
    if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5:
        paper["screening_status"] = (
            "included" if score >= 4 else "supporting" if score == 3 else "excluded"
        )
    else:
        paper.pop("relevance_score", None)
        paper["screening_status"] = "pending"
    return paper


def prepare_records(base, supplements):
    """Return a merged audit and indices requiring fresh evaluation, without I/O."""
    records = []
    for group in GROUPS:
        for paper in (base.get("screening") or {}).get(group, []):
            paper = normalize_status({**copy.deepcopy(paper), "screening_status": group})
            index = find_matching_paper_index(records, paper)
            if index is None:
                records.append(paper)
            else:
                records[index] = merge_paper_metadata(records[index], paper)
    for paper in base.get("papers", []):
        if find_matching_paper_index(records, paper) is None:
            records.append(normalize_status(copy.deepcopy(paper)))
    candidate_indices = set()
    year_from = base["evaluation"]["year_from"]
    for supplement in supplements:
        for paper in filter_papers_by_year(supplement.get("papers", []), year_from):
            paper = copy.deepcopy(paper)
            index = find_matching_paper_index(records, paper)
            if index is None:
                for field in VERDICT_FIELDS:
                    paper.pop(field, None)
                paper["screening_status"] = "pending"
                candidate_indices.add(len(records))
                records.append(paper)
                continue
            previous = records[index]
            merged = merge_paper_metadata(previous, paper)
            if previous.get("screening_status") == "pending" and len(
                paper.get("abstract") or ""
            ) > len(previous.get("abstract") or ""):
                merged["abstract"] = paper["abstract"]
                candidate_indices.add(index)
            if paper.get("is_retracted") and previous.get("screening_status") != "excluded":
                # Deterministic metadata override; no need to ask the model to
                # endorse a withdrawn work or discard an otherwise valid audit.
                merged.update(
                    screening_status="excluded",
                    relevance_score=1,
                    relevance_reason="检索来源标记撤稿，不作为研究证据",
                    relevance_match_scope="excluded",
                    relevance_evidence="来源元数据 is_retracted=true",
                )
                candidate_indices.discard(index)
            records[index] = merged
    return records, candidate_indices


def canonical(text):
    text = re.sub(r"\\{2,}(?=[A-Za-z_])", lambda _: "\\", str(text or ""))
    return " ".join(text.split()).casefold()


def verified_recheck(verdict, paper, document):
    """Require an actual cached response and a quote in the discovered paper.

    No benchmark expected labels, check booleans, or fixture scores are read.
    """
    quote = canonical(verdict.get("relevance_evidence"))
    source = canonical(f"{paper.get('title', '')}\n{paper.get('abstract', '')}")
    if len(quote) < 8 or quote not in source or paper.get("is_retracted"):
        return False
    score = verdict.get("relevance_score")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
        return False
    expected = "include" if score >= 3 else "exclude"
    for call in document.get("llm_calls", []):
        response = parse_llm_json(call.get("response") or "")
        if not isinstance(response, list):
            continue
        for item in response:
            if not isinstance(item, dict):
                continue
            if (
                item.get("score") == score
                and item.get("decision") == expected
                and canonical(item.get("evidence")) == quote
                and item.get("reason") == verdict.get("relevance_reason")
            ):
                # Ensure the corresponding call actually included this paper,
                # rather than borrowing an identical sentence from another run.
                messages = "\n".join(str(m.get("content", "")) for m in call.get("messages", []))
                if canonical(paper.get("title")) in canonical(messages):
                    return True
    return False


def apply_rechecks(records, candidate_indices, rechecks):
    applied = []
    used_paths = set()
    for path, document in rechecks:
        if not document.get("completed_at"):
            raise ValueError(f"Incomplete recheck: {path}")
        for verdict in document.get("audit", []):
            index = find_matching_paper_index(records, verdict)
            if index is None or not verified_recheck(verdict, records[index], document):
                continue
            before = {field: records[index].get(field) for field in VERDICT_FIELDS}
            # Merge retrieval metadata first, then explicitly replace the verdict:
            # generic metadata merging intentionally prefers high previous scores.
            merged = dict(records[index])
            for field in VERDICT_FIELDS:
                if field in verdict:
                    merged[field] = copy.deepcopy(verdict[field])
                else:
                    merged.pop(field, None)
            records[index] = normalize_status(merged)
            candidate_indices.discard(index)
            used_paths.add(str(path))
            applied.append(
                {
                    "paper_id": merged.get("paper_id"),
                    "title": merged.get("title"),
                    "artifact": str(path),
                    "artifact_sha256": sha256(path),
                    "before": before,
                    "after": {field: records[index].get(field) for field in VERDICT_FIELDS},
                }
            )
    return applied, used_paths


def inherited_calls(calls, artifact, document=None):
    document = document or {}
    result = []
    for call in calls:
        recorded_at = document.get("started_at") or document.get("completed_at")
        result.append(
            {
                **copy.deepcopy(call),
                "original_id": call.get("id"),
                "source_artifact": str(artifact),
                "source_sha256": sha256(artifact),
                "status": call.get(
                    "status", "completed" if call.get("response") is not None else "unknown"
                ),
                "started_at": call.get("started_at") or recorded_at,
                "completed_at": call.get("completed_at") or document.get("completed_at"),
                "timing_basis": "call"
                if call.get("started_at")
                else "recorded_artifact_timestamp_not_exact_call_start",
                "elapsed_seconds": call.get("elapsed_seconds", call.get("seconds", 0)),
            }
        )
    return result


async def run(args):
    base_dir = Path(args.base).resolve()
    base_file = base_dir / "result.json"
    if not base_file.is_file():
        raise ValueError("Base result.json is absent; wait for the original run to finish")
    base = read_json(base_file)
    if not base.get("evaluation", {}).get("completed_at"):
        raise ValueError("Base evaluation is incomplete")
    if (base_dir / "progress.json").exists() and read_json(base_dir / "progress.json").get(
        "status"
    ) != "completed":
        raise ValueError("Base progress is not completed")
    topic = base["evaluation"]["topic"]
    year_from = base["evaluation"]["year_from"]
    supplement_paths = [Path(path).resolve() for path in args.supplement]
    supplements = [read_json(path) for path in supplement_paths]
    for path, data in zip(supplement_paths, supplements):
        if (
            not data.get("completed_at")
            or data.get("topic") != topic
            or data.get("year_from") != year_from
        ):
            raise ValueError(f"Incomplete or incompatible supplemental retrieval: {path}")
    rescreen_included = getattr(args, "rescreen_included", False)
    skip_references = getattr(args, "skip_references", False) or rescreen_included
    recheck_paths = [] if rescreen_included else [Path(path).resolve() for path in args.recheck]
    rechecks = [(path, read_json(path)) for path in recheck_paths]
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite output directory: {output}")
    model = get_llm()
    records, candidate_indices = prepare_records(base, supplements)
    rescreen_count = 0
    if rescreen_included:
        included_indices = {
            i for i, p in enumerate(records) if p.get("screening_status") == "included"
        }
        candidate_indices.update(included_indices)
        rescreen_count = len(included_indices)
    checkpoint_signature = hashlib.sha256(
        json.dumps(
            {
                "topic": topic,
                "year_from": year_from,
                "topic_plan": base.get("topic_plan"),
                "prompt": RELEVANCE_WITH_REASON_SYSTEM,
                **model_metadata(model),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    checkpoint_records = []
    resumed_count = 0
    resume_path = getattr(args, "resume_screening", None)
    if resume_path:
        checkpoint = read_json(resume_path)
        if checkpoint.get("signature") != checkpoint_signature:
            raise ValueError(
                "Checkpoint uses different topic semantics, screening prompt, or model"
            )
        for reviewed in checkpoint.get("records", []):
            index = find_matching_paper_index(records, reviewed)
            if index in candidate_indices and records[index].get("abstract") == reviewed.get(
                "abstract"
            ):
                restored = dict(records[index])
                for field in VERDICT_FIELDS:
                    if field in reviewed:
                        restored[field] = copy.deepcopy(reviewed[field])
                    else:
                        restored.pop(field, None)
                records[index] = restored
                checkpoint_records.append(restored)
                candidate_indices.discard(index)
                resumed_count += 1
    applied_rechecks, used_recheck_paths = apply_rechecks(records, candidate_indices, rechecks)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    started_at = now()
    log = []
    original_log = base_dir / "log.txt"
    if original_log.exists():
        log.extend(original_log.read_text(encoding="utf-8").splitlines())
    calls_file = base_dir / "prompt-log.json"
    calls = inherited_calls(read_json(calls_file), calls_file) if calls_file.exists() else []
    for path, document in rechecks:
        if str(path) in used_recheck_paths:
            calls.extend(inherited_calls(document.get("llm_calls", []), path, document))
    for path in args.planning_artifact:
        path = Path(path).resolve()
        document = read_json(path)
        calls.extend(inherited_calls(document.get("llm_calls", []), path, document))
    for index, call in enumerate(calls, 1):
        call["id"] = index
    inherited_count = len(calls)
    inputs = [
        base_file,
        *supplement_paths,
        *recheck_paths,
        *(Path(p).resolve() for p in args.planning_artifact),
    ]
    if calls_file.exists():
        inputs.append(calls_file)
    if resume_path:
        inputs.append(Path(resume_path).resolve())
    metadata = {
        **base["evaluation"],
        "started_at": started_at,
        "pid": os.getpid(),
        "quick_mode": False,
        **model_metadata(model),
        "method": (
            "Rescreen the previously included set under current architecture and task-function evidence criteria; preserve all other groups and discovery history, regenerate synthesis, and do not retrieve again."
            if rescreen_included and not supplements
            else "Append one supplemental retrieval round; merge real cached hits and evidence-verified rechecks, screen only new/richer pending candidates, regenerate synthesis from direct evidence. Base query plan and earlier rounds remain unchanged."
        ),
        "base_started_at": base["evaluation"]["started_at"],
        "base_completed_at": base["evaluation"]["completed_at"],
        "input_artifacts": [{"path": str(path), "sha256": sha256(path)} for path in inputs],
        "source_hashes": {
            name: sha256(ROOT / name)
            for name in (
                "backend/deep_search_agent.py",
                "backend/retrieval.py",
                "tools.py",
                "scripts/refine_search_evaluation.py",
            )
        },
    }
    # Never carry the base completion marker into an in-progress refinement.
    metadata.pop("completed_at", None)
    progress = {"status": "running", "metadata": metadata, "steps": {}, "snapshot": {}}

    def persist():
        progress.update(updated_at=now(), elapsed_seconds=round(time.monotonic() - started, 2))
        atomic_write_json(output / "progress.json", progress)
        atomic_write_json(output / "prompt-log.json", calls)
        (output / "log.txt").write_text("\n".join(log), encoding="utf-8")

    async def invoke(messages):
        call = {
            "id": len(calls) + 1,
            "started_at": now(),
            "status": "running",
            "phase": "refinement",
            "messages": [
                {"role": message.type, "content": message.content} for message in messages
            ],
        }
        calls.append(call)
        persist()
        call_start = time.monotonic()
        try:
            response = await asyncio.to_thread(model.invoke, messages)
            call.update(status="completed", response=response.content)
            return response
        except Exception as exc:
            call.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            call.update(completed_at=now(), elapsed_seconds=round(time.monotonic() - call_start, 2))
            persist()

    candidate_indices = sorted(candidate_indices)
    candidates = []
    for index in candidate_indices:
        paper = dict(records[index])
        for field in VERDICT_FIELDS:
            paper.pop(field, None)
        candidates.append(paper)
    iterations = copy.deepcopy(base.get("iteration_log") or [])
    round_number = (
        max(
            base["evaluation"].get("max_rounds", 0),
            max((entry.get("round", 0) for entry in iterations), default=0),
        )
        + 1
    )
    queries = list(
        dict.fromkeys(query for data in supplements for query in data.get("queries", []))
    )
    diagnostics = [entry for data in supplements for entry in data.get("diagnostics", [])]
    warnings = list(
        dict.fromkeys(warning for data in supplements for warning in data.get("warnings", []))
    )
    checked_count = 0

    def on_batch(batch):
        nonlocal checked_count
        checked_count += len(batch)
        checkpoint_records.extend(copy.deepcopy(batch))
        atomic_write_json(
            output / "screening-checkpoint.json",
            {
                "signature": checkpoint_signature,
                "updated_at": now(),
                "base_artifact": str(base_file),
                "base_sha256": sha256(base_file),
                "prompt_sha256": hashlib.sha256(RELEVANCE_WITH_REASON_SYSTEM.encode()).hexdigest(),
                "records": checkpoint_records,
            },
        )
        progress["steps"]["screening"] = {
            "status": "running",
            "detail": f"已筛选补充候选 {checked_count}/{len(candidates)}",
        }
        print(progress["steps"]["screening"]["detail"], flush=True)
        persist()

    log.append(
        f"复筛主列表 {rescreen_count} 篇（恢复已完成 {resumed_count} 篇）；本次筛选 {len(candidates)} 篇，不重复检索。"
        if rescreen_included and not queries
        else f"追加第 {round_number} 轮补充检索：{queries}；新/更完整候选 {len(candidates)} 篇，应用 {len(applied_rechecks)} 条真实证据复核。"
    )
    persist()
    try:
        new_audit = []
        if candidates:
            await filter_papers_with_reason(
                candidates,
                topic,
                invoke,
                log,
                round_number,
                topic_plan=base.get("topic_plan"),
                audit=new_audit,
                on_batch=on_batch,
            )
        for reviewed in new_audit:
            index = find_matching_paper_index(records, reviewed)
            if index is None or index not in candidate_indices:
                raise ValueError("Screening returned an unexpected paper identity")
            if rescreen_included and records[index].get("discovery_round") is not None:
                reviewed["discovery_round"] = records[index]["discovery_round"]
            records[index] = reviewed
        records = [normalize_status(paper) for paper in records]
        lexical_candidate_count = len(candidates)
        citation_hits = SearchResults()
        citation_artifact = None
        citation_metadata = copy.deepcopy((base.get("refinement") or {}).get("citation_artifact"))
        if skip_references:
            candidates = []
            total_candidate_count = lexical_candidate_count
            progress["steps"]["citations"] = {
                "status": "skipped",
                "detail": "保留上一轮引用结果，不执行第二跳引用扩展",
            }
        else:
            citation_started_at = now()
            progress["steps"]["citations"] = {
                "status": "running",
                "detail": "从直接相关论文进行一次有界引用补检",
            }
            persist()
            citation_hits = await asyncio.to_thread(
                retrieve_references,
                [p for p in records if p.get("screening_status") == "included"],
                year_from,
                20,
                60,
            )
            citation_artifact = output / "citation-retrieval.json"
            atomic_write_json(
                citation_artifact,
                {
                    "topic": topic,
                    "year_from": year_from,
                    "started_at": citation_started_at,
                    "completed_at": now(),
                    "queries": ["backward citation search"],
                    "papers": list(citation_hits),
                    "diagnostics": citation_hits.diagnostics,
                    "warnings": citation_hits.warnings,
                },
            )
            citation_base = {
                "evaluation": metadata,
                "screening": screening_summary(records),
                "papers": [],
            }
            records, citation_indices = prepare_records(
                citation_base, [{"papers": list(citation_hits)}]
            )
            citation_indices = sorted(citation_indices)
            candidates = []
            for index in citation_indices:
                paper = dict(records[index])
                for field in VERDICT_FIELDS:
                    paper.pop(field, None)
                candidates.append(paper)
            citation_audit = []
            checked_count = 0
            if candidates:
                await filter_papers_with_reason(
                    candidates,
                    topic,
                    invoke,
                    log,
                    round_number,
                    topic_plan=base.get("topic_plan"),
                    audit=citation_audit,
                    on_batch=on_batch,
                )
            for reviewed in citation_audit:
                index = find_matching_paper_index(records, reviewed)
                if index is None or index not in citation_indices:
                    raise ValueError("Citation screening returned an unexpected paper identity")
                records[index] = reviewed
            new_audit.extend(citation_audit)
            total_candidate_count = lexical_candidate_count + len(candidates)
            queries.append("backward citation search")
            diagnostics.extend(citation_hits.diagnostics)
            warnings = list(dict.fromkeys([*warnings, *citation_hits.warnings]))
            log.extend(f"⚠️ {warning}" for warning in citation_hits.warnings)
            progress["steps"]["citations"] = {
                "status": "done",
                "detail": f"一跳命中 {len(citation_hits)} 篇，新增筛选 {len(candidates)} 篇",
            }
            citation_metadata = {
                "path": str(citation_artifact),
                "sha256": sha256(citation_artifact),
            }
        records = [normalize_status(paper) for paper in records]
        screening = screening_summary(records)
        direct = screening["included"]
        representative, sorted_papers = identify_representative_papers(direct, top_k=5)
        entry = {
            "round": round_number,
            "queries": queries,
            "found": len(
                merge_paper_lists(
                    [],
                    [p for data in supplements for p in data.get("papers", [])]
                    + list(citation_hits),
                )
            ),
            "new_candidates": total_candidate_count,
            "lexical_new_candidates": lexical_candidate_count,
            "citation_new_candidates": len(candidates),
            "citation_found": len(citation_hits),
            "new_retained": sum(p.get("screening_status") == "included" for p in new_audit),
            "total_retained": len(direct),
            "search_diagnostics": diagnostics,
            "warnings": warnings,
            "method": "supplemental_cached_retrieval",
            "retrieval_artifacts": [str(p) for p in supplement_paths]
            + ([str(citation_artifact)] if citation_artifact else []),
        }
        if queries or not rescreen_included:
            iterations.append(entry)
        combined_diagnostics = [
            d for iteration in iterations for d in iteration.get("search_diagnostics", [])
        ]
        coverage = (
            "partial_sources"
            if any(d.get("status") in {"error", "skipped"} for d in combined_diagnostics)
            else "bounded_results"
        )
        progress["snapshot"] = {
            "papers": sorted_papers,
            "screening": screening,
            "iteration_log": iterations,
            "topic_plan": base.get("topic_plan"),
            "coverage_status": coverage,
        }
        progress["steps"]["screening"] = {"status": "done", "detail": str(screening["counts"])}
        progress["steps"]["summary"] = {
            "status": "running",
            "detail": "基于最终直接相关论文生成总结",
        }
        persist()
        summary = (
            await generate_deep_summary(
                topic, sorted_papers, representative, iterations, invoke, log
            )
            if direct
            else ""
        )
        result = {
            "papers": sorted_papers,
            "representative": representative,
            "summary": summary,
            "iteration_log": iterations,
            "topic_plan": copy.deepcopy(base.get("topic_plan")),
            "screening": screening,
            "coverage_status": coverage,
            "refinement": {
                "base_screening": copy.deepcopy(base.get("screening")),
                "applied_rechecks": applied_rechecks,
                "supplemental_candidate_count": total_candidate_count,
                "citation_artifact": citation_metadata,
                "reference_expansion_skipped": skip_references,
                "rescreened_included_count": rescreen_count,
                "resumed_screening_count": resumed_count,
                "screening_prompt_sha256": hashlib.sha256(
                    RELEVANCE_WITH_REASON_SYSTEM.encode()
                ).hexdigest(),
            },
            "evaluation": {
                **metadata,
                "completed_at": now(),
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "max_rounds": len(iterations),
                "model_call_count": len(calls),
                "inherited_model_call_count": inherited_count,
                "new_model_call_count": len(calls) - inherited_count,
            },
        }
        atomic_write_json(output / "result.json", result)
        progress.update(status="completed", snapshot=result)
        progress["steps"]["summary"] = {
            "status": "done" if summary else "skipped",
            "detail": f"总结 {len(summary)} 字符",
        }
        print(f"Completed supplemental evaluation: {screening['counts']}", flush=True)
        return result
    except Exception as exc:
        progress.update(
            status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()
        )
        raise
    finally:
        persist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Directory of a completed evaluation")
    parser.add_argument(
        "--supplement",
        nargs="*",
        default=[],
    )
    parser.add_argument(
        "--recheck",
        nargs="*",
        default=[],
    )
    parser.add_argument("--planning-artifact", nargs="*", default=[])
    parser.add_argument(
        "--skip-references",
        action="store_true",
        help="Preserve prior citation evidence without another citation hop",
    )
    parser.add_argument(
        "--rescreen-included",
        action="store_true",
        help="Reassess all previously included papers using current evidence criteria; implies no extra citation hop",
    )
    parser.add_argument(
        "--resume-screening",
        type=Path,
        help="Reuse completed batches from a matching screening-checkpoint.json in a new output directory",
    )
    parser.add_argument("--output", required=True, help="New directory for the refined evaluation")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
