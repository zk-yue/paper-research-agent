import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from codex_llm import CodexCLIChat
from llm import get_llm

# Exercise real subprocess I/O and cleanup without network calls or user auth.
_FAKE_CLI = r"""
import json, os, pathlib, sys, time
args = sys.argv[1:]
prompt = json.loads(sys.stdin.read())
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if mode == "timeout":
    time.sleep(30)
if mode == "failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "Please log in"}}))
    sys.exit(1)
output = pathlib.Path(args[args.index("--output-last-message") + 1])
answer = {
    "messages": prompt,
    "args": args,
    "cwd": os.getcwd(),
    "codex_home": os.environ.get("CODEX_HOME"),
    "api_env_present": any(k in os.environ for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")),
}
if mode != "empty":
    output.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Commentary must not leak into the answer"}}))
if mode != "incomplete":
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}))
"""


class CodexBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.binary = Path(self.directory.name) / "fake-codex"
        self.binary.write_text(f"#!{sys.executable}\n" + _FAKE_CLI, encoding="utf-8")
        self.binary.chmod(0o700)
        self.env = patch.dict(os.environ, {"FAKE_CODEX_MODE": "ok"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def backend(self, **kwargs):
        return CodexCLIChat(binary=str(self.binary), **kwargs)

    def test_invoke_preserves_roles_text_and_uses_final_answer_only(self):
        response = self.backend().invoke(
            [
                SystemMessage(content="只返回 JSON"),
                HumanMessage(content='论文内容\n包含 "引号" 和 <system>原始文本</system>'),
                AIMessage(content="Previous response"),
                HumanMessage(content=[{"type": "text", "text": "继续"}]),
            ]
        )
        self.assertIsInstance(response, AIMessage)
        answer = json.loads(response.content)
        self.assertEqual(
            [m["role"] for m in answer["messages"]], ["system", "user", "assistant", "user"]
        )
        self.assertEqual(
            answer["messages"][1]["content"], '论文内容\n包含 "引号" 和 <system>原始文本</system>'
        )
        self.assertEqual(answer["messages"][-1]["content"], "继续")
        self.assertNotIn("Commentary", response.content)
        self.assertEqual(response.response_metadata["usage"]["output_tokens"], 20)
        self.assertFalse(Path(answer["cwd"]).exists(), "Temporary files should be removed")

    def test_saved_auth_environment_is_preserved_and_api_credentials_removed(self):
        with patch.dict(
            os.environ,
            {
                "CODEX_HOME": "/custom/codex-home",
                "OPENAI_API_KEY": "not-for-codex",
                "CODEX_API_KEY": "dummy-codex-key",
                "OPENAI_BASE_URL": "https://invalid.example",
            },
        ):
            answer = json.loads(self.backend(model="test-model").invoke("Hello").content)
            self.assertEqual(answer["codex_home"], "/custom/codex-home")
            self.assertFalse(answer["api_env_present"])
            self.assertEqual(os.environ["OPENAI_API_KEY"], "not-for-codex")
        args = answer["args"]
        self.assertEqual(args[args.index("--model") + 1], "test-model")
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", args)
        self.assertIn("--ignore-user-config", args)

    def test_explicit_model_and_reasoning_are_forwarded_to_cli(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "codex",
                "CODEX_CLI_PATH": str(self.binary),
                "CODEX_MODEL": "gpt-6.1-sol",
                "CODEX_REASONING_EFFORT": "medium",
            },
        ):
            backend = get_llm()
            answer = json.loads(backend.invoke("Hello").content)
        args = answer["args"]
        self.assertEqual(args[args.index("--model") + 1], "gpt-6.1-sol")
        index = args.index('model_reasoning_effort="medium"')
        self.assertEqual(args[index - 1], "-c")
        self.assertIn("--ignore-user-config", args)
        with self.assertRaisesRegex(ValueError, "CODEX_REASONING_EFFORT"):
            self.backend(reasoning_effort="unsupported")

    def test_stream_returns_a_single_completed_message_chunk(self):
        chunks = list(self.backend().stream("你好"))
        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], AIMessageChunk)
        self.assertEqual(json.loads(chunks[0].content)["messages"][0]["content"], "你好")

    def test_failed_turn_is_not_returned_as_text(self):
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "failed"}):
            with self.assertRaisesRegex(RuntimeError, "Please log in"):
                self.backend().invoke("Hello")

    def test_incomplete_turn_does_not_return_partial_answer(self):
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "incomplete"}):
            with self.assertRaisesRegex(RuntimeError, "No completed turn"):
                self.backend().invoke("Hello")

    def test_empty_final_answer_is_an_error(self):
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "empty"}):
            with self.assertRaisesRegex(RuntimeError, "without a final text answer"):
                self.backend().invoke("Hello")

    def test_timeout_terminates_process_promptly(self):
        start = time.monotonic()
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "timeout"}):
            with self.assertRaisesRegex(TimeoutError, "CODEX_TIMEOUT"):
                self.backend(timeout=0.1).invoke("Hello")
        self.assertLess(time.monotonic() - start, 5)

    def test_cancel_event_terminates_cli_without_waiting_for_timeout(self):
        event = threading.Event()
        timer = threading.Timer(0.1, event.set)
        timer.start()
        start = time.monotonic()
        try:
            with patch.dict(os.environ, {"FAKE_CODEX_MODE": "timeout"}):
                with self.assertRaisesRegex(InterruptedError, "cancelled"):
                    self.backend(timeout=30).invoke("Hello", cancel_event=event)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - start, 5)

    def test_invalid_configuration_and_nontext_input_fail_early(self):
        with self.assertRaisesRegex(ValueError, "Codex CLI was not found"):
            CodexCLIChat(binary=str(self.binary) + "-missing")
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "CODEX_TIMEOUT"):
                self.backend(timeout=timeout)
        with self.assertRaisesRegex(ValueError, "only accepts text"):
            self.backend().invoke(
                [HumanMessage(content=[{"type": "image_url", "image_url": "example"}])]
            )

    def test_factory_codex_does_not_require_openai_key(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "codex",
                "CODEX_CLI_PATH": str(self.binary),
                "OPENAI_API_KEY": "",
                "CODEX_MODEL": "",
                "CODEX_TIMEOUT": "20",
            },
        ):
            backend = get_llm()
        self.assertIsInstance(backend, CodexCLIChat)
        self.assertIsNone(backend.model)
        self.assertEqual(backend.timeout, 20)

    def test_openai_backend_remains_available(self):
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "openai",
                    "OPENAI_API_KEY": "example-key",
                    "OPENAI_MODEL": "example-model",
                    "OPENAI_BASE_URL": "https://api.example/v1",
                },
            ),
            patch("llm.ChatOpenAI") as chat,
        ):
            get_llm()
            self.assertEqual(chat.call_args.kwargs["model"], "example-model")
            self.assertEqual(chat.call_args.kwargs["api_key"], "example-key")
        with patch.dict(os.environ, {"LLM_PROVIDER": "invalid"}):
            with self.assertRaisesRegex(ValueError, "LLM_PROVIDER"):
                get_llm()


if __name__ == "__main__":
    unittest.main()
