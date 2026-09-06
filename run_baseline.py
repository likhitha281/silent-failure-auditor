#!/usr/bin/env python3
"""
run_baseline.py — Silent-Failure Auditor, rule-based baseline

Ingests an agent tool-call log (Codex CLI rollout JSONL, an Anthropic/OpenAI
messages transcript, or a generic event list) and flags silent failures:
misread success, retry loops, hallucinated file paths, and no-op edits.

Usage:
    python run_baseline.py --input examples/sample_rollout.jsonl
    python run_baseline.py --input examples/sample_generic.json --output report.json
    python run_baseline.py --input examples/sample_rollout.jsonl --flagged-only

This is a rule-based baseline (course requirement: "a rule-based scan of one
log file for a few known failure signatures"). No model calls, no external
dependencies beyond the Python standard library.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Detection signatures
# ---------------------------------------------------------------------------

ERROR_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|denied|non-zero|"
    r"timed?\s?out|refused|connectionerror)\b", re.IGNORECASE
)
SUCCESS_RE = re.compile(
    r"\b(done|success(ful|fully)?|passed|passing|fixed|completed|resolved|"
    r"works now|verified|no issues|all good)\b|\u2713", re.IGNORECASE
)
NOTFOUND_RE = re.compile(
    r"\benoent\b|\bno such file\b|\bfile does not exist\b|\bpath does not exist\b",
    re.IGNORECASE
)
NOOP_RE = re.compile(
    r"\b0 (hunks|changes|files changed)\b|\bno changes (made|applied)\b|"
    r"\bnothing to (commit|apply)\b|\btarget text not found\b|"
    r"\balready up to date\b|\bunmodified\b", re.IGNORECASE
)
PATH_RE = re.compile(
    r"\b[\w.\-/]+\.(py|js|ts|tsx|jsx|md|json|yaml|yml|txt|cfg|toml|go|rs|java|rb|c|cpp|h)\b"
)
EDIT_TOOLS_RE = re.compile(r"edit_file|write_file|apply_patch|^patch$|str_replace", re.IGNORECASE)


def truncate(s, n=60):
    s = s or ""
    return s if len(s) <= n else s[:n] + "\u2026"


def first_match(s, pattern):
    m = pattern.search(s or "")
    return m.group(0) if m else ""


def same_call(a, b):
    return a["tool"] == b["tool"] and (a.get("args") or "") == (b.get("args") or "")


# ---------------------------------------------------------------------------
# Normalizers — one per supported log shape
# ---------------------------------------------------------------------------

def block_text(content):
    """Extract plain text from a string or a list of {type, text} content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def normalize_generic_events(data):
    """Array of {tool, args, output, textAfter} objects — the simplest schema."""
    steps = []
    for i, it in enumerate(data):
        args = it.get("args", it.get("arguments", ""))
        if not isinstance(args, str):
            args = json.dumps(args)
        output = it.get("output", it.get("result", ""))
        if not isinstance(output, str):
            output = json.dumps(output)
        steps.append({
            "step": i + 1,
            "time": it.get("time", it.get("timestamp", "")),
            "tool": it.get("tool", it.get("tool_name", it.get("name", "unknown"))),
            "args": args,
            "output": output,
            "textBefore": it.get("textBefore", it.get("agent_note", "")),
            "textAfter": it.get("textAfter", it.get("assistant_text", it.get("note", ""))),
        })
    return steps


def normalize_messages_transcript(messages):
    """Anthropic/OpenAI-style {role, content:[...]} transcript with tool_use/tool_result blocks."""
    steps = []
    pending = {}
    text_buffer = ""
    for msg in messages:
        content = msg.get("content", "")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for b in blocks:
            btype = b.get("type")
            if btype == "text":
                text_buffer += (" " if text_buffer else "") + (b.get("text") or "")
                if steps:
                    steps[-1]["textAfter"] = (steps[-1].get("textAfter") or "") + " " + (b.get("text") or "")
            elif btype == "tool_use":
                s = {
                    "step": len(steps) + 1, "time": "", "tool": b.get("name", "unknown"),
                    "args": b.get("input") if isinstance(b.get("input"), str) else json.dumps(b.get("input", {})),
                    "output": "", "textBefore": text_buffer, "textAfter": "",
                }
                steps.append(s)
                pending[b.get("id")] = s
                text_buffer = ""
            elif btype == "tool_result":
                target = pending.get(b.get("tool_use_id"))
                if target is not None:
                    target["output"] = block_text(b.get("content"))
    return steps


