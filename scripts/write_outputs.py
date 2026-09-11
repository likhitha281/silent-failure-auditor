#!/usr/bin/env python3
"""Writes this action's declared outputs (findings, high-count, medium-count,
report-path) to $GITHUB_OUTPUT, so a calling workflow can branch on the
result (e.g. notify a channel only if high-count != '0') without parsing
the JSON report itself."""

import json
import os
import sys


def main():
    if len(sys.argv) != 2:
        print("Usage: write_outputs.py <report.json>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        report = json.load(f)

    steps = report["steps"]
    high = sum(1 for s in steps if s.get("status") == "flag" and s.get("severity") == "high")
    medium = sum(1 for s in steps if s.get("status") == "flag" and s.get("severity") == "medium")

    lines = [
        f"findings={high + medium}",
        f"high-count={high}",
        f"medium-count={medium}",
        f"report-path={sys.argv[1]}",
    ]

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
    else:
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()
