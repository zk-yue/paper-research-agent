import os

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from state import ResearchState


def report_agent(state: ResearchState) -> dict:
    """Generate a weekly meeting report in Markdown format."""
    topic = state["topic"]
    literature_review = state["literature_review"]
    experiment_plan = state["experiment_plan"]
    reviewer_feedback = state["reviewer_feedback"]
    log = state.get("execution_log") or []

    log.append("📊 [ReportAgent] 开始生成组会报告...")
    log.append("🤖 [ReportAgent] 正在整合各模块输出（调用 LLM）...")

    system_prompt = """你是一位正在准备组会汇报的博士生。
你的任务是将文献调研、实验方案和审稿意见整合为一份简洁、专业的周报。

报告应包含：
1. 研究主题
2. 文献调研摘要（关键发现和研究空白）
3. 实验设计（假设、平台、指标）
4. 审稿意见摘要（主要问题和建议）
5. 行动计划 / 下周工作安排

请使用清晰、学术性的语气，适合研究组会讨论。
使用 Markdown 格式，包含适当的标题和列表。
保持简洁但全面。
请使用中文输出。"""

    user_prompt = f"""研究主题：{topic}

文献调研：
{literature_review}

实验方案：
{experiment_plan}

审稿意见：
{reviewer_feedback}

请基于以上内容生成一份组会周报。
使用 Markdown 格式输出。"""

    log.append("📤 [ReportAgent] 发送请求到 LLM...")
    log.append(f"📝 [ReportAgent] System Prompt: {system_prompt[:100]}...")
    log.append(f"📝 [ReportAgent] User Prompt (前 200 字符): {user_prompt[:200]}...")

    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    log.append(f"📥 [ReportAgent] LLM 响应 (前 200 字符): {response.content[:200]}...")

    os.makedirs("outputs", exist_ok=True)
    report_path = "outputs/weekly_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(response.content)

    log.append("✅ [ReportAgent] 组会报告生成完成！")
    log.append(f"💾 [ReportAgent] 报告已保存到 {report_path}")

    print(f"✅ Weekly report saved to {report_path}")
    return {"weekly_report": response.content, "execution_log": log}
