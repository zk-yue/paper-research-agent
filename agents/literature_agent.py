from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from state import ResearchState
from tools import (
    combine_keyword_expansions,
    filter_papers_by_relevance,
    format_papers_for_prompt,
    identify_representative_papers,
    invoke_llm_keyword_expand,
    search_papers_multi_query,
    translate_abstracts,
)


def expand_search_queries(keywords: list, log: list) -> list:
    """Use the shared conservative keyword expander for CLI/LangGraph runs."""
    log.append(f"🤖 [LiteratureAgent] 正在用 LLM 扩展 {len(keywords)} 个关键词...")
    try:
        llm = get_llm()
        groups = []
        for keyword in keywords:
            if "*" in keyword:
                groups.append([keyword])
            else:
                groups.append(invoke_llm_keyword_expand(llm, keyword))
        queries = combine_keyword_expansions(groups)[:25]
        log.append(f"✅ [LiteratureAgent] 扩展后共 {len(queries)} 个搜索关键词: {queries}")
        return queries
    except Exception as e:
        log.append(f"⚠️ [LiteratureAgent] 关键词扩展失败 ({e})，使用原始关键词: {keywords}")

    return keywords


def literature_agent(state: ResearchState) -> dict:
    """Search real papers and generate a structured literature review framework."""
    keywords = state.get("keywords", [])
    year_from = state.get("year_from", 2025)
    log = state.get("execution_log") or []

    log.append("📚 [LiteratureAgent] 开始执行文献调研...")
    log.append(f"📅 [LiteratureAgent] 检索年份范围: {year_from} 年至今")
    log.append(f"🔑 [LiteratureAgent] 用户关键词: {keywords}")

    queries = expand_search_queries(keywords, log)

    log.append(f"📡 [LiteratureAgent] 正在独立查询 arXiv 与 OpenAlex（{len(queries)} 组查询）...")
    papers = search_papers_multi_query(queries, max_results=50, year_from=year_from)
    log.append(f"✅ [LiteratureAgent] 共搜索到 {len(papers)} 篇论文（去重后，{year_from} 年至今）")

    diagnostics = list(getattr(papers, "diagnostics", []))
    warnings = list(getattr(papers, "warnings", []))
    used_queries = {" ".join(q.split()).casefold() for q in queries}
    untried_originals = [q for q in keywords if " ".join(q.split()).casefold() not in used_queries]
    if not papers and untried_originals:
        log.append("⚠️ [LiteratureAgent] 未取得候选，尝试尚未执行的原始关键词查询...")
        from tools import search_papers_per_query

        papers = search_papers_per_query(
            untried_originals, max_results_per_query=50, year_from=year_from
        )
        diagnostics.extend(getattr(papers, "diagnostics", []))
        warnings.extend(getattr(papers, "warnings", []))
        log.append(f"✅ [LiteratureAgent] 补充搜索到 {len(papers)} 篇论文")
    warnings = list(dict.fromkeys(warnings))
    log.extend(f"⚠️ {warning}" for warning in warnings)
    retrieval_details = {"search_diagnostics": diagnostics, "search_warnings": warnings}

    if not papers:
        log.append("未检索到论文，跳过相关度排序、摘要翻译与文献综述生成")
        return {
            "literature_review": "",
            "papers": [],
            "representative_papers": [],
            "execution_log": log,
            "screening": filter_papers_by_relevance([], ", ".join(keywords), None, log).screening,
            **retrieval_details,
        }

    llm = get_llm()
    papers = filter_papers_by_relevance(papers, ", ".join(keywords), llm, log)
    screening = papers.screening
    if not papers:
        log.append("暂无已确认直接相关论文，补充阅读及待复核候选已保留；跳过翻译与文献综述生成")
        return {
            "literature_review": "",
            "papers": [],
            "representative_papers": [],
            "execution_log": log,
            "screening": screening,
            **retrieval_details,
        }

    log.append("🔄 [LiteratureAgent] 按相关度排序论文...")
    representative, sorted_papers = identify_representative_papers(papers, top_k=5)
    log.append(f"⭐ [LiteratureAgent] 已识别 {len(representative)} 篇代表性论文")

    sorted_papers = translate_abstracts(sorted_papers, llm, log)

    representative = [
        p for p in sorted_papers if p.get("title") in {r.get("title") for r in representative}
    ]

    log.append("📝 [LiteratureAgent] 正在生成文献调研框架（调用 LLM）...")

    papers_text = format_papers_for_prompt(sorted_papers, representative)

    system_prompt = """你是一位具身智能和机器人领域的资深研究员。
你的任务是基于搜索到的真实论文，为给定的研究主题生成结构化的文献调研框架。
聚焦该领域的最新趋势、关键挑战和代表性工作。

请输出一份结构清晰的 Markdown 文档，包含以下章节：
1. 研究方向：3-5 个相关子方向
2. 关键术语：10-15 个重要关键词
3. 代表性论文分析：基于标记为 ⭐ 的代表性论文，详细分析其核心贡献和方法
4. 关键阅读问题：阅读该领域论文时需要重点关注的 5-8 个问题
5. 开放问题：当前的研究空白和机会

请针对具身智能、机器人学和长时域操作任务进行具体分析。
请使用中文输出。"""

    user_prompt = f"""研究主题：{", ".join(keywords)}

搜索到的相关论文（共 {len(sorted_papers)} 篇，⭐ 标记为代表性论文）：
{papers_text}

请基于以上真实论文，为该主题生成一份全面的文献调研框架。
使用 Markdown 格式输出。"""

    log.append("📤 [LiteratureAgent] 发送请求到 LLM...")
    log.append(f"📝 [LiteratureAgent] System Prompt: {system_prompt[:100]}...")
    log.append(f"📝 [LiteratureAgent] User Prompt (前 200 字符): {user_prompt[:200]}...")

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    log.append(f"📥 [LiteratureAgent] LLM 响应 (前 200 字符): {response.content[:200]}...")
    log.append("✅ [LiteratureAgent] 文献调研框架生成完成！")

    return {
        "literature_review": response.content,
        "papers": sorted_papers,
        "representative_papers": representative,
        "execution_log": log,
        "screening": screening,
        **retrieval_details,
    }
