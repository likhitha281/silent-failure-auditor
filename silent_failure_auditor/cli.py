#!/usr/bin/env python3
"""
sfa -- Silent Failure Auditor CLI

    sfa audit <log>      Run the rule-based baseline against a log file.
    sfa watch            Tail a live Codex CLI session and flag failures as they happen.
    sfa explain <log>    Send rule-flagged steps to an LLM for a second opinion.
"""

import argparse
import glob
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from silent_failure_auditor.adapters import normalize
from silent_failure_auditor.detectors import run_detectors, severity_bucket, truncate


# ---------------------------------------------------------------------- audit

def cmd_audit(args):
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    steps, fmt, error = normalize(input_path.read_text(encoding="utf-8"))
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    run_detectors(steps)
    flagged = [s for s in steps if s["status"] == "flag"]
    high = sum(1 for s in flagged if severity_bucket(s["type"]) == "high")
    med = sum(1 for s in flagged if severity_bucket(s["type"]) == "medium")

    print(f"\nSilent Failure Auditor -- format detected: {fmt}")
    print("-" * 60)
    print(f"Steps: {len(steps)}   Flagged: {len(flagged)} (high: {high}, medium: {med})   "
          f"Clean: {len(steps) - len(flagged)}\n")

    rows = flagged if args.flagged_only else steps
    for s in rows:
        tag = f"[{s['type']}/{s['severity']}]" if s["status"] == "flag" else "[ok]"
        print(f"  {s['step']:>3}  {tag:<16} {s['tool']:<18} {truncate(s.get('args'), 40)}")
        if s["status"] == "flag":
            print(f"       -> {s['verdict']}\n")

    if args.output:
        report = {
            "input_file": str(input_path), "format": fmt,
            "total_steps": len(steps), "flagged": len(flagged), "steps": steps,
        }
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.output}")

    return 0


# ---------------------------------------------------------------------- watch

def find_latest_session():
    home = Path.home()
    candidates = glob.glob(str(home / ".codex" / "sessions" / "**" / "rollout-*.jsonl"), recursive=True)
    return max(candidates, key=os.path.getmtime) if candidates else None


def start_demo_stream(dest_path, source_path, delay):
    lines = Path(source_path).read_text(encoding="utf-8").splitlines(keepends=True)
    Path(dest_path).write_text("", encoding="utf-8")

    def _writer():
        for line in lines:
            time.sleep(delay)
            with open(dest_path, "a", encoding="utf-8") as out:
                out.write(line)

    threading.Thread(target=_writer, daemon=True).start()


