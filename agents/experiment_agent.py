from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from state import ResearchState


def experiment_agent(state: ResearchState) -> dict:
    """Design an experiment plan based on the topic and literature review."""
    topic = state["topic"]
    literature_review = state["literature_review"]
    log = state.get("execution_log") or []

    log.append("🧪 [ExperimentAgent] 开始执行实验设计...")
    log.append("📊 [ExperimentAgent] 正在分析文献调研结果...")
    log.append("🤖 [ExperimentAgent] 正在生成实验方案（调用 LLM）...")

    system_prompt = """你是一位具身智能实验设计专家。
你的任务是为研究项目创建详细的实验方案。

请输出一份结构清晰的 Markdown 文档，包含以下章节：
1. 研究假设：清晰、可检验的假设
2. 仿真环境：指定仿真器（Isaac Sim、MuJoCo 等）和任务
3. 真实世界设置：如适用，描述物理机器人平台
4. 基线方法：3-5 个用于对比的基线
5. 评估指标：4-6 个指标及其合理性说明
6. 消融实验：3-4 个消融实验
7. 预期结果：哪些结果能支持假设
8. 风险分析：潜在问题及缓解策略

请针对具身智能和长时域操作任务进行具体设计。
请使用中文输出。"""

    user_prompt = f"""研究主题：{topic}

文献调研：
{literature_review}

基于以上内容，请设计一份全面的实验方案。
使用 Markdown 格式输出。"""

    log.append("📤 [ExperimentAgent] 发送请求到 LLM...")
    log.append(f"📝 [ExperimentAgent] System Prompt: {system_prompt[:100]}...")
    log.append(f"📝 [ExperimentAgent] User Prompt (前 200 字符): {user_prompt[:200]}...")

    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    log.append(f"📥 [ExperimentAgent] LLM 响应 (前 200 字符): {response.content[:200]}...")
    log.append("✅ [ExperimentAgent] 实验方案生成完成！")

    print("✅ Experiment plan generated.")
    return {"experiment_plan": response.content, "execution_log": log}
