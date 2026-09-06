#!/usr/bin/env python3
"""Exit non-zero if any flagged step in an sfa audit report meets or exceeds
the given severity threshold. Used by action.yml to fail a CI job."""

import json
import sys

SEVERITY_ORDER = {"none": 0, "medium": 1, "high": 2}


def main():
    if len(sys.argv) != 3:
        print("Usage: check_severity.py <report.json> <fail-on: none|medium|high>", file=sys.stderr)
        sys.exit(2)

    report_path, fail_on = sys.argv[1], sys.argv[2]
    threshold = SEVERITY_ORDER.get(fail_on, 2)

    if threshold == 0:
        print("fail-on=none -- not failing the build regardless of flags.")
        return

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    offending = [
        s for s in report["steps"]
        if s.get("status") == "flag" and SEVERITY_ORDER.get(s.get("severity"), 0) >= threshold
    ]

    if offending:
        print(f"Silent Failure Auditor found {len(offending)} step(s) at or above "
              f"'{fail_on}' severity:")
        for s in offending:
            print(f"  step {s['step']}: {s['type']}/{s['severity']} -- {s['tool']} {s.get('args') or ''}")
        sys.exit(1)

    print(f"No steps at or above '{fail_on}' severity. Passing.")


if __name__ == "__main__":
    main()
