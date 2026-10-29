from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from state import ResearchState


def reviewer_agent(state: ResearchState) -> dict:
    """Simulate a top-conference reviewer to critique the experiment plan."""
    topic = state["topic"]
    experiment_plan = state["experiment_plan"]
    log = state.get("execution_log") or []

    log.append("📝 [ReviewerAgent] 开始执行审稿...")
    log.append("🔍 [ReviewerAgent] 正在分析实验方案...")
    log.append("🤖 [ReviewerAgent] 正在生成审稿意见（调用 LLM）...")

    system_prompt = """你是一位顶级机器人/AI 会议（如 CoRL、RSS、ICRA、NeurIPS）的严格审稿人。
你的任务是对实验方案进行严谨、建设性的评审。

请从以下维度进行评估：
1. 声明强度：研究声明是否过于激进还是合理界定？
2. 基线覆盖：基线方法是否充分且公平？
3. 指标充分性：评估指标是否全面捕捉了关键方面？
4. 实验漏洞：是否有缺失的实验或边缘情况？
5. 可复现性：实验设置是否可复现？
6. 统计严谨性：是否存在统计显著性方面的隐患？

请按以下格式给出评审意见：
- 摘要（2-3 句话）
- 优点（3-5 条）
- 不足（3-5 条）
- 作者需回答的问题（2-3 个）
- 总体评分（1-10 分，并说明理由）
- 建议：接收 / 弱接收 / 边缘 / 弱拒稿 / 拒稿

请保持公正但严谨，同时指出优点和不足。
请使用中文输出。"""

    user_prompt = f"""研究主题：{topic}

实验方案：
{experiment_plan}

请以严格的同行评审专家身份审查该实验方案。
使用 Markdown 格式输出。"""

    log.append("📤 [ReviewerAgent] 发送请求到 LLM...")
    log.append(f"📝 [ReviewerAgent] System Prompt: {system_prompt[:100]}...")
    log.append(f"📝 [ReviewerAgent] User Prompt (前 200 字符): {user_prompt[:200]}...")

    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    log.append(f"📥 [ReviewerAgent] LLM 响应 (前 200 字符): {response.content[:200]}...")
    log.append("✅ [ReviewerAgent] 审稿意见生成完成！")

    print("✅ Reviewer feedback generated.")
    return {"reviewer_feedback": response.content, "execution_log": log}
