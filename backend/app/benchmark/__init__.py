"""Versioned, read-only benchmark and demo evaluation boundary."""

from app.benchmark.client import BenchmarkApiClient, BenchmarkApiError
from app.benchmark.convergence import (
    ConvergenceAggregate,
    ConvergenceExpectationKind,
    ConvergenceIssueClass,
    ConvergenceIssueEvent,
    ConvergenceIssueExpectation,
    ConvergencePairDelta,
    ConvergencePairVerdict,
    ConvergenceRunInput,
    ConvergenceRunMetrics,
    aggregate_convergence_pairs,
    analyze_convergence,
    compare_convergence_pair,
)
from app.benchmark.evaluator import evaluate_suite
from app.benchmark.io import canonical_sha256, load_observations, load_suite
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkCaseEvaluation,
    BenchmarkCaseVerdict,
    BenchmarkExecutionConfig,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkReport,
    BenchmarkSuite,
)

__all__ = [
    "BenchmarkApiClient",
    "BenchmarkApiError",
    "BenchmarkCase",
    "BenchmarkCaseEvaluation",
    "BenchmarkCaseVerdict",
    "BenchmarkExecutionConfig",
    "BenchmarkObservation",
    "BenchmarkObservationBundle",
    "BenchmarkObservationState",
    "BenchmarkReport",
    "BenchmarkSuite",
    "ConvergenceAggregate",
    "ConvergenceExpectationKind",
    "ConvergenceIssueClass",
    "ConvergenceIssueEvent",
    "ConvergenceIssueExpectation",
    "ConvergencePairDelta",
    "ConvergencePairVerdict",
    "ConvergenceRunInput",
    "ConvergenceRunMetrics",
    "aggregate_convergence_pairs",
    "analyze_convergence",
    "canonical_sha256",
    "compare_convergence_pair",
    "evaluate_suite",
    "load_observations",
    "load_suite",
]
