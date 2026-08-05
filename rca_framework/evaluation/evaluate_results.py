"""
evaluate_results.py — computes every statistic the supervisor asked for.

Joins TicketBuddy_GroundTruth (what fault was actually injected, when)
with TicketBuddy_RCAResults (what the LLM diagnosed, when) and produces,
per fault category and overall:

  Accuracy, Precision, Recall, F1
  Detection latency: mean, variance, standard deviation
  Confidence statistics
  False-positive count from control (no-fault) windows

Matching rule: an RCA result belongs to the trial whose
[injected_at, cleared_at] window contains the result's alarm_time
(with a grace period after clearing for in-flight analyses).

Outputs:
  results_per_trial.csv   — one row per trial (raw data for the thesis appendix)
  results_summary.csv     — per-category metrics table (thesis results chapter)
  and a printed summary.

Run: python evaluate_results.py
"""

import csv
import math
import statistics
from datetime import datetime, timedelta, timezone

import boto3

dynamo = boto3.resource("dynamodb")
TRUTH = dynamo.Table("TicketBuddy_GroundTruth")
RESULTS = dynamo.Table("TicketBuddy_RCAResults")

# injected fault_type -> expected LLM category
TYPE_TO_CATEGORY = {
    "api_failure": "API_REQUEST_FAILURE",
    "timeout": "LAMBDA_TIMEOUT",
    "cpu_overload": "HIGH_RESOURCE_USAGE",
    "exception": "LAMBDA_EXCEPTION",
    "dynamodb_failure": "DYNAMODB_FAILURE",
    "none": "NO_FAULT",
}

GRACE_MINUTES = 2  # analyses may complete shortly after a fault is cleared


def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def scan_all(table):
    items, resp = [], table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def match_results_to_trials(trials, results):
    """Attach each RCA result to the trial window containing its alarm time."""
    for t in trials:
        t["_start"] = parse_ts(t.get("injected_at"))
        end = parse_ts(t.get("cleared_at"))
        t["_end"] = (end + timedelta(minutes=GRACE_MINUTES)) if end else (t["_start"] + timedelta(minutes=5) if t["_start"] else None)
        t["_results"] = []

    unmatched = []
    for r in results:
        rt = parse_ts(r.get("alarm_time"))
        placed = False
        for t in trials:
            if t["_start"] and rt and rt >= t["_start"] and \
                    (t["_end"] is None or rt <= t["_end"]):
                t["_results"].append(r)
                placed = True
                break
        if not placed:
            unmatched.append(r)
    return unmatched


def evaluate():
    trials = scan_all(TRUTH)
    results = scan_all(RESULTS)
    unmatched = match_results_to_trials(trials, results)

    rows = []
    for t in trials:
        expected = TYPE_TO_CATEGORY.get(t.get("fault_type", "none"),
                                        "NO_FAULT")
        rs = sorted(t["_results"], key=lambda r: r.get("alarm_time", ""))
        first = rs[0] if rs else None

        detected = first is not None
        predicted = first["root_cause_category"] if first else "NO_DETECTION"
        correct = detected and predicted == expected

        latency = None
        if detected and t["_start"]:
            at = parse_ts(first["alarm_time"])
            latency = (at - t["_start"]).total_seconds()

        rows.append({
            "trial_id": t["trial_id"],
            "target": t.get("target", ""),
            "fault_type": t.get("fault_type", ""),
            "expected_category": expected,
            "detected": detected,
            "predicted_category": predicted,
            "correct": correct,
            "detection_latency_s": latency,
            "analysis_seconds": float(first["analysis_seconds"])
            if first and first.get("analysis_seconds") else None,
            "confidence": float(first["confidence"])
            if first and first.get("confidence") else None,
            "root_cause": first.get("root_cause", "") if first else "",
            "remediation": first.get("remediation", "") if first else "",
        })

    # ── Per-category metrics ────────────────────────────────────────────
    fault_rows = [r for r in rows if r["fault_type"] != "none"]
    control_rows = [r for r in rows if r["fault_type"] == "none"]

    categories = sorted({r["expected_category"] for r in fault_rows})
    summary = []
    for cat in categories:
        tp = sum(1 for r in rows
                 if r["predicted_category"] == cat
                 and r["expected_category"] == cat)
        fp = sum(1 for r in rows
                 if r["predicted_category"] == cat
                 and r["expected_category"] != cat)
        fn = sum(1 for r in rows
                 if r["expected_category"] == cat
                 and r["predicted_category"] != cat)

        n = tp + fn
        accuracy = tp / n if n else 0.0
        misdiagnosed = sum(1 for r in rows
                           if r["expected_category"] == cat
                           and r["detected"]
                           and r["predicted_category"] != cat)
        detected_count = tp + misdiagnosed
        diag_accuracy = tp / detected_count if detected_count else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)

        lats = [r["detection_latency_s"] for r in rows
                if r["expected_category"] == cat
                and r["detection_latency_s"] is not None]
        lat_mean = statistics.mean(lats) if lats else float("nan")
        lat_var = statistics.pvariance(lats) if len(lats) > 1 else 0.0
        lat_std = math.sqrt(lat_var)

        confs = [r["confidence"] for r in rows
                 if r["expected_category"] == cat
                 and r["confidence"] is not None]

        summary.append({
            "category": cat, "trials": n,
            "TP": tp, "FP": fp, "FN": fn,
            "detected": detected_count,
            "accuracy": round(accuracy, 4),
            "diag_accuracy": round(diag_accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "latency_mean_s": round(lat_mean, 2) if lats else "",
            "latency_variance": round(lat_var, 2) if lats else "",
            "latency_std_s": round(lat_std, 2) if lats else "",
            "confidence_mean": round(statistics.mean(confs), 3)
            if confs else "",
        })

    # ── Overall ─────────────────────────────────────────────────────────
    total = len(fault_rows)
    correct_total = sum(1 for r in fault_rows if r["correct"])
    all_lats = [r["detection_latency_s"] for r in fault_rows
                if r["detection_latency_s"] is not None]
    control_fps = sum(len(t["_results"]) for t in trials
                      if t.get("fault_type") == "none")

    # ── Write CSVs ──────────────────────────────────────────────────────
    with open("results_per_trial.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)

    with open("results_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=list(summary[0].keys()) if summary else [])
        w.writeheader()
        w.writerows(summary)

    # ── Print ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}\nRCA FRAMEWORK EVALUATION\n{'=' * 70}")
    print(f"Fault trials: {total}   Control trials: {len(control_rows)}   "
          f"Unmatched RCA results: {len(unmatched)}")
    if total:
        print(f"\nOVERALL ACCURACY: {correct_total}/{total} "
              f"= {correct_total / total:.1%}")
    if all_lats:
        print(f"DETECTION LATENCY: mean={statistics.mean(all_lats):.1f}s  "
              f"var={statistics.pvariance(all_lats):.1f}  "
              f"std={math.sqrt(statistics.pvariance(all_lats)):.1f}s")
    print(f"FALSE POSITIVES in control windows: {control_fps}")
    print(f"\nPer-category summary written to results_summary.csv")
    for s in summary:
        if s['category'] == 'NO_FAULT':
            continue
        print(f"  {s['category']:<22} n={s['trials']:<3} "
              f"acc={s['accuracy']:<7} P={s['precision']:<7} "
              f"R={s['recall']:<7} F1={s['f1']:<7} "
              f"lat={s['latency_mean_s']}s")
    print(f"\nRaw per-trial data written to results_per_trial.csv "
          f"(thesis appendix material)")


if __name__ == "__main__":
    evaluate()
