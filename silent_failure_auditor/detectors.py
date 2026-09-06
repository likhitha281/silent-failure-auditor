"""
silent_failure_auditor.detectors -- the rule-based detection engine.

Same logic as the course baseline (run_baseline.py), packaged as an
importable module so both the CLI (`sfa`) and the standalone scripts can
share one implementation without drifting apart.
"""

import re

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


def severity_bucket(flag_type):
    return "medium" if flag_type in ("retry", "noop") else "high"


def run_detectors(steps):
    """Annotate each step in-place with status/type/severity/verdict."""
    for s in steps:
        s["status"] = None
        s["type"] = None
        s["severity"] = None
        s["verdict"] = None

    # Rule 1: retry loop -- same tool + args repeated 3+ times consecutively
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
        known_paths.update(m.group(0) for m in PATH_RE.finditer(s.get("args") or ""))

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
