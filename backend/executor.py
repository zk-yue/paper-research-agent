"""Parallel research workflow executor."""

import asyncio
import time
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from backend.deep_search_agent import filter_papers_with_reason, run_deep_search
from backend.prompt_log import record_prompt
from backend.schemas import PaperInfo, StepInfo
from backend.task_manager import task_manager
from history import save_history
from llm import get_llm
from tools import (
    format_papers_for_display,
    format_papers_for_prompt,
    identify_representative_papers,
    search_papers,
    search_papers_multi_query,
)

STEP_LABELS = [
    "关键词确认",
    "论文搜索",
    "相关度过滤",
    "文献调研",
    "保存历史",
]


def _set_step(task_id: str, idx: int, status: str, detail: str):
    task_manager.set_step(task_id, idx, status, detail)


async def _run_in_executor(func, *args, timeout: int = 120):
    """Run a blocking function in a thread pool with timeout."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func, *args),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"[executor] Timeout ({timeout}s) running {func.__name__}")
        raise


async def _llm_invoke(llm, messages, task_id: str = "", agent: str = ""):
    """Simple non-streaming LLM call via thread pool, with prompt logging."""
    # Record prompt before LLM call
    if task_id:
        sys_msg = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        usr_msg = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
        entry = record_prompt(task_id, agent, sys_msg, usr_msg, status="running")
        task_manager.broadcast(
            task_id,
            {
                "type": "prompt_log_entry",
                "entry": entry,
            },
        )

    start_time = time.time()
    try:
        result = await _run_in_executor(
            llm.invoke, messages, timeout=getattr(llm, "timeout", 300) + 5
        )
    except Exception:
        if task_id:
            failed = record_prompt(
                task_id,
                agent,
                "",
                "",
                duration_ms=int((time.time() - start_time) * 1000),
                status="error",
                entry_id=entry["id"],
            )
            task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": failed})
        raise

    # Update with response
    if task_id:
        duration_ms = int((time.time() - start_time) * 1000)
        resp_content = result.content if hasattr(result, "content") else str(result)
        entry = record_prompt(
            task_id, agent, "", "", resp_content, duration_ms, "success", entry_id=entry["id"]
        )
        task_manager.broadcast(
            task_id,
            {
                "type": "prompt_log_entry",
                "entry": entry,
            },
        )
    return result


async def _llm_invoke_with_progress(
    task_id, step_idx, llm, messages, prefix="正在调用 LLM", stream=True, agent: str = ""
):
    """Run LLM invoke with periodic progress updates visible to the user.
    If stream=True, broadcast content chunks via WebSocket as they arrive."""
    import threading

    result_holder = {"response": None, "error": None}
    accumulated = []

    # Record prompt BEFORE LLM call (broadcast immediately via WS)
    sys_msg = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
    usr_msg = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
    entry = record_prompt(task_id, agent or prefix, sys_msg, usr_msg, status="running")
    task_manager.broadcast(
        task_id,
        {
            "type": "prompt_log_entry",
            "entry": entry,
        },
    )

    start_time = time.time()

    if stream:

        def _stream():
            try:
                for chunk in llm.stream(messages):
                    content = chunk.content if hasattr(chunk, "content") else ""
                    if content:
                        accumulated.append(content)
                        task_manager.broadcast(
                            task_id,
                            {
                                "type": "stream_content",
                                "step_idx": step_idx,
                                "content": content,
                            },
                        )
                from langchain_core.messages import AIMessage

                result_holder["response"] = AIMessage(content="".join(accumulated))
            except Exception as e:
                result_holder["error"] = e

        thread = threading.Thread(target=_stream)
    else:

        def _invoke():
            try:
                result_holder["response"] = llm.invoke(messages)
            except Exception as e:
                result_holder["error"] = e

        thread = threading.Thread(target=_invoke)

    thread.start()

    progress_msgs = [
        f"{prefix}...",
        f"{prefix}（已等待 5s）...",
        f"{prefix}（已等待 10s）...",
        f"{prefix}（已等待 15s）...",
        f"{prefix}（已等待 20s）...",
    ]

    i = 0
    while thread.is_alive():
        _set_step(task_id, step_idx, "running", progress_msgs[min(i, len(progress_msgs) - 1)])
        await asyncio.sleep(5)
        i += 1

    thread.join()

    duration_ms = int((time.time() - start_time) * 1000)

    if result_holder["error"]:
        entry = record_prompt(
            task_id,
            agent or prefix,
            "",
            "",
            status="error",
            duration_ms=duration_ms,
            entry_id=entry["id"],
        )
        task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": entry})
        raise result_holder["error"]

    response = result_holder["response"]
    resp_content = response.content if hasattr(response, "content") else str(response)
    entry = record_prompt(
        task_id, agent or prefix, "", "", resp_content, duration_ms, "success", entry_id=entry["id"]
    )
    task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": entry})
    return response


async def execute_task(task_id: str):
    """Execute the full research workflow for a task."""
    task = task_manager.get_task(task_id)
    if not task or task_manager.is_cancelled(task_id):
        return

    # Initialize steps as proper StepInfo objects (Pydantic won't auto-convert dicts on setattr)
    task.steps = [StepInfo(label=label, status="pending", detail="") for label in STEP_LABELS]
    task_manager.update_task(task_id, status="running", steps=task.steps)
    if getattr(task, "search_mode", "keyword") == "deep" and getattr(task, "topic", None):
        _set_step(task_id, 0, "running", "正在确认研究主题和检索查询词…")
    else:
        _set_step(task_id, 0, "done", f"已使用 {len(task.keywords)} 个关键词")

    log = []
    state = {}

    try:
        await _execute_literature(task_id, task, log, state)
        if task_manager.is_cancelled(task_id):
            return

        # Save history
        _set_step(task_id, 4, "running", "保存至 task_data/ 目录")
        await _save_task_history(task_id, task, state, log)
        _set_step(task_id, 4, "done", f"ID: {task_id}")

        # Collect results for frontend
        results = dict(task.results or {})
        if state.get("literature_review"):
            results["literature_review"] = state["literature_review"]
        if state.get("deep_research_summary"):
            results["deep_research_summary"] = state["deep_research_summary"]
        if state.get("iteration_log"):
            results["iteration_log"] = state["iteration_log"]
        if state.get("topic_plan"):
            results["topic_plan"] = state["topic_plan"]
        for key in ("screening", "coverage_status"):
            if key in state:
                results[key] = state[key]

        task_manager.update_task(
            task_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            results=results,
        )

    except asyncio.CancelledError:
        task_manager.update_task(
            task_id,
            status="cancelled",
            completed_at=datetime.now().isoformat(),
        )
        raise
    except Exception as e:
        task_manager.update_task(
            task_id,
            status="failed",
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )
        _set_step(task_id, _get_current_step(task), "error", f"错误: {e}")


def _get_current_step(task) -> int:
    """Find the first non-done step."""
    for i, s in enumerate(task.steps):
        status = s.status if isinstance(s, StepInfo) else s.get("status")
        if status not in ("done", "skipped", "pending"):
            return i
    return 0


async def _execute_literature(task_id, task, log, state):
    """Step 1-3: Search, filter, literature review."""
    if getattr(task, "search_mode", "keyword") == "deep" and getattr(task, "topic", None):
        await _execute_deep_literature(task_id, task, log, state)
        return

    top_k = 1 if task.quick_mode else 5
    max_results = 10 if task.quick_mode else 50

    # === Step 1: Search papers ===
    search_keywords = task.keywords
    _set_step(task_id, 1, "running", f"并行搜索 {len(search_keywords)} 个关键词...")
    papers = await _parallel_search(search_keywords, max_results, task.year_from)
    log.append(f"搜索到 {len(papers)} 篇论文（去重后）")
    _set_step(task_id, 1, "done", f"找到 {len(papers)} 篇论文")

    if not papers:
        log.append("未搜索到论文，尝试用原始关键词搜索...")
        papers = await _run_in_executor(
            search_papers, ", ".join(task.keywords), 20, None, task.year_from
        )
        log.append(f"补充搜索到 {len(papers)} 篇论文")
        _set_step(task_id, 1, "done", f"补充搜索找到 {len(papers)} 篇论文")

    if task_manager.is_cancelled(task_id):
        return

    if not papers:
        _set_step(task_id, 2, "skipped", "未检索到论文，跳过相关度过滤")
        _skip_empty_literature(task_id, state, log, "未检索到论文")
        return

    llm = await _run_in_executor(get_llm)
    # === Step 2: Relevance filter (parallel batches) ===
    _set_step(task_id, 2, "running", f"评估 {len(papers)} 篇论文相关度...")
    filtered = await _parallel_relevance_filter(papers, ", ".join(task.keywords), llm, log, task_id)
    _set_step(task_id, 2, "done", f"保留 {len(filtered)}/{len(papers)} 篇")

    if task_manager.is_cancelled(task_id):
        return

    if not filtered:
        screening = (task.results or {}).get("screening") or {}
        reason = "相关度过滤后无可用论文"
        if screening.get("pending"):
            reason = "候选论文待复核，暂无已确认的直接相关论文"
        elif screening.get("supporting"):
            reason = "仅找到补充阅读，暂无直接相关论文"
        _skip_empty_literature(task_id, state, log, reason)
        return

    # Sort and identify representative
    representative, sorted_papers = identify_representative_papers(filtered, top_k=top_k)

    # === Step 3: Literature review ===
    papers_text = format_papers_for_prompt(sorted_papers, representative)

    if task.quick_mode:
        _set_step(task_id, 3, "running", "测试模式：跳过文献调研 LLM...")
        await asyncio.sleep(0.5)
        literature_review = (
            "## 📚 测试模式：文献调研（占位内容）\n\n"
            "> 测试模式下跳过 LLM 生成，以下为固定内容。\n\n"
            "### 研究方向\n"
            "- 方向 A：测试验证\n"
            "- 方向 B：流程完整性检查\n\n"
            "### 关键术语\n"
            "Test, Mode, Placeholder, Quick, Validation\n\n"
            "### 代表性论文分析\n"
            "本次为测试运行，请切换到正式模式后查看完整的论文分析。\n\n"
            "### 关键阅读问题\n"
            "- 该研究解决了什么问题？\n"
            "- 实验设计是否合理？\n"
            "- 结果是否可复现？\n\n"
            "### 开放问题\n"
            "测试模式结束，请切换正式模式查看完整分析。"
        )
    else:
        _set_step(task_id, 3, "running", f"基于 {len(sorted_papers)} 篇论文生成文献调研...")

        lit_sys = """你是一位具身智能和机器人领域的资深研究员。