def normalize_codex_rollout(lines):
    """
    Real Codex CLI session format: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    Each line is {timestamp, type, payload}. Tool calls and their outputs are
    both type:"response_item", linked by a flat payload.call_id.
    """
    steps = []
    pending = {}
    text_buffer = ""
    for line in lines:
        if line.get("type") != "response_item" or "payload" not in line:
            continue
        p = line["payload"]
        ptype = p.get("type")
        if ptype == "function_call":
            args_str = p.get("arguments", "")
            try:
                args_str = json.dumps(json.loads(args_str))
            except (TypeError, ValueError):
                pass
            s = {
                "step": len(steps) + 1, "time": line.get("timestamp", ""),
                "tool": p.get("name", "unknown"), "args": args_str or "",
                "output": "", "textBefore": text_buffer, "textAfter": "",
            }
            steps.append(s)
            pending[p.get("call_id")] = s
            text_buffer = ""
        elif ptype == "function_call_output":
            target = pending.get(p.get("call_id"))
            if target is not None:
                out = p.get("output", "")
                target["output"] = out if isinstance(out, str) else json.dumps(out)
        elif ptype == "message":
            text = block_text(p.get("content"))
            if p.get("role") == "assistant":
                text_buffer += (" " if text_buffer else "") + text
                if steps:
                    steps[-1]["textAfter"] = (steps[-1].get("textAfter") or "") + " " + text
    return steps


def normalize(raw_text):
    """Auto-detect the log shape and dispatch to the right normalizer."""
    data = None
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        lines = [l for l in (ln.strip() for ln in raw_text.splitlines()) if l]
        try:
            data = [json.loads(l) for l in lines]
        except json.JSONDecodeError:
            return None, "Could not parse input as JSON or JSON-lines."

    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return normalize_messages_transcript(data["messages"]), None

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("type"), str) and "payload" in first:
            return normalize_codex_rollout(data), None
        if isinstance(first, dict) and "role" in first and "content" in first:
            return normalize_messages_transcript(data), None
        if isinstance(first, dict) and ("tool" in first or "tool_name" in first or "name" in first):
            return normalize_generic_events(data), None

    return None, "Unrecognized log shape. See the module docstring for supported formats."


# ---------------------------------------------------------------------------
# Rule-based detector
# ---------------------------------------------------------------------------

def severity_bucket(flag_type):
    return "medium" if flag_type in ("retry", "noop") else "high"


