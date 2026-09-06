from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.benchmark import (
    SemanticPressureExpectation,
    SemanticPressureRepairDelta,
    SemanticPressureRunInput,
    aggregate_semantic_pressure,
    analyze_semantic_pressure,
)
from app.models.review import ReviewDecision

EXPECTED_CASES = (
    "py-exception-swallow",
    "api-response-contract",
    "db-transaction-rollback",
    "concurrency-lost-update",
    "security-path-scope",
    "multifile-symbol-rename",
    "deploy-env-default",
    "frontend-stale-submit",
    "cross-file-schema-drift",
    "repair-induced-timeout-regression",
)


def load_reports(root: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("p156-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        case_id = payload.get("case_id")
        if not isinstance(case_id, str):
            continue
        if case_id in reports:
            raise ValueError(f"duplicate report for {case_id}")
        reports[case_id] = payload
    return reports


def build_input(report: dict[str, Any]) -> SemanticPressureRunInput | None:
    if report.get("status") != "COMPLETE":
        return None
    raw_reviews = report.get("reviews")
    raw_deltas = report.get("repair_deltas")
    raw_expectations = report.get("expectations")
    if not isinstance(raw_reviews, list) or not raw_reviews:
        return None
    if not isinstance(raw_deltas, list) or not isinstance(raw_expectations, list):
        return None
    if len(raw_deltas) != len(raw_reviews) - 1:
        return None
    return SemanticPressureRunInput(
        case_id=str(report["case_id"]),
        workload=str(report["workload"]),
        model=str(report["model"]),
        task_succeeded=bool(report.get("task_succeeded")),
        reviews=tuple(ReviewDecision.model_validate(item) for item in raw_reviews),
        repair_deltas=tuple(
            SemanticPressureRepairDelta.model_validate(item) for item in raw_deltas
        ),
        expectations=tuple(
            SemanticPressureExpectation.model_validate(item)
            for item in raw_expectations
        ),
        reviewer_tokens=int(report.get("reviewer_tokens") or 0),
    )


def external_reason(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "UNKNOWN")
    if status == "INITIAL_VERIFICATION_INTERCEPTED":
        return "candidate was intercepted by deterministic verification before Review round 1"
    if status == "HARNESS_ERROR":
        error = report.get("error") or {}
        return f"harness error: {error.get('type')}: {error.get('message')}"
    reviews = report.get("reviews") or []
    deltas = report.get("repair_deltas") or []
    if reviews and len(deltas) != len(reviews) - 1:
        verification = report.get("verification") or []
        if verification and not verification[-1].get("passed", True):
            return "repair was intercepted by deterministic verification before the next Review"
        return "review/repair transition evidence is incomplete"
    return f"report status {status} is not analyzer-evaluable"


def aggregate(root: Path) -> dict[str, Any]:
    reports = load_reports(root)
    missing = [case_id for case_id in EXPECTED_CASES if case_id not in reports]
    unexpected = sorted(set(reports) - set(EXPECTED_CASES))
    if missing or unexpected:
        raise ValueError(f"case registry drift: missing={missing}, unexpected={unexpected}")

    inputs: list[SemanticPressureRunInput] = []
    cases: list[dict[str, Any]] = []
    external_inconclusive = 0
    harness_errors: list[str] = []

    for case_id in EXPECTED_CASES:
        report = reports[case_id]
        run_input = build_input(report)
        if run_input is None:
            external_inconclusive += 1
            reason = external_reason(report)
            if report.get("status") == "HARNESS_ERROR":
                harness_errors.append(case_id)
            cases.append(
                {
                    "case_id": case_id,
                    "workload": report.get("workload"),
                    "repair_scope": report.get("repair_scope"),
                    "status": report.get("status"),
                    "analyzer_evaluable": False,
                    "closure_ab_eligible": False,
                    "verdict": "EXTERNAL_INCONCLUSIVE",
                    "reason": reason,
                    "review_rounds": len(report.get("reviews") or []),
                    "reviewer_tokens": int(report.get("reviewer_tokens") or 0),
                }
            )
            continue

        inputs.append(run_input)
        metrics = analyze_semantic_pressure(run_input)
        cases.append(
            {
                "case_id": case_id,
                "workload": metrics.workload.value,
                "repair_scope": report.get("repair_scope"),
                "status": report.get("status"),
                "analyzer_evaluable": metrics.evaluable,
                "closure_ab_eligible": metrics.closure_ab_eligible,
                "verdict": metrics.verdict.value,
                "review_rounds": metrics.review_rounds,
                "repair_rounds": metrics.repair_rounds,
                "confirmed_churn_events": metrics.confirmed_churn_events,
                "recurring_primary_events": metrics.recurring_primary_events,
                "legitimate_new_blocker_events": metrics.legitimate_new_blocker_events,
                "repair_induced_issue_events": metrics.repair_induced_issue_events,
                "unregistered_new_issue_events": metrics.unregistered_new_issue_events,
                "reviewer_tokens": metrics.reviewer_tokens,
                "issue_events": [
                    event.model_dump(mode="json") for event in metrics.issue_events
                ],
            }
        )

    analyzer_aggregate = (
        aggregate_semantic_pressure(tuple(inputs)).model_dump(mode="json")
        if inputs
        else None
    )
    eligible = [
        item["case_id"] for item in cases if item["closure_ab_eligible"] is True
    ]
    verdict_counts = Counter(str(item["verdict"]) for item in cases)

    return {
        "experiment": "P1.5.6 Semantic-Pressure Fresh Reviewer Discovery",
        "status": "COMPLETE" if not harness_errors else "COMPLETE_WITH_HARNESS_ERRORS",
        "expected_case_count": len(EXPECTED_CASES),
        "report_count": len(reports),
        "analyzer_input_count": len(inputs),
        "external_inconclusive_count": external_inconclusive,
        "harness_errors": harness_errors,
        "closure_ab_eligible_case_ids": eligible,
        "confirmed_baseline_churn_case_count": len(eligible),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "aggregate": analyzer_aggregate,
        "cases": cases,
        "interpretation": (
            "Only BASELINE_CHURN_CONFIRMED cases are eligible for Closure OFF/ON. "
            "No case may be substituted after observing results."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = aggregate(args.input_root)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["harness_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
