"""Tests for the silent_failure_auditor package (adapters + detectors)."""

from pathlib import Path

from silent_failure_auditor.adapters import normalize
from silent_failure_auditor.detectors import run_detectors

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_and_run(filename):
    raw = (EXAMPLES / filename).read_text(encoding="utf-8")
    steps, fmt, error = normalize(raw)
    assert error is None, f"normalize() failed: {error}"
    run_detectors(steps)
    return steps, fmt


def test_codex_rollout_format_detected_and_flagged():
    steps, fmt = load_and_run("sample_rollout.jsonl")
    assert fmt == "codex_rollout"
    flagged = {s["step"]: s["type"] for s in steps if s["status"] == "flag"}
    assert flagged == {2: "misread", 3: "hallucinated", 6: "retry", 7: "noop"}


def test_generic_events_format_detected():
    steps, fmt = load_and_run("sample_generic.json")
    assert fmt == "generic_events"
    assert all(s["status"] == "ok" for s in steps)


def test_anthropic_transcript_adapter():
    raw = """{
        "messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "run_tests", "input": {"cmd": "pytest"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "1 failed, 3 passed"}
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "All tests are passing now."}
            ]}
        ]
    }"""
    steps, fmt, error = normalize(raw)
    assert error is None
    assert fmt == "anthropic_openai_transcript"
    run_detectors(steps)
    assert steps[0]["status"] == "flag"
    assert steps[0]["type"] == "misread"


def test_aider_markdown_adapter_parses_edit_and_shell_blocks():
    raw = """#### fix bug

src/app.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE

```bash
pytest
```
"""
    steps, fmt, error = normalize(raw)
    assert error is None
    assert fmt == "aider_markdown"
    tools = [s["tool"] for s in steps]
    assert "edit_file" in tools
    assert "run_command" in tools


def test_unrecognized_format_returns_error():
    steps, fmt, error = normalize("not json and not aider markdown at all")
    assert steps is None
    assert error is not None
