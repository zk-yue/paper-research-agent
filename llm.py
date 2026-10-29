import os

from langchain_openai import ChatOpenAI

import settings  # noqa: F401 -- Load the project .env once, independently of cwd.


def model_metadata(model) -> dict:
    """Record reproducible model identity without serializing credentials or clients."""
    from codex_llm import CodexCLIChat

    name = getattr(model, "model", None) or getattr(model, "model_name", None)
    effort = getattr(model, "reasoning_effort", None)
    return {
        "provider": "codex" if isinstance(model, CodexCLIChat) else "openai",
        "model": name if isinstance(name, str) else None,
        "reasoning_effort": effort if isinstance(effort, str) else None,
    }


def get_llm():
    """Return the configured model backend with invoke() and stream() support."""
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider == "codex":
        from codex_llm import CodexCLIChat

        return CodexCLIChat(
            binary=os.getenv("CODEX_CLI_PATH", "codex"),
            model=os.getenv("CODEX_MODEL") or None,
            timeout=float(os.getenv("CODEX_TIMEOUT", "300")),
            reasoning_effort=os.getenv("CODEX_REASONING_EFFORT") or None,
        )
    if provider != "openai":
        raise ValueError("LLM_PROVIDER must be 'openai' or 'codex'.")

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. "
            "Please create a .env file with your API key or set it in your environment."
        )

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.7,
        request_timeout=300,
    )
