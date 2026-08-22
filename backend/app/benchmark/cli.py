from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from app.benchmark.chaos import load_chaos_manifest, run_chaos_recovery_benchmark
from app.benchmark.client import BenchmarkApiClient
from app.benchmark.demo import load_demo_manifest, run_control_plane_demo
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
    BenchmarkExperimentIdentityBasis,
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

    demo = subparsers.add_parser(
        "demo",
        help="Run the versioned deterministic control-plane demo manifest through pytest.",
    )
    demo.add_argument("--manifest", required=True, type=Path)
    demo.add_argument("--repository-root", type=Path, default=Path("."))
    demo.add_argument("--timeout-seconds", type=float, default=300.0)

    chaos = subparsers.add_parser(
        "chaos",
        help=(
            "Run the versioned Step 5.8 deterministic crash/race recovery matrix through "
            "production authority components."
        ),
    )
    chaos.add_argument("--manifest", required=True, type=Path)
    chaos.add_argument("--repository-root", type=Path, default=Path("."))
    chaos.add_argument("--timeout-seconds", type=float, default=600.0)

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
            "Execute stochastic live-model benchmark cases through the existing DevFlow "
            "Product API, then evaluate them."
        ),
    )
    run.add_argument("--suite", required=True, type=Path)
    run.add_argument(
        "--api-base-url",
        default=os.getenv("DEVFLOW_BENCHMARK_API_BASE_URL", "http://127.0.0.1:8000"),
    )
    run.add_argument("--poll-interval-seconds", type=float, default=1.0)
    run.add_argument("--timeout-per-case-seconds", type=float, default=900.0)
    run.add_argument("--runtime-commit", required=True)
    run.add_argument("--provider", required=True)
    run.add_argument("--planner-model")
    run.add_argument("--developer-model", required=True)
    run.add_argument("--reviewer-model", required=True)
    run.add_argument("--repair-model", required=True)
    run.add_argument("--context-strategy", required=True)
    run.add_argument("--verifier-identity", required=True)
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
        identity_basis=BenchmarkExperimentIdentityBasis.OPERATOR_DECLARED,
        runtime_commit=args.runtime_commit,
        provider=args.provider,
        planner_model=args.planner_model,
        developer_model=args.developer_model,
        reviewer_model=args.reviewer_model,
        repair_model=args.repair_model,
        context_strategy=args.context_strategy,
        verifier_identity=args.verifier_identity,
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
                "task_success_rate": report.summary.aggregates.task_success_rate,
                "first_pass_success_rate": (
                    report.summary.aggregates.first_pass_success_rate
                ),
                "repaired_success_rate": report.summary.aggregates.repaired_success_rate,
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

        if args.command == "demo":
            manifest, manifest_sha256 = load_demo_manifest(args.manifest)
            result = run_control_plane_demo(
                args.manifest,
                repository_root=args.repository_root,
                timeout_seconds=args.timeout_seconds,
            )
            print(
                json.dumps(
                    {
                        "manifest_id": manifest.manifest_id,
                        "manifest_version": manifest.manifest_version,
                        "manifest_sha256": manifest_sha256,
                        "runtime_commit": result.runtime_commit,
                        "scenario_count": result.scenario_count,
                        "passed": result.passed,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if result.passed else 1

        if args.command == "chaos":
            manifest, manifest_sha256 = load_chaos_manifest(args.manifest)
            result = run_chaos_recovery_benchmark(
                args.manifest,
                repository_root=args.repository_root,
                timeout_seconds=args.timeout_seconds,
            )
            print(
                json.dumps(
                    {
                        "suite_id": manifest.suite_id,
                        "suite_version": manifest.suite_version,
                        "suite_sha256": manifest_sha256,
                        "runtime_commit": result.runtime_commit,
                        "scenario_count": result.scenario_count,
                        "invariant_count": result.invariant_count,
                        "passed": result.passed,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if result.passed else 1

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
                        "task_success_rate": report.summary.aggregates.task_success_rate,
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
        subprocess.TimeoutExpired,
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