你的任务是基于搜索到的真实论文，为给定的研究主题生成结构化的文献调研框架。
聚焦该领域的最新趋势、关键挑战和代表性工作。

请输出一份结构清晰的 Markdown 文档，包含以下章节：
1. 研究方向：3-5 个相关子方向
2. 关键术语：10-15 个重要关键词
3. 代表性论文分析：基于标记为 ⭐ 的代表性论文，详细分析其核心贡献和方法
4. 关键阅读问题：阅读该领域论文时需要重点关注的 5-8 个问题
5. 开放问题：当前的研究空白和机会

请使用中文输出。"""

        lit_usr = f"""研究主题：{", ".join(task.keywords)}

搜索到的相关论文（共 {len(sorted_papers)} 篇，⭐ 标记为代表性论文）：
{papers_text}

请基于以上真实论文，为该主题生成一份全面的文献调研框架。
使用 Markdown 格式输出。"""

        response = await _llm_invoke_with_progress(
            task_id,
            3,
            llm,
            [
                SystemMessage(content=lit_sys),
                HumanMessage(content=lit_usr),
            ],
            prefix="生成文献调研",
            agent="文献调研Agent",
        )
        literature_review = response.content

    state["literature_review"] = literature_review
    state["papers"] = sorted_papers
    state["representative_papers"] = representative

    # Translate abstracts for display (parallel batches)
    if sorted_papers and not task.quick_mode:
        _set_step(task_id, 3, "running", "生成中文摘要（用于展示）...")
        sorted_papers = await _parallel_translate(sorted_papers, llm, log, task_id)
        representative = [
            p for p in sorted_papers if p.get("title") in {r.get("title") for r in representative}
        ]

    papers_display = format_papers_for_display(sorted_papers, representative)
    state["papers_display"] = papers_display
    task_manager.update_task(task_id, papers=_papers_to_task_payload(sorted_papers))

    _set_step(task_id, 3, "done", f"生成 {len(literature_review)} 字符文献调研")


def _skip_empty_literature(task_id, state, log, reason):
    """Finish an empty search without inventing a review or invoking a model."""
    detail = f"{reason}，跳过文献调研与综述生成"
    log.append(detail)
    state.update(
        papers=[],
        representative_papers=[],
        literature_review="",
        deep_research_summary="",
        papers_display=reason,
    )
    task_manager.update_task(task_id, papers=[])
    _set_step(task_id, 3, "skipped", detail)


def _papers_to_task_payload(sorted_papers: list) -> list[PaperInfo]:
    """Map internal paper dicts to TaskInfo.papers payload."""
    task_papers = []
    for p in sorted_papers:
        year = ""
        if p.get("published_date"):
            year = str(p["published_date"])[:4]
        task_papers.append(
            PaperInfo(
                **{
                    "title": p.get("title", ""),
                    "authors": p.get("authors", ""),
                    "url": p.get("url"),
                    "pdf_url": p.get("pdf_url"),
                    "paper_id": p.get("paper_id"),
                    "summary_cn": p.get("abstract_cn") or p.get("abstract", ""),
                    "relevance_score": p.get("relevance_score"),
                    "year": year or None,
                    "source": p.get("source"),
                    "citations": p.get("citations"),
                    "relevance_reason": p.get("relevance_reason"),
                    "discovery_round": p.get("discovery_round"),
                    "relevance_match_scope": p.get("relevance_match_scope"),
                    "relevance_evidence": p.get("relevance_evidence"),
                    "citation_from": p.get("citation_from") or [],
                    "matched_queries": p.get("matched_queries")
                    or ([p["matched_query"]] if p.get("matched_query") else []),
                }
            )
        )
    return task_papers


async def _execute_deep_literature(task_id, task, log, state):
    """Deep mode: multi-round topic search + comprehensive summary."""
    llm = await _run_in_executor(get_llm)
    topic = (task.topic or "").strip() or ", ".join(task.keywords)

    async def llm_invoke(messages):
        return await _llm_invoke(llm, messages, task_id=task_id, agent="深度检索Agent")

    def set_step(idx, status, detail):
        _set_step(task_id, idx, status, detail)

    def is_cancelled():
        return task_manager.is_cancelled(task_id)

    def publish_plan(plan):
        # Persist before retrieval so queries remain visible during search,
        # on refresh, and if a later phase fails or is stopped.
        state["topic_plan"] = plan
        current = task_manager.get_task(task_id)
        if current is not None and not task_manager.is_cancelled(task_id):
            task_manager.update_task(
                task_id, results={**(current.results or {}), "topic_plan": plan}
            )

    def publish_progress(snapshot):
        if task_manager.is_cancelled(task_id):
            return
        state.update(snapshot)
        current = task_manager.get_task(task_id)
        if current is not None:
            public = {
                key: snapshot[key]
                for key in ("iteration_log", "screening", "coverage_status", "topic_plan")
                if key in snapshot
            }
            task_manager.update_task(
                task_id,
                results={**(current.results or {}), **public},
                papers=_papers_to_task_payload(snapshot.get("papers") or []),
            )

    result = await run_deep_search(
        topic=topic,
        year_from=task.year_from,
        max_rounds=getattr(task, "deep_rounds", 3) or 3,
        quick_mode=task.quick_mode,
        llm_invoke=llm_invoke,
        set_step=set_step,
        is_cancelled=is_cancelled,
        log=log,
        on_plan=publish_plan,
        on_progress=publish_progress,
    )

    if task_manager.is_cancelled(task_id):
        return

    for key in ("screening", "coverage_status"):
        if key in result:
            state[key] = result[key]
    sorted_papers = result.get("papers") or []
    representative = result.get("representative") or []
    summary = result.get("summary") or ""

    state["literature_review"] = summary
    state["deep_research_summary"] = summary
    state["iteration_log"] = result.get("iteration_log") or []
    state["topic_plan"] = result.get("topic_plan") or {}
    state["papers"] = sorted_papers
    state["representative_papers"] = representative

    if not sorted_papers:
        _skip_empty_literature(task_id, state, log, result.get("empty_reason") or "无可用论文")
        return

    if sorted_papers and not task.quick_mode:
        _set_step(task_id, 3, "running", "生成中文摘要（用于展示）...")
        sorted_papers = await _parallel_translate(sorted_papers, llm, log, task_id)
        representative = [
            p for p in sorted_papers if p.get("title") in {r.get("title") for r in representative}
        ]

    state["papers_display"] = format_papers_for_display(sorted_papers, representative)
    task_manager.update_task(task_id, papers=_papers_to_task_payload(sorted_papers))

    _set_step(task_id, 3, "done", f"生成 {len(summary)} 字符文献调研")


async def _save_task_history(task_id, task, state, log):
    """Save task results to history."""
    _set_step(task_id, 4, "running", "保存历史记录...")

    def _save():
        save_history(
            keywords=task.keywords,
            year_from=task.year_from,
            literature_review=state.get("literature_review", ""),
            papers_display=state.get("papers_display", ""),
            execution_log="\n".join(log),
        )

    await _run_in_executor(_save)


# === Parallel helpers ===


async def _parallel_search(keywords, max_results, year_from):
    """Search papers with all keywords combined via OR query."""
    try:
        result = await _run_in_executor(
            search_papers_multi_query,
            keywords,
            max_results,
            year_from,
            timeout=90,
        )
        if isinstance(result, list):
            print(f"[parallel_search] Got {len(result)} papers from combined query")
            return result
    except asyncio.TimeoutError:
        print("[parallel_search] Timeout on combined search")
    except Exception as e:
        print(f"[parallel_search] Combined search failed: {e}")

    # Fallback: try with original topic if combined query fails
    print("[parallel_search] Combined query returned nothing, trying fallback...")
    return []


async def _parallel_relevance_filter(papers, topic, llm, log, task_id=""):
    """Use the same evidence-based screening contract in both search modes."""

    async def invoke(messages):
        return await _llm_invoke(llm, messages, task_id=task_id, agent="相关度过滤Agent")

    audit = []
    retained = await filter_papers_with_reason(papers, topic, invoke, log, 1, audit=audit)
    if task_id:
        task = task_manager.get_task(task_id)
        if task and not task_manager.is_cancelled(task_id):
            screening = {
                key: [paper for paper in audit if paper.get("screening_status") == key]
                for key in ("included", "supporting", "excluded", "pending")
            }
            task_manager.update_task(
                task_id, results={**(task.results or {}), "screening": screening}
            )
    return retained


async def _parallel_translate(papers, llm, log, task_id=""):
    """Translate abstracts to Chinese in parallel batches."""
    if not papers:
        return papers

    batch_size = 10

    async def translate_batch(batch):
        abstracts_text = "\n\n".join(
            f"[{i}] {p.get('abstract', '')}" for i, p in enumerate(batch) if p.get("abstract")
        )
        if not abstracts_text:
            return batch

        sys_prompt = """你是一位学术论文翻译专家。请将以下英文论文摘要翻译为中文。