def run_detectors(steps):
    """Annotate each step in-place with status/type/severity/verdict."""
    for s in steps:
        s["status"] = None
        s["type"] = None
        s["severity"] = None
        s["verdict"] = None

    # Rule 1: retry loop — same tool + args repeated 3+ times consecutively
    i = 0
    while i < len(steps):
        j = i
        while j + 1 < len(steps) and same_call(steps[j + 1], steps[i]):
            j += 1
        run_len = j - i + 1
        if run_len >= 3:
            s = steps[j]
            s["status"] = "flag"
            s["type"] = "retry"
            s["severity"] = "medium"
            s["verdict"] = (
                f"The same call ({s['tool']} {truncate(s.get('args'), 50)}) was repeated "
                f"{run_len} times in a row with no change in outcome, and the run then "
                f"proceeds as if it had succeeded."
            )
        i = j + 1

    known_paths = set()
    for s in steps:
        known_paths.update(PATH_RE.findall(s.get("args") or ""))
        # findall with a group returns the captured group, not the full match;
        # re-run with finditer to keep the whole path string
    known_paths = set()
    for s in steps:
        known_paths.update(m.group(0) for m in PATH_RE.finditer(s.get("args") or ""))

    # Rules 2-4: per-step checks, in priority order
    for s in steps:
        if s["status"]:
            continue
        out = s.get("output") or ""
        claim = s.get("textAfter") or ""
        has_error = bool(ERROR_RE.search(out))
        claims_success = bool(SUCCESS_RE.search(claim))
        claim_quotes_error = bool(ERROR_RE.search(claim))

        if EDIT_TOOLS_RE.search(s["tool"]) and NOTFOUND_RE.search(out) and claims_success:
            s["status"] = "flag"
            s["type"] = "hallucinated"
            s["severity"] = "high"
            s["verdict"] = (
                f"The tool call for {s.get('args') or 'this path'} returned "
                f"\"{first_match(out, NOTFOUND_RE)}\", meaning the file was never actually "
                f"reached, but the following message describes the edit as done."
            )
            continue

        if NOOP_RE.search(out) and claims_success:
            s["status"] = "flag"
            s["type"] = "noop"
            s["severity"] = "medium"
            s["verdict"] = (
                f"The tool reported no effective change (\"{first_match(out, NOOP_RE)}\"), "
                f"but the following message describes the fix as applied."
            )
            continue

        if has_error and claims_success and not claim_quotes_error:
            s["status"] = "flag"
            s["type"] = "misread"
            s["severity"] = "high"
            s["verdict"] = (
                f"The tool output indicates a failure (\"{first_match(out, ERROR_RE)}\"), "
                f"but the following message claims success and does not reference the failure."
            )
            continue

        mentioned = [m.group(0) for m in PATH_RE.finditer(claim)]
        ghost = next((p for p in mentioned if p not in known_paths), None)
        if ghost and claims_success:
            s["status"] = "flag"
            s["type"] = "hallucinated"
            s["severity"] = "high"
            s["verdict"] = (
                f"The message references {ghost}, which never appears as an argument to "
                f"any tool call in this run."
            )
            continue

        s["status"] = "ok"

    return steps


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(steps, flagged_only=False):
    flagged = [s for s in steps if s["status"] == "flag"]
    high = sum(1 for s in flagged if severity_bucket(s["type"]) == "high")
    med = sum(1 for s in flagged if severity_bucket(s["type"]) == "medium")
    clean = sum(1 for s in steps if s["status"] == "ok")

    print(f"\nSilent-Failure Auditor — baseline scan")
    print(f"{'-' * 60}")
    print(f"Steps: {len(steps)}   Flagged: {len(flagged)} "
          f"(high: {high}, medium: {med})   Clean: {clean}\n")

    rows = flagged if flagged_only else steps
    if not rows:
        print("No steps to show.")
        return

    for s in rows:
        tag = f"[{s['type']}/{s['severity']}]" if s["status"] == "flag" else "[ok]"
        print(f"  {s['step']:>3}  {tag:<16} {s['tool']:<18} {truncate(s.get('args'), 40)}")
        if s["status"] == "flag":
            print(f"       -> {s['verdict']}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Silent-Failure Auditor — rule-based baseline detector"
    )
    parser.add_argument("--input", required=True, help="Path to the log file "
                         "(Codex CLI rollout .jsonl, a messages-transcript .json, "
                         "or a generic event-list .json)")
    parser.add_argument("--output", help="Optional path to write the full JSON report")
    parser.add_argument("--flagged-only", action="store_true",
                         help="Only print flagged steps to the console")
    args = parser.parse_args()

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
    print_summary(steps, flagged_only=args.flagged_only)

    if args.output:
        report = {
            "input_file": str(input_path),
            "total_steps": len(steps),
            "flagged": sum(1 for s in steps if s["status"] == "flag"),
            "steps": steps,
        }
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.output}")


if __name__ == "__main__":
    main()
