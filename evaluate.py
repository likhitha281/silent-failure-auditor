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

from run_baseline import normalize, run_detectors


def evaluate(logs_dir, labels_path):
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))

    total_tp = total_fp = total_fn = total_tn = 0
    per_file_rows = []

    for filename, truth in labels.items():
        log_path = Path(logs_dir) / filename
        if not log_path.exists():
            print(f"Warning: {filename} listed in labels but not found in {logs_dir}", file=sys.stderr)
            continue

        raw_text = log_path.read_text(encoding="utf-8")
        steps, error = normalize(raw_text)
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

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else float("nan")
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) and precision == precision and recall == recall else float("nan"))

    return {
        "per_file": per_file_rows,
        "true_positives": total_tp, "false_positives": total_fp,
        "false_negatives": total_fn, "true_negatives": total_tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate the detector against hand-labeled ground truth")
    parser.add_argument("--logs-dir", required=True, help="Directory containing the log files")
    parser.add_argument("--labels", required=True, help="Path to labels.json (ground truth)")
    args = parser.parse_args()

    result = evaluate(args.logs_dir, args.labels)

    print(f"\nEvaluation -- {len(result['per_file'])} log(s)")
    print("-" * 60)
    print(f"{'file':<30} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}")
    for filename, tp, fp, fn, tn in result["per_file"]:
        print(f"{filename:<30} {tp:>4} {fp:>4} {fn:>4} {tn:>4}")
    print("-" * 60)
    print(f"Precision: {result['precision']:.2f}")
    print(f"Recall:    {result['recall']:.2f}")
    print(f"F1:        {result['f1']:.2f}")
    print(f"(TP={result['true_positives']}, FP={result['false_positives']}, "
          f"FN={result['false_negatives']}, TN={result['true_negatives']})")


if __name__ == "__main__":
    main()
