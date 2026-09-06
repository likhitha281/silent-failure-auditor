#!/usr/bin/env python3
"""
llm_explain.py -- optional LLM-confirmation pass over rule-flagged steps

This is a Phase-2 stretch addition, not part of the required rule-based
baseline (run_baseline.py). It takes each step the rule engine already
flagged and asks an LLM to independently confirm or challenge the flag,
producing a plain-language explanation grounded in the same evidence.

Why this exists: the rule engine is precise but rigid -- it needs near-exact
vocabulary matches (see Limitations in README.md). An LLM pass can catch
phrasing the regex rules miss and can push back on false positives the
rules raise, without replacing the interpretable rule layer underneath it.

Requires:
    pip install anthropic
    export ANTHROPIC_API_KEY=...      (macOS/Linux)
    setx ANTHROPIC_API_KEY "..."      (Windows, then restart the terminal)

Usage:
    python llm_explain.py --input examples/sample_rollout.jsonl
    python llm_explain.py --input examples/sample_rollout.jsonl --model claude-sonnet-5
    python llm_explain.py --input examples/sample_rollout.jsonl --output explained.json
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from run_baseline import normalize, run_detectors
except ImportError:
    print("Could not import run_baseline.py -- make sure llm_explain.py sits "
          "in the same folder as run_baseline.py.", file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    anthropic = None

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

PROMPT_TEMPLATE = """You are reviewing one step from a coding agent's session log. A rule-based detector already flagged this step as a possible silent failure. Judge independently whether this is a genuine silent failure or a false positive, using only the evidence given below -- do not assume anything that isn't shown here.

Tool call: {tool} {args}
Tool output: {output}
What the agent told the user next: {claim}
Rule-based flag: {flag_type} ({severity} severity)
Rule's stated reason: {verdict}

Respond with exactly two lines:
Verdict: <CONFIRMED or FALSE POSITIVE>
Explanation: <one or two plain-language sentences a developer could read in a report>
"""


def explain_step(client, model, step):
    prompt = PROMPT_TEMPLATE.format(
        tool=step["tool"],
        args=step.get("args") or "(none)",
        output=step.get("output") or "(empty)",
        claim=step.get("textAfter") or "(nothing captured)",
        flag_type=step["type"],
        severity=step["severity"],
        verdict=step["verdict"],
    )
    response = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    verdict_line = next((l for l in text.splitlines() if l.lower().startswith("verdict:")), "Verdict: UNKNOWN")
    explanation_line = next((l for l in text.splitlines() if l.lower().startswith("explanation:")), "Explanation: (no explanation returned)")
    return {
        "llm_verdict": verdict_line.split(":", 1)[1].strip(),
        "llm_explanation": explanation_line.split(":", 1)[1].strip(),
    }


def main():
    parser = argparse.ArgumentParser(description="LLM confirmation pass over rule-flagged steps")
    parser.add_argument("--input", required=True, help="Path to the same log run_baseline.py accepts")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Anthropic model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--output", help="Optional path to write the combined JSON report")
    args = parser.parse_args()

    if anthropic is None:
        print("The 'anthropic' package isn't installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    raw_text = input_path.read_text(encoding="utf-8")
    steps, error = normalize(raw_text)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    run_detectors(steps)
    flagged = [s for s in steps if s["status"] == "flag"]

    if not flagged:
        print("No rule-flagged steps to explain -- nothing to send to the LLM.")
        return

    try:
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    except Exception as e:
        print(f"Could not create an Anthropic client -- is ANTHROPIC_API_KEY set? ({e})", file=sys.stderr)
        sys.exit(1)

    print(f"\nLLM confirmation pass -- {len(flagged)} flagged step(s), model: {args.model}")
    print("-" * 60)
    for step in flagged:
        try:
            result = explain_step(client, args.model, step)
        except Exception as e:
            result = {"llm_verdict": "ERROR", "llm_explanation": f"LLM call failed: {e}"}
        step["llm_verdict"] = result["llm_verdict"]
        step["llm_explanation"] = result["llm_explanation"]

        print(f"  Step {step['step']:>3}  [{step['type']}/{step['severity']}]  {step['tool']}")
        print(f"    Rule verdict : {step['verdict']}")
        print(f"    LLM verdict  : {step['llm_verdict']}")
        print(f"    LLM says     : {step['llm_explanation']}\n")

    if args.output:
        Path(args.output).write_text(json.dumps(flagged, indent=2), encoding="utf-8")
        print(f"Full report written to {args.output}")


if __name__ == "__main__":
    main()
