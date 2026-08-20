from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from app.benchmark.client import BenchmarkApiClient
from app.benchmark.evaluator import evaluate_suite
from app.benchmark.io import (
    load_observations,
    load_suite,
    write_model,
    write_report,
)
from app.benchmark.models import (
    BenchmarkCaseVerdict,
    BenchmarkExecutionConfig,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devflow-benchmark",
        description=(
            "Run or evaluate versioned DevFlow benchmark fixtures through accepted Product API "
            "boundaries. Benchmark results never mutate Run truth."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate and hash one benchmark suite.")
    validate.add_argument("--suite", required=True, type=Path)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Deterministically evaluate an existing observation bundle.",
    )
    evaluate.add_argument("--suite", required=True, type=Path)
    evaluate.add_argument("--observations", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)

    run = subparsers.add_parser(
        "run",
        help=(
            "Execute benchmark cases through the existing DevFlow Product API, "
            "then evaluate them."
        ),
    )
    run.add_argument("--suite", required=True, type=Path)
    run.add_argument(
        "--api-base-url",
        default=os.getenv("DEVFLOW_BENCHMARK_API_BASE_URL", "http://127.0.0.1:8000"),
    )
    run.add_argument("--poll-interval-seconds", type=float, default=1.0)
    run.add_argument("--timeout-per-case-seconds", type=float, default=900.0)
    run.add_argument("--output", required=True, type=Path)
    return parser


def _report_exit_code(report) -> int:
    return (
        0
        if all(item.verdict is BenchmarkCaseVerdict.MATCHED for item in report.cases)
        else 1
    )


async def _run_live(args: argparse.Namespace) -> int:
    suite, suite_sha256 = load_suite(args.suite)
    execution = BenchmarkExecutionConfig(
        api_base_url=args.api_base_url,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout_per_case_seconds=args.timeout_per_case_seconds,
    )
    async with BenchmarkApiClient(execution) as client:
        observations = await client.run_suite(suite, suite_sha256)
    args.output.mkdir(parents=True, exist_ok=True)
    write_model(args.output / "observations.json", observations)
    report = evaluate_suite(suite, suite_sha256, observations)
    write_report(args.output, report)
    print(
        json.dumps(
            {
                "suite_id": suite.suite_id,
                "suite_version": suite.suite_version,
                "suite_sha256": suite_sha256,
                "report_sha256": report.report_sha256,
                "matched_cases": report.summary.matched_cases,
                "mismatched_cases": report.summary.mismatched_cases,
                "not_evaluated_cases": report.summary.not_evaluated_cases,
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return _report_exit_code(report)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            suite, suite_sha256 = load_suite(args.suite)
            print(
                json.dumps(
                    {
                        "suite_id": suite.suite_id,
                        "suite_version": suite.suite_version,
                        "suite_sha256": suite_sha256,
                        "case_count": len(suite.cases),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "evaluate":
            suite, suite_sha256 = load_suite(args.suite)
            observations = load_observations(args.observations)
            report = evaluate_suite(suite, suite_sha256, observations)
            write_report(args.output, report)
            print(
                json.dumps(
                    {
                        "report_sha256": report.report_sha256,
                        "matched_cases": report.summary.matched_cases,
                        "mismatched_cases": report.summary.mismatched_cases,
                        "not_evaluated_cases": report.summary.not_evaluated_cases,
                        "output": str(args.output),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return _report_exit_code(report)

        if args.command == "run":
            return asyncio.run(_run_live(args))

        parser.error(f"unsupported command: {args.command}")
    except (
        OSError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {"error": str(exc)[:1000]},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
