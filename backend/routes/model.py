import asyncio

from fastapi import APIRouter
from langchain_core.messages import HumanMessage, SystemMessage

from backend import prompt_log
from backend.schemas import (
    ExpandKeywordsRequest,
    TranslateReviewRequest,
)
from llm import get_llm
from tools import combine_keyword_expansions, invoke_llm_keyword_expand

router = APIRouter(tags=["model"])


@router.get("/api/tasks/{task_id}/prompt-log")
async def get_prompt_log(task_id: str):
    """Get the LLM prompt log for a task."""

    return prompt_log.get_log(task_id)


@router.delete("/api/tasks/{task_id}/prompt-log/{entry_id}")
async def delete_prompt_log_entry(task_id: str, entry_id: str):
    """Delete a single prompt log entry."""

    ok = prompt_log.delete_entry(task_id, entry_id)
    return {"ok": ok}


@router.post("/api/translate-review")
async def translate_review(req: TranslateReviewRequest):
    """Translate review content to Chinese."""
    llm = await asyncio.to_thread(get_llm)

    sys_prompt = """你是一位学术论文翻译专家。请将以下英文文献综述翻译为中文。

要求：
1. 保持学术性和专业性
2. 保留所有引用标记如 [1], [2] 等
3. 保持段落结构
4. 术语翻译要准确
5. 直接输出翻译结果，不要添加额外说明"""

    response = await asyncio.to_thread(
        llm.invoke,
        [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=f"请将以下文献综述翻译为中文：\n\n{req.content}"),
        ],
    )
    return {"translated": response.content.strip()}


@router.post("/api/expand-keywords")
async def expand_keywords(req: ExpandKeywordsRequest):
    """Expand each keyword individually with LLM-derived variants."""
    llm = await asyncio.to_thread(get_llm)

    groups = []
    for kw in req.keywords:
        if not kw.strip():
            continue
        group = await asyncio.to_thread(invoke_llm_keyword_expand, llm, kw)
        groups.append(group)

    if not groups:
        return {"keywords": req.keywords}

    return {"keywords": combine_keyword_expansions(groups)}
