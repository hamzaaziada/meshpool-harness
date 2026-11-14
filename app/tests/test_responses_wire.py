"""v2 #6: Responses API wire format (the route that cached at 84-93% for Codex). Pure conversion tests."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.core.wire import from_responses, to_responses  # noqa: E402


def test_round_trip_tools_and_history():
    body = {"model": "m", "max_tokens": 100, "temperature": 0.2,
            "tools": [{"type": "function", "function": {"name": "bash", "description": "d", "parameters": {"type": "object"}}}],
            "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
                         {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}]},
                         {"role": "tool", "tool_call_id": "c1", "content": "a.py"}]}
    r = to_responses(body)
    kinds = [i.get("type") or i.get("role") for i in r["input"]]
    assert kinds == ["system", "user", "function_call", "function_call_output"]
    assert r["tools"][0]["name"] == "bash" and r["max_output_tokens"] == 100


def test_reply_conversion():
    p = {"output": [{"type": "reasoning"}, {"type": "function_call", "call_id": "c9", "name": "finish",
                                            "arguments": json.dumps({"answer": "ok"})}],
         "usage": {"input_tokens": 1000, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 900}}}
    c = from_responses(p)
    m = c["choices"][0]["message"]
    assert m["tool_calls"][0]["function"]["name"] == "finish" and m["tool_calls"][0]["id"] == "c9"
    assert c["usage"]["prompt_tokens"] == 1000 and c["usage"]["prompt_tokens_details"]["cached_tokens"] == 900


def test_json_mode():
    r = to_responses({"model": "m", "messages": [{"role": "user", "content": "x"}], "response_format": {"type": "json_object"}})
    assert r["text"]["format"]["type"] == "json_object"


@__import__("pytest").mark.skipif(not (__import__("pathlib").Path(__file__).resolve().parents[1] / "eval").exists(), reason="needs the benchmark suite (eval/)")
def test_package_and_benchmark_copies_agree():
    from eval import ledger
    from meshpool_harness.core import wire
    body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}]}
    assert ledger.to_responses(body) == wire.to_responses(body)
    p = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}], "usage": {}}
    assert ledger.from_responses(p) == wire.from_responses(p)