def cmd_watch(args):
    if args.demo:
        demo_source = Path(__file__).resolve().parent.parent / "examples" / "sample_rollout.jsonl"
        if not demo_source.exists():
            print(f"Demo source not found: {demo_source}", file=sys.stderr)
            return 1
        session_path = str(Path(tempfile.gettempdir()) / "sfa_watch_demo.jsonl")
        print(f"Demo mode: streaming {demo_source.name} into a temp file over time.\n")
        start_demo_stream(session_path, demo_source, delay=max(args.interval / 2, 0.5))
    else:
        session_path = args.session or find_latest_session()
        if not session_path:
            print("No Codex CLI session found under ~/.codex/sessions. Start a session "
                  "first, pass --session explicitly, or use --demo.", file=sys.stderr)
            return 1

    print(f"Watching: {session_path}")
    print(f"Polling every {args.interval}s. Press Ctrl+C to stop.\n")
    seen_flags = set()
    last_step_count = 0

    try:
        while True:
            try:
                raw_text = Path(session_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                time.sleep(args.interval)
                continue

            steps, fmt, error = normalize(raw_text)
            if error or not steps:
                time.sleep(args.interval)
                continue

            run_detectors(steps)
            if len(steps) > last_step_count:
                print(f"... {len(steps)} step(s) seen so far")
                last_step_count = len(steps)

            for s in steps:
                if s["status"] != "flag":
                    continue
                key = (s["step"], s["tool"], s.get("args"))
                if key in seen_flags:
                    continue
                seen_flags.add(key)
                print(f"\n[NEW FLAG] Step {s['step']:>3}  {s['type']}/{s['severity']}  "
                      f"{s['tool']} {s.get('args') or ''}")
                print(f"    -> {s['verdict']}\n")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")
    return 0


# -------------------------------------------------------------------- explain

def cmd_explain(args):
    try:
        import anthropic
    except ImportError:
        print("The 'anthropic' package isn't installed. Run: pip install "
              "silent-failure-auditor[llm]", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    steps, fmt, error = normalize(input_path.read_text(encoding="utf-8"))
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    run_detectors(steps)
    flagged = [s for s in steps if s["status"] == "flag"]
    if not flagged:
        print("No rule-flagged steps to explain.")
        return 0

    try:
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"Could not create an Anthropic client -- is ANTHROPIC_API_KEY set? ({e})", file=sys.stderr)
        return 1

    prompt_template = """You are reviewing one step from a coding agent's session log. A rule-based detector already flagged this step as a possible silent failure. Judge independently whether this is a genuine silent failure or a false positive, using only the evidence given below.

Tool call: {tool} {args}
Tool output: {output}
What the agent told the user next: {claim}
Rule-based flag: {flag_type} ({severity} severity)
Rule's stated reason: {verdict}

Respond with exactly two lines:
Verdict: <CONFIRMED or FALSE POSITIVE>
Explanation: <one or two plain-language sentences>
"""

    print(f"\nLLM confirmation pass -- {len(flagged)} flagged step(s), model: {args.model}")
    print("-" * 60)
    for step in flagged:
        prompt = prompt_template.format(
            tool=step["tool"], args=step.get("args") or "(none)",
            output=step.get("output") or "(empty)", claim=step.get("textAfter") or "(nothing captured)",
            flag_type=step["type"], severity=step["severity"], verdict=step["verdict"],
        )
        try:
            response = client.messages.create(model=args.model, max_tokens=200,
                                               messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            v_line = next((l for l in text.splitlines() if l.lower().startswith("verdict:")), "Verdict: UNKNOWN")
            e_line = next((l for l in text.splitlines() if l.lower().startswith("explanation:")), "Explanation: (none)")
            step["llm_verdict"] = v_line.split(":", 1)[1].strip()
            step["llm_explanation"] = e_line.split(":", 1)[1].strip()
        except Exception as e:
            step["llm_verdict"] = "ERROR"
            step["llm_explanation"] = f"LLM call failed: {e}"

        print(f"  Step {step['step']:>3}  [{step['type']}/{step['severity']}]  {step['tool']}")
        print(f"    Rule verdict : {step['verdict']}")
        print(f"    LLM verdict  : {step['llm_verdict']}")
        print(f"    LLM says     : {step['llm_explanation']}\n")

    if args.output:
        Path(args.output).write_text(json.dumps(flagged, indent=2), encoding="utf-8")
        print(f"Full report written to {args.output}")
    return 0


# ----------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(prog="sfa", description="Silent Failure Auditor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Run the rule-based baseline against a log file")
    p_audit.add_argument("input")
    p_audit.add_argument("--output")
    p_audit.add_argument("--flagged-only", action="store_true")
    p_audit.set_defaults(func=cmd_audit)

    p_watch = sub.add_parser("watch", help="Tail a live Codex CLI session")
    p_watch.add_argument("--session")
    p_watch.add_argument("--interval", type=float, default=2.0)
    p_watch.add_argument("--demo", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    p_explain = sub.add_parser("explain", help="LLM confirmation pass over rule-flagged steps")
    p_explain.add_argument("input")
    p_explain.add_argument("--model", default="claude-haiku-4-5-20251001")
    p_explain.add_argument("--output")
    p_explain.set_defaults(func=cmd_explain)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
