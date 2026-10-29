import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from codex_llm import CodexCLIChat
from llm import model_metadata
from scripts import run_search_evaluation


@pytest.mark.parametrize("provider", ["openai", "codex"])
def test_live_evaluation_uses_configured_model_without_account_specific_guard(
    tmp_path, monkeypatch, provider
):
    if provider == "codex":
        # No subprocess is invoked: the workflow is replaced below.
        model = CodexCLIChat(binary=sys.executable, model="account-model", reasoning_effort="low")
    else:
        model = SimpleNamespace(model_name="api-model", api_key="dummy-private-key")
    monkeypatch.setattr(run_search_evaluation, "get_llm", lambda: model)

    async def search(**kwargs):
        return {"papers": [], "screening": {"counts": {}}, "coverage_status": "bounded_results"}

    monkeypatch.setattr(run_search_evaluation, "run_deep_search", search)
    args = SimpleNamespace(
        output=tmp_path / "evaluation",
        topic="Example research",
        year_from=2024,
        quick=True,
        rounds=1,
    )
    asyncio.run(run_search_evaluation.run(args))
    result = json.loads((args.output / "result.json").read_text())
    assert result["evaluation"]["provider"] == provider
    assert result["evaluation"]["model"] == (
        "account-model" if provider == "codex" else "api-model"
    )
    assert "dummy-private-key" not in json.dumps(result)


def test_model_metadata_contains_only_public_identity():
    model = SimpleNamespace(
        model_name="model-name", reasoning_effort="medium", api_key="dummy-private-key"
    )
    assert model_metadata(model) == {
        "provider": "openai",
        "model": "model-name",
        "reasoning_effort": "medium",
    }
