from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.benchmark.models import BenchmarkObservationBundle, BenchmarkReport, BenchmarkSuite

MAX_SUITE_BYTES = 512 * 1024
MAX_OBSERVATION_BYTES = 2 * 1024 * 1024


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _read_json(path: Path, *, max_bytes: int) -> object:
    if path.is_symlink():
        raise ValueError(f"benchmark input must not be a symlink: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"benchmark input exceeds {max_bytes} bytes: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_suite(path: Path) -> tuple[BenchmarkSuite, str]:
    payload = _read_json(path, max_bytes=MAX_SUITE_BYTES)
    suite = BenchmarkSuite.model_validate(payload)
    return suite, canonical_sha256(suite)


def load_observations(path: Path) -> BenchmarkObservationBundle:
    payload = _read_json(path, max_bytes=MAX_OBSERVATION_BYTES)
    return BenchmarkObservationBundle.model_validate(payload)


def write_model(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        f"# DevFlow Benchmark Report — {report.suite_id} {report.suite_version}",
        "",
        f"- Suite SHA-256: `{report.suite_sha256}`",
        f"- Report SHA-256: `{report.report_sha256}`",
        f"- API origin: `{report.execution.api_base_url}`",
        f"- Cases: {report.summary.total_cases}",
        f"- Matched: {report.summary.matched_cases}",
        f"- Mismatched: {report.summary.mismatched_cases}",
        f"- Not evaluated: {report.summary.not_evaluated_cases}",
        f"- Cost/token data: **{report.summary.cost_data.value}**",
        "",
        "> Benchmark verdicts are read-only comparisons. They never change DevFlow Run truth.",
        "",
        "| Case | Verdict | Runtime | Completion | Evidence | Code delta | Reliability | Latency |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.cases:
        lines.append(
            "| "
            + " | ".join(
                (
                    item.case_id,
                    item.verdict.value,
                    item.runtime_status.value if item.runtime_status is not None else "—",
                    item.completion.status.value,
                    item.evidence.status.value,
                    item.code_delta.status.value,
                    item.reliability.status.value,
                    item.latency.status.value,
                )
            )
            + " |"
        )
        if item.failure_modes:
            lines.append("")
            lines.append(
                f"- `{item.case_id}` failure/mismatch modes: "
                + ", ".join(f"`{mode}`" for mode in item.failure_modes)
            )
    lines.extend(
        (
            "",
            "## Dimension counts",
            "",
            f"- Completion matches: {report.summary.completion_matches}",
            f"- Evidence matches: {report.summary.evidence_matches}",
            f"- Code-delta matches: {report.summary.code_delta_matches}",
            f"- Reliability matches: {report.summary.reliability_matches}",
            f"- Latency matches: {report.summary.latency_matches}",
            "",
            "No aggregate success score is produced.",
            "",
        )
    )
    return "\n".join(lines)


def write_report(output_dir: Path, report: BenchmarkReport) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    write_model(json_path, report)
    temporary = markdown_path.with_name(f".{markdown_path.name}.{os.getpid()}.tmp")
    temporary.write_text(render_markdown(report), encoding="utf-8")
    temporary.replace(markdown_path)
    return json_path, markdown_path


__all__ = [
    "MAX_OBSERVATION_BYTES",
    "MAX_SUITE_BYTES",
    "ValidationError",
    "canonical_json_bytes",
    "canonical_sha256",
    "load_observations",
    "load_suite",
    "render_markdown",
    "write_model",
    "write_report",
]
