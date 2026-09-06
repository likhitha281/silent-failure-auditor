#!/usr/bin/env python3
"""
watch_live.py -- near real-time silent-failure auditing of a running Codex CLI session

Codex CLI writes its session log incrementally to a rollout-*.jsonl file as
the agent works (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl). This script
polls that file every couple of seconds, re-runs the same rule-based
detector from run_baseline.py, and prints new flags to the console the
moment they appear -- while the agent is still running.

Honest framing: this is polling, not true event streaming. Codex CLI
doesn't expose a socket or webhook, so watching the file it already writes
is the practical way to get "live" behavior without modifying Codex itself.

Usage:
    # Auto-find the most recently modified session under ~/.codex/sessions
    python watch_live.py

    # Watch a specific session file
    python watch_live.py --session "/path/to/rollout-....jsonl"

    # Change the poll interval (seconds)
    python watch_live.py --interval 1
"""

import argparse
import glob
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from run_baseline import normalize, run_detectors


def find_latest_session():
    home = Path.home()
    candidates = glob.glob(str(home / ".codex" / "sessions" / "**" / "rollout-*.jsonl"), recursive=True)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def start_demo_stream(dest_path, source_path, delay):
    """Write the bundled sample log into dest_path one line at a time, in the
    background, so --demo shows live detection without needing Codex CLI."""
    lines = Path(source_path).read_text(encoding="utf-8").splitlines(keepends=True)
    Path(dest_path).write_text("", encoding="utf-8")

    def _writer():
        for line in lines:
            time.sleep(delay)
            with open(dest_path, "a", encoding="utf-8") as out:
                out.write(line)

    threading.Thread(target=_writer, daemon=True).start()


def watch(path, interval):
    print(f"Watching: {path}")
    print(f"Polling every {interval}s. Press Ctrl+C to stop.\n")
    seen_flags = set()   # (step, tool, args) already reported this session
    last_step_count = 0

    while True:
        try:
            raw_text = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            print("Session file not found yet -- waiting for Codex to create it...")
            time.sleep(interval)
            continue

        steps, error = normalize(raw_text)
        if error or not steps:
            time.sleep(interval)
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

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="Near real-time silent-failure watcher for a live Codex CLI session"
    )
    parser.add_argument("--session", help="Path to a specific rollout-*.jsonl file. "
                         "If omitted, auto-finds the most recently modified session.")
    parser.add_argument("--interval", type=float, default=2.0,
                         help="Poll interval in seconds (default: 2.0)")
    parser.add_argument("--demo", action="store_true",
                         help="Stream the bundled examples/sample_rollout.jsonl into a "
                              "temp file gradually, so you can see live detection work "
                              "without Codex CLI installed.")
    args = parser.parse_args()

    if args.demo:
        demo_source = Path(__file__).resolve().parent / "examples" / "sample_rollout.jsonl"
        if not demo_source.exists():
            print(f"Demo source not found: {demo_source}", file=sys.stderr)
            sys.exit(1)
        demo_dest = Path(tempfile.gettempdir()) / "watch_live_demo.jsonl"
        print(f"Demo mode: streaming {demo_source.name} into a temp file over time.\n")
        start_demo_stream(demo_dest, demo_source, delay=max(args.interval / 2, 0.5))
        session_path = str(demo_dest)
    else:
        session_path = args.session or find_latest_session()
        if not session_path:
            print("No Codex CLI session found under ~/.codex/sessions. "
                  "Start a Codex session first, pass --session explicitly, "
                  "or use --demo to try it without Codex installed.", file=sys.stderr)
            sys.exit(1)

    try:
        watch(session_path, args.interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")


if __name__ == "__main__":
    main()
