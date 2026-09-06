"""
Unit tests for the rule-based detector in run_baseline.py.

Run with:
    pytest tests/ -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_baseline import normalize, run_detectors  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_and_run(filename):
    raw = (EXAMPLES / filename).read_text(encoding="utf-8")
    steps, error = normalize(raw)
    assert error is None, f"normalize() failed: {error}"
    run_detectors(steps)
    return steps


def test_sample_rollout_flags_expected_steps():
    steps = load_and_run("sample_rollout.jsonl")
    flagged = {s["step"]: s["type"] for s in steps if s["status"] == "flag"}
    assert flagged == {2: "misread", 3: "hallucinated", 6: "retry", 7: "noop"}


def test_sample_rollout_clean_steps_stay_clean():
    steps = load_and_run("sample_rollout.jsonl")
    clean_steps = {s["step"] for s in steps if s["status"] == "ok"}
    assert clean_steps == {1, 4, 5, 8}


def test_sample_generic_has_no_flags():
    steps = load_and_run("sample_generic.json")
    assert [s for s in steps if s["status"] == "flag"] == []


def test_retry_loop_needs_at_least_three_repeats():
    """Two identical failing calls with no success claim afterward should not be flagged --
    there's no repeated-call pattern yet (needs 3+) and no misread claim to catch either."""
    raw = """[
        {"tool":"run_command","args":"x","output":"network error"},
        {"tool":"run_command","args":"x","output":"network error"}
    ]"""
    steps, error = normalize(raw)
    assert error is None
    run_detectors(steps)
    assert all(s["status"] == "ok" for s in steps)


def test_retry_loop_detects_three_identical_calls():
    raw = """[
        {"tool":"run_command","args":"x","output":"error: fail"},
        {"tool":"run_command","args":"x","output":"error: fail"},
        {"tool":"run_command","args":"x","output":"error: fail","textAfter":"Done, moving on."}
    ]"""
    steps, error = normalize(raw)
    assert error is None
    run_detectors(steps)
    assert steps[-1]["status"] == "flag"
    assert steps[-1]["type"] == "retry"


def test_error_acknowledged_is_not_flagged():
    """If the agent's next message quotes the error, that's correct behavior -- not a silent failure."""
    raw = """[
        {"tool":"run_tests","args":"pytest","output":"1 failed","textAfter":"The test failed, investigating further."}
    ]"""
    steps, error = normalize(raw)
    assert error is None
    run_detectors(steps)
    assert steps[0]["status"] == "ok"
