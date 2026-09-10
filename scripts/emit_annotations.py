#!/usr/bin/env python3
"""Emits GitHub Actions ::error/::warning workflow commands for each flagged
step in an sfa report, so findings appear directly in the workflow UI and
PR diff annotations, not just in a downloadable JSON report."""

import json
import sys

TYPE_TITLES = {
    "misread": "Silent Failure Auditor: misread success",
    "hallucinated": "Silent Failure Auditor: hallucinated path",
    "noop": "Silent Failure Auditor: no-op edit",
    "retry": "Silent Failure Auditor: possible retry loop",
}


def escape(s):
    # Per GitHub's workflow-command spec: % , \r and \n must be escaped
    # in both the title and message fields.
    return (s or "").replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main():
    if len(sys.argv) != 2:
        print("Usage: emit_annotations.py <report.json>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        report = json.load(f)

    for s in report["steps"]:
        if s.get("status") != "flag":
            continue
        level = "error" if s.get("severity") == "high" else "warning"
        title = TYPE_TITLES.get(s["type"], "Silent Failure Auditor")
        message = escape(s["verdict"])
        print(f"::{level} title={escape(title)}::{message}")


if __name__ == "__main__":
    main()
