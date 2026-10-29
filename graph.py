from langgraph.graph import END, START, StateGraph

from agents import literature_agent
from state import ResearchState


def literature_node(state: ResearchState) -> dict:
    """Wrapper for literature agent."""
    return literature_agent(state)


def build_graph():
    """Build the research workflow graph."""
    graph = StateGraph(ResearchState)

    graph.add_node("literature_agent", literature_node)
    graph.add_edge(START, "literature_agent")
    graph.add_edge("literature_agent", END)

    return graph.compile()
