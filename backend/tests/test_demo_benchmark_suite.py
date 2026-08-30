from __future__ import annotations

from pathlib import Path

from app.benchmark.io import load_suite

FIXTURE_BASE = "d141eba7df1d2d5016de2589152d5ab2518778ab"


def test_versioned_demo_suite_is_valid_and_pinned_to_fixture_branch() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    suite, suite_sha = load_suite(repository_root / "benchmarks/v1/demo-suite.json")

    assert suite.schema_version == 1
    assert suite.suite_id == "devflow-v1-demo"
    assert suite.suite_version == "1.0.0"
    assert len(suite.cases) == 3
    assert len(suite_sha) == 64
    assert {case.case_id for case in suite.cases} == {
        "text-marker",
        "typed-add",
        "json-contract",
    }
    for case in suite.cases:
        assert str(case.repository_url).rstrip("/") == ("https://github.com/xinxin1101/Muti_Agent")
        assert case.default_branch == "benchmark-fixtures/v1-base"
        assert case.expected_base_commit == FIXTURE_BASE
        assert case.task.max_retries == 2
        assert case.expectations.max_human_decisions == 0


def test_demo_suite_contains_no_runtime_or_provider_credentials() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    text = (repository_root / "benchmarks/v1/demo-suite.json").read_text(encoding="utf-8").lower()

    for forbidden in (
        "run_token",
        "github_token",
        "siliconflow_api_key",
        "authorization",
        "password",
        "secret",
    ):
        assert forbidden not in text