请直接输出一个 JSON 对象，key 为编号，value 为中文摘要。例如：
{"0": "中文摘要1", "1": "中文摘要2"}"""

        response = await _llm_invoke(
            llm,
            [
                SystemMessage(content=sys_prompt),
                HumanMessage(content=f"请翻译以下论文摘要：\n\n{abstracts_text}"),
            ],
            task_id=task_id,
            agent="摘要翻译Agent",
        )

        try:
            import json

            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
            translations = json.loads(content)
            if isinstance(translations, dict):
                for idx_str, translated in translations.items():
                    try:
                        idx = int(idx_str)
                        if 0 <= idx < len(batch):
                            batch[idx]["abstract_cn"] = translated
                    except (ValueError, IndexError):
                        continue
        except Exception:
            pass

        return batch

    batch_jobs = []
    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        batch_jobs.append((batch, translate_batch(batch)))

    results = await asyncio.gather(*(job for _, job in batch_jobs), return_exceptions=True)
    translated_papers = []
    for batch, r in zip((batch for batch, _ in batch_jobs), results):
        if isinstance(r, list):
            translated_papers.extend(r)
        else:
            if isinstance(r, Exception):
                print(f"[translate] Batch failed: {r}")
            translated_papers.extend(batch)

    return translated_papers
