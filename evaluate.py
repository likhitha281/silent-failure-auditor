#!/usr/bin/env python3
"""
evaluate.py -- compute precision/recall/F1 for the rule-based detector
against a hand-labeled set of logs.

This is NOT the same as running the detector on the examples it was built
against (examples/sample_rollout.jsonl, sample_generic.json) -- doing that
would be circular, since those logs are what the rules were written to
match. A real number requires a labeled set the rules were never tuned on.

Ground truth format (one labels.json per eval set directory):
    {
      "log_filename.jsonl": {
        "2": true,     // step 2 SHOULD be flagged (a real silent failure)
        "3": true,
        "7": false     // step 7 should NOT be flagged (detector's call, if
                        // any, would be a false positive)
      },
      "another_log.json": { ... }
    }
Any step not listed is assumed to be a true negative (should not be flagged).

Usage:
    python evaluate.py --logs-dir examples/eval_set --labels examples/eval_set/labels.json
"""

import argparse
import json
import sys
from pathlib import Path

from silent_failure_auditor.adapters import normalize
from silent_failure_auditor.detectors import run_detectors


def evaluate(logs_dir, labels_path):
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))

    total_tp = total_fp = total_fn = total_tn = 0
    per_file_rows = []
    per_bucket = {}  # bucket name -> [tp, fp, fn, tn]

    for filename, truth in labels.items():
        log_path = Path(logs_dir) / filename
        if not log_path.exists():
            print(f"Warning: {filename} listed in labels but not found in {logs_dir}", file=sys.stderr)
            continue

        bucket = filename.split("/")[0] if "/" in filename else "(root)"

        raw_text = log_path.read_text(encoding="utf-8")
        steps, fmt, error = normalize(raw_text)
        if error:
            print(f"Warning: could not parse {filename}: {error}", file=sys.stderr)
            continue
        run_detectors(steps)

        predicted_flagged = {s["step"] for s in steps if s["status"] == "flag"}
        truth_flagged = {int(k) for k, v in truth.items() if v}
        all_steps = {s["step"] for s in steps}

        tp = len(predicted_flagged & truth_flagged)
        fp = len(predicted_flagged - truth_flagged)
        fn = len(truth_flagged - predicted_flagged)
        tn = len(all_steps - predicted_flagged - truth_flagged)

        total_tp += tp
        total_fp += fp
        total_fn += fn
        total_tn += tn
        per_file_rows.append((filename, tp, fp, fn, tn))

        b = per_bucket.setdefault(bucket, [0, 0, 0, 0])
        b[0] += tp
        b[1] += fp
        b[2] += fn
        b[3] += tn

    def prf(tp, fp, fn):
        p = tp / (tp + fp) if (tp + fp) else float("nan")
        r = tp / (tp + fn) if (tp + fn) else float("nan")
        f1 = 2 * p * r / (p + r) if (p == p and r == r and (p + r)) else float("nan")
        return p, r, f1

    precision, recall, f1 = prf(total_tp, total_fp, total_fn)
    bucket_stats = {b: (*counts, *prf(counts[0], counts[1], counts[2])) for b, counts in per_bucket.items()}

    return {
        "per_file": per_file_rows,
        "per_bucket": bucket_stats,
        "true_positives": total_tp, "false_positives": total_fp,
        "false_negatives": total_fn, "true_negatives": total_tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate the detector against hand-labeled ground truth")
    parser.add_argument("--logs-dir", required=True, help="Directory containing the log files")
    parser.add_argument("--labels", required=True, help="Path to labels.json (ground truth)")
    parser.add_argument("--per-file", action="store_true", help="Also print the per-file breakdown")
    args = parser.parse_args()

    result = evaluate(args.logs_dir, args.labels)

    print(f"\nEvaluation -- {len(result['per_file'])} log(s)")
    print("-" * 66)
    print(f"{'bucket':<14} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}   {'P':>5} {'R':>5} {'F1':>5}")
    for bucket, (tp, fp, fn, tn, p, r, f1) in sorted(result["per_bucket"].items()):
        print(f"{bucket:<14} {tp:>4} {fp:>4} {fn:>4} {tn:>4}   {p:>5.2f} {r:>5.2f} {f1:>5.2f}")
    print("-" * 66)
    print(f"{'OVERALL':<14} {result['true_positives']:>4} {result['false_positives']:>4} "
          f"{result['false_negatives']:>4} {result['true_negatives']:>4}   "
          f"{result['precision']:>5.2f} {result['recall']:>5.2f} {result['f1']:>5.2f}")

    if args.per_file:
        print(f"\n{'file':<40} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}")
        for filename, tp, fp, fn, tn in result["per_file"]:
            print(f"{filename:<40} {tp:>4} {fp:>4} {fn:>4} {tn:>4}")


if __name__ == "__main__":
    main()
