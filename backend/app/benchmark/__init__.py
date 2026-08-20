"""Versioned, read-only benchmark and demo evaluation boundary."""

from app.benchmark.client import BenchmarkApiClient, BenchmarkApiError
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
    "canonical_sha256",
    "evaluate_suite",
    "load_observations",
    "load_suite",
]
