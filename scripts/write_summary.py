#!/usr/bin/env python3
"""Writes a Markdown summary of an sfa report to $GITHUB_STEP_SUMMARY, so
findings are readable directly in the workflow run UI instead of requiring
a JSON artifact download."""

import json
import os
import sys

TYPE_LABELS = {
    "misread": "Misread success",
    "hallucinated": "Hallucinated path",
    "noop": "No-op edit",
    "retry": "Retry loop",
}


def main():
    if len(sys.argv) != 2:
        print("Usage: write_summary.py <report.json>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        report = json.load(f)

    steps = report["steps"]
    high = [s for s in steps if s.get("status") == "flag" and s.get("severity") == "high"]
    med = [s for s in steps if s.get("status") == "flag" and s.get("severity") == "medium"]
    clean = [s for s in steps if s.get("status") == "ok"]

    lines = ["## Silent Failure Auditor", ""]
    lines.append(f"- 🔴 {len(high)} high severity findings")
    lines.append(f"- 🟡 {len(med)} medium severity findings")
    lines.append(f"- 🟢 {len(clean)} clean steps")
    lines.append("")

    for heading, group in (("High severity", high), ("Medium severity", med)):
        if not group:
            continue
        lines.append(f"### {heading}")
        lines.append("")
        for s in group:
            lines.append(f"**{TYPE_LABELS.get(s['type'], s['type'])}**  ")
            lines.append(s["verdict"])
            lines.append("")

    lines.append(f"Full report: `{report.get('total_steps')}` steps audited, "
                  f"`{report.get('flagged')}` flagged. See the `sfa-report` "
                  f"workflow artifact for the complete per-step JSON.")

    text = "\n".join(lines) + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        # Not running inside GitHub Actions -- print instead of failing,
        # so this is still usable/testable locally.
        print(text)


if __name__ == "__main__":
    main()
