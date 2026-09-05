from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.benchmark import (
    ConvergenceIssueExpectation,
    ConvergenceRunInput,
    aggregate_convergence_pairs,
    analyze_convergence,
    compare_convergence_pair,
)
from app.models.review import ReviewDecision


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return payload


def run_metrics(report: dict[str, Any]):
    expectations = tuple(
        ConvergenceIssueExpectation.model_validate(item)
        for item in report.get("expectations", [])
    )
    reviews = tuple(
        ReviewDecision.model_validate(item) for item in report.get("reviews", [])
    )
    reviewer_audit = report.get("reviewer_token_audit") or {}
    reviewer_tokens = int(reviewer_audit.get("total_tokens") or 0)
    outcome = report.get("outcome") or {}
    task_succeeded = outcome.get("status") == "SUCCEEDED"
    return analyze_convergence(
        ConvergenceRunInput(
            case_id=str(report["case_id"]),
            variant=str(report["variant"]),
            task_succeeded=task_succeeded,
            reviews=reviews,
            repair_attempts=len(report.get("repairs", [])),
            reviewer_tokens=reviewer_tokens,
            repair_tokens=0,
            total_tokens=reviewer_tokens,
            expectations=expectations,
        )
    )


def aggregate(input_root: Path) -> tuple[dict[str, Any], bool]:
    reports = [load_report(path) for path in sorted(input_root.rglob("p155-*.json"))]
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    harness_errors: list[dict[str, Any]] = []

    for report in reports:
        case_id = str(report.get("case_id") or "")
        variant = str(report.get("variant") or "")
        if not case_id or variant not in {"A", "B"}:
            continue
        if report.get("status") != "COMPLETE":
            harness_errors.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "status": report.get("status"),
                    "error": report.get("error"),
                }
            )
            continue
        grouped.setdefault(case_id, {})[variant] = report

    planned_case_ids = sorted(grouped)
    pairs = []
    case_results = []
    integrity_errors: list[str] = []

    for case_id in planned_case_ids:
        variants = grouped[case_id]
        if set(variants) != {"A", "B"}:
            integrity_errors.append(f"{case_id}: expected A and B reports")
            continue
        baseline_report = variants["A"]
        closure_report = variants["B"]
        if baseline_report.get("source_sha") != closure_report.get("source_sha"):
            integrity_errors.append(f"{case_id}: source SHA mismatch")
            continue
        if baseline_report.get("fingerprints") != closure_report.get("fingerprints"):
            integrity_errors.append(f"{case_id}: candidate/repair fingerprint mismatch")
            continue
        if baseline_report.get("expectations") != closure_report.get("expectations"):
            integrity_errors.append(f"{case_id}: pre-registered expectation mismatch")
            continue
        if baseline_report.get("closure_enabled") is not False:
            integrity_errors.append(f"{case_id}: A must have closure disabled")
            continue
        if closure_report.get("closure_enabled") is not True:
            integrity_errors.append(f"{case_id}: B must have closure enabled")
            continue

        baseline = run_metrics(baseline_report)
        closure = run_metrics(closure_report)
        delta = compare_convergence_pair(baseline, closure)
        pairs.append((baseline, closure))
        case_results.append(
            {
                "case_id": case_id,
                "baseline": baseline.model_dump(mode="json"),
                "closure": closure.model_dump(mode="json"),
                "delta": delta.model_dump(mode="json"),
                "runtime_closure_rounds_A": baseline_report.get("closure_rounds", []),
                "runtime_closure_rounds_B": closure_report.get("closure_rounds", []),
            }
        )

    matrix_complete = not harness_errors and not integrity_errors and bool(pairs)
    aggregate_metrics = aggregate_convergence_pairs(tuple(pairs)) if pairs else None
    summary = {
        "experiment": "P1.5.5 Tier1 Controlled Closure Isolation Matrix",
        "design": {
            "same_frozen_runtime_for_A_and_B": True,
            "A": "real qwen reviewer with runtime closure context deliberately not forwarded",
            "B": "real qwen reviewer with runtime closure context forwarded normally",
            "developer": "deterministic scripted candidate",
            "repair": "deterministic scripted repair",
            "real_model_token_scope": "reviewer only",
            "evaluable_gate": (
                "both A and B must observe a pre-registered primary blocker in round 1, "
                "and neither may report a pre-registered churn trap in round 1"
            ),
            "confirmed_churn": "only a later issue matching a pre-registered CHURN_TRAP",
        },
        "matrix_complete": matrix_complete,
        "report_count": len(reports),
        "paired_case_count": len(pairs),
        "harness_errors": harness_errors,
        "integrity_errors": integrity_errors,
        "cases": case_results,
        "aggregate": (
            aggregate_metrics.model_dump(mode="json") if aggregate_metrics is not None else None
        ),
    }
    return summary, matrix_complete


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary, complete = aggregate(args.input_root)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
