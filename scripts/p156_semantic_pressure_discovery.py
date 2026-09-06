from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents import ReviewerAgent
from app.core.settings import Settings
from app.models.review import ReviewDecision, ReviewOutcome
from app.models.task import TaskContract
from app.models.verification import (
    CheckResult,
    CheckType,
    VerificationBackend,
    VerificationResult,
)
from app.providers.siliconflow import SiliconFlowDriver
from app.workspace import LocalGitWorkspace

SOURCE_SHA = "2ff8d396876f3afd74adaa32fd5a1953687ead36"
MAX_REVIEW_ROUNDS = 3


@dataclass(frozen=True)
class Scenario:
    case_id: str
    workload: str
    repair_scope: str
    objective: str
    criteria: tuple[str, ...]
    baseline: dict[str, str]
    candidate: dict[str, str]
    repaired: dict[str, str]
    clean: dict[str, str]
    verification_command: str
    expectations: tuple[dict[str, Any], ...]
    primary_expectation_ids: tuple[str, ...]
    introduced_expectation_ids: tuple[str, ...] = ()

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted(self.candidate))

    def fingerprints(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for label, files in (
            ("candidate", self.candidate),
            ("repaired", self.repaired),
            ("clean", self.clean),
        ):
            joined = "\n".join(f"{path}\0{files[path]}" for path in sorted(files))
            payload[f"{label}_sha256"] = hashlib.sha256(joined.encode()).hexdigest()
        return payload


def expectation(
    expectation_id: str,
    kind: str,
    *,
    file: str,
    patterns: tuple[str, ...],
    origin: str = "PREEXISTING",
    introduced_by_repair_round: int | None = None,
    minimum_pattern_matches: int = 1,
) -> dict[str, Any]:
    return {
        "matcher": {
            "expectation_id": expectation_id,
            "kind": kind,
            "file": file,
            "message_patterns": list(patterns),
            "minimum_pattern_matches": minimum_pattern_matches,
        },
        "origin": origin,
        "introduced_by_repair_round": introduced_by_repair_round,
    }


def scenarios() -> dict[str, Scenario]:
    py_base = {"app/service.py": "class DomainError(Exception):\n    pass\n\n\ndef process(action):\n    return action()\n"}
    py_candidate = {"app/service.py": "class DomainError(Exception):\n    pass\n\n\ndef process(action):\n    try:\n        x = action()\n        return x\n    except DomainError as exc:\n        return {\"ok\": True, \"error\": str(exc)}\n"}
    py_repaired = {"app/service.py": "class DomainError(Exception):\n    pass\n\n\ndef process(action):\n    try:\n        x = action()\n        return x\n    except DomainError:\n        raise\n"}
    py_clean = {"app/service.py": "class DomainError(Exception):\n    pass\n\n\ndef process(action):\n    return action()\n"}

    api_base = {
        "app/api.py": "def build_response(data, request_id):\n    return data\n",
        "app/schemas.py": "REQUIRED_FIELDS = ('data', 'request_id')\n",
    }
    api_candidate = {
        "app/api.py": "def build_response(data, request_id):\n    payload = {'data': data}\n    payload = dict(payload)\n    return payload\n",
        "app/schemas.py": "REQUIRED_FIELDS = ('data', 'request_id')\n",
    }
    api_repaired = {
        "app/api.py": "def build_response(data, request_id):\n    payload = {'data': data, 'request_id': request_id}\n    payload = dict(payload)\n    return payload\n",
        "app/schemas.py": "REQUIRED_FIELDS = ('data', 'request_id')\n",
    }
    api_clean = {
        "app/api.py": "def build_response(data, request_id):\n    return {'data': data, 'request_id': request_id}\n",
        "app/schemas.py": "REQUIRED_FIELDS = ('data', 'request_id')\n",
    }

    db_base = {"app/repository.py": "def save_order(db, order, audit):\n    db.save(order)\n    db.save(audit)\n"}
    db_candidate = {"app/repository.py": "def save_order(db, order, audit):\n    db.save(order)\n    x = audit\n    db.save(x)\n"}
    db_repaired = {"app/repository.py": "def save_order(db, order, audit):\n    with db.transaction():\n        db.save(order)\n        x = audit\n        db.save(x)\n"}
    db_clean = {"app/repository.py": "def save_order(db, order, audit):\n    with db.transaction():\n        db.save(order)\n        db.save(audit)\n"}

    concurrency_base = {"app/counter.py": "class Counter:\n    def __init__(self):\n        self.value = 0\n"}
    concurrency_candidate = {"app/counter.py": "import threading\n\n\nclass Counter:\n    def __init__(self):\n        self.value = 0\n        self._lock = threading.Lock()\n\n    def increment(self):\n        tmp = self.value\n        self.value = tmp + 1\n        return self.value\n"}
    concurrency_repaired = {"app/counter.py": "import threading\n\n\nclass Counter:\n    def __init__(self):\n        self.value = 0\n        self._lock = threading.Lock()\n\n    def increment(self):\n        with self._lock:\n            tmp = self.value\n            self.value = tmp + 1\n            return self.value\n"}
    concurrency_clean = {"app/counter.py": "import threading\n\n\nclass Counter:\n    def __init__(self):\n        self.value = 0\n        self._lock = threading.Lock()\n\n    def increment(self):\n        with self._lock:\n            self.value += 1\n            return self.value\n"}

    security_base = {"app/files.py": "from pathlib import Path\n\nROOT = Path('/srv/data')\n"}
    security_candidate = {"app/files.py": "from pathlib import Path\n\nROOT = Path('/srv/data')\n\n\ndef resolve_user_path(raw):\n    if not raw.startswith(str(ROOT)):\n        raise ValueError('bad path')\n    return Path(raw).resolve()\n"}
    security_repaired = {"app/files.py": "from pathlib import Path\n\nROOT = Path('/srv/data')\n\n\ndef resolve_user_path(raw):\n    resolved = Path(raw).resolve()\n    try:\n        resolved.relative_to(ROOT.resolve())\n    except ValueError as exc:\n        raise ValueError('bad path') from exc\n    return resolved\n"}
    security_clean = {"app/files.py": "from pathlib import Path\n\nROOT = Path('/srv/data')\n\n\ndef resolve_user_path(raw):\n    resolved = Path(raw).resolve()\n    root = ROOT.resolve()\n    try:\n        resolved.relative_to(root)\n    except ValueError as exc:\n        raise ValueError('path outside allowed root') from exc\n    return resolved\n"}

    rename_base = {
        "app/domain.py": "def old_name(value):\n    return value * 2\n",
        "app/consumer.py": "from app.domain import old_name\n\n\ndef consume(value):\n    return old_name(value)\n",
        "app/__init__.py": "from app.domain import old_name\n",
    }
    rename_candidate = {
        "app/domain.py": "def new_name(value):\n    return value * 2\n\nold_name = new_name\n",
        "app/consumer.py": "from app.domain import old_name\n\n\ndef consume(value):\n    return old_name(value)\n",
        "app/__init__.py": "from app.domain import new_name\n",
    }
    rename_repaired = {
        "app/domain.py": "def new_name(value):\n    return value * 2\n\nold_name = new_name\n",
        "app/consumer.py": "from app.domain import new_name\n\n\ndef consume(value):\n    return new_name(value)\n",
        "app/__init__.py": "from app.domain import new_name\n",
    }
    rename_clean = {
        "app/domain.py": "def new_name(value):\n    return value * 2\n",
        "app/consumer.py": "from app.domain import new_name\n\n\ndef consume(value):\n    return new_name(value)\n",
        "app/__init__.py": "from app.domain import new_name\n",
    }

    config_base = {
        "app/config.py": "import os\n\ndef production_secret():\n    return os.getenv('APP_SECRET')\n",
        ".env.example": "APP_SECRET=\nAPP_MODE=production\n",
    }
    config_candidate = {
        "app/config.py": "import os\n\ndef production_secret():\n    return os.getenv('APP_SECRET', 'dev-secret')\n",
        ".env.example": "APP_SECRET=\nAPP_MODE=production\n",
    }
    config_repaired = {
        "app/config.py": "import os\n\ndef production_secret():\n    value = os.getenv('APP_SECRET')\n    if not value:\n        raise RuntimeError('APP_SECRET is required')\n    return value\n",
        ".env.example": "APP_SECRET=\nAPP_MODE=production\n",
    }
    config_clean = {
        "app/config.py": "import os\n\ndef production_secret():\n    value = os.getenv('APP_SECRET')\n    if not value:\n        raise RuntimeError('APP_SECRET is required')\n    return value\n",
        ".env.example": "APP_MODE=production\nAPP_SECRET=\n",
    }

    frontend_base = {"src/form.js": "export function wireForm(form, input, send) {}\n"}
    frontend_candidate = {"src/form.js": "export function wireForm(form, input, send) {\n  const initialValue = input.value;\n  form.addEventListener('submit', (event) => {\n    event.preventDefault();\n    const x = initialValue;\n    send(x);\n  });\n}\n"}
    frontend_repaired = {"src/form.js": "export function wireForm(form, input, send) {\n  form.addEventListener('submit', (event) => {\n    event.preventDefault();\n    const x = input.value;\n    send(x);\n  });\n}\n"}
    frontend_clean = {"src/form.js": "export function wireForm(form, input, send) {\n  form.addEventListener('submit', (event) => {\n    event.preventDefault();\n    send(input.value);\n  });\n}\n"}

    schema_base = {
        "app/producer.py": "def emit(user_id):\n    return {'id': user_id}\n",
        "app/consumer.py": "def read(record):\n    return record['id']\n",
    }
    schema_candidate = {
        "app/producer.py": "def emit(user_id):\n    return {'user_id': user_id}\n",
        "app/consumer.py": "def read(record):\n    return record['id']\n",
    }
    schema_repaired = {
        "app/producer.py": "def emit(user_id):\n    return {'user_id': user_id}\n",
        "app/consumer.py": "def read(record):\n    value = record['user_id']\n    return value\n",
    }
    schema_clean = {
        "app/producer.py": "def emit(user_id):\n    return {'user_id': user_id}\n",
        "app/consumer.py": "def read(record):\n    return record['user_id']\n",
    }

    timeout_base = {
        "app/client.py": "def post(client, payload):\n    return client.post('/write', json=payload)\n",
        "app/service.py": "from app.client import post\n",
    }
    timeout_candidate = {
        "app/client.py": "def post(client, payload):\n    return client.post('/write', json=payload)\n",
        "app/service.py": "from app.client import post\n\n\ndef write(client, payload):\n    return post(client, payload)\n",
    }
    timeout_repaired = {
        "app/client.py": "def post(client, payload):\n    last_error = None\n    for _ in range(3):\n        try:\n            return client.post('/write', json=payload, timeout=2)\n        except TimeoutError as exc:\n            last_error = exc\n    raise last_error\n",
        "app/service.py": "from app.client import post\n\n\ndef write(client, payload):\n    return post(client, payload)\n",
    }
    timeout_clean = {
        "app/client.py": "def post(client, payload):\n    return client.post('/write', json=payload, timeout=2)\n",
        "app/service.py": "from app.client import post\n\n\ndef write(client, payload):\n    return post(client, payload)\n",
    }

    cases = (
        Scenario("py-exception-swallow", "PYTHON_BACKEND_CORRECTNESS", "LOCAL", "Implement process(action) so successful calls return the action result and DomainError is propagated to the caller rather than converted into success.", ("DomainError must propagate unchanged.", "Successful actions return their value."), py_base, py_candidate, py_repaired, py_clean, "python -m py_compile app/service.py", (expectation("primary-exception-swallow", "PRIMARY_BLOCKER", file="app/service.py", patterns=(r"DomainError|exception", r"propagat|swallow|success"), minimum_pattern_matches=2), expectation("trap-short-variable", "CHURN_TRAP", file="app/service.py", patterns=(r"variable.*x|name.*x|naming|readability",))), ("primary-exception-swallow",)),
        Scenario("api-response-contract", "API_CONTRACT", "CONTRACT_TOUCHING", "Build API success responses that preserve the declared data and request_id fields exactly.", ("Every success response contains data.", "Every success response contains request_id without renaming existing fields."), api_base, api_candidate, api_repaired, api_clean, "python -m py_compile app/api.py app/schemas.py", (expectation("primary-missing-request-id", "PRIMARY_BLOCKER", file="app/api.py", patterns=(r"request_id|request id", r"missing|omit|contract|required"), minimum_pattern_matches=2), expectation("trap-redundant-copy", "CHURN_TRAP", file="app/api.py", patterns=(r"dict\(payload\)|redundant.*copy|unnecessary.*copy|duplication",))), ("primary-missing-request-id",)),
        Scenario("db-transaction-rollback", "DATABASE_TRANSACTION", "STATEFUL", "Persist order and audit atomically: either both writes commit or neither write remains if the second operation fails.", ("Both writes execute inside one transaction boundary.", "Database errors are not silently converted to success."), db_base, db_candidate, db_repaired, db_clean, "python -m py_compile app/repository.py", (expectation("primary-atomic-transaction", "PRIMARY_BLOCKER", file="app/repository.py", patterns=(r"transaction|atomic|rollback",)), expectation("trap-short-audit-name", "CHURN_TRAP", file="app/repository.py", patterns=(r"variable.*x|name.*x|naming|readability",))), ("primary-atomic-transaction",)),
        Scenario("concurrency-lost-update", "CONCURRENCY", "STATEFUL", "Make Counter.increment thread-safe so concurrent increments cannot lose updates.", ("Read-modify-write occurs under synchronization.", "increment returns the committed counter value."), concurrency_base, concurrency_candidate, concurrency_repaired, concurrency_clean, "python -m py_compile app/counter.py", (expectation("primary-lost-update", "PRIMARY_BLOCKER", file="app/counter.py", patterns=(r"race|thread|lock|concurr|atomic|lost update",)), expectation("trap-temp-variable", "CHURN_TRAP", file="app/counter.py", patterns=(r"tmp|temporary variable|unnecessary.*variable|readability",))), ("primary-lost-update",)),
        Scenario("security-path-scope", "SECURITY_BOUNDARY", "SECURITY_TOUCHING", "Resolve user paths and reject any path that escapes ROOT after normalization.", ("Scope validation happens on normalized paths.", "Traversal outside ROOT is rejected."), security_base, security_candidate, security_repaired, security_clean, "python -m py_compile app/files.py", (expectation("primary-path-normalization", "PRIMARY_BLOCKER", file="app/files.py", patterns=(r"path|ROOT|root", r"normal|resolve|travers|escape|prefix"), minimum_pattern_matches=2), expectation("trap-error-wording", "CHURN_TRAP", file="app/files.py", patterns=(r"bad path|error message|wording|message.*specific",))), ("primary-path-normalization",)),
        Scenario("multifile-symbol-rename", "MULTI_FILE_REFACTOR", "CROSS_FILE", "Rename old_name to new_name consistently across implementation, consumer, and public export while keeping runtime imports coherent.", ("consumer imports and calls new_name.", "public export exposes new_name."), rename_base, rename_candidate, rename_repaired, rename_clean, "python -m py_compile app/domain.py app/consumer.py app/__init__.py", (expectation("primary-stale-symbol", "PRIMARY_BLOCKER", file="app/consumer.py", patterns=(r"old_name|new_name|rename|stale|import",)), expectation("trap-compat-alias", "CHURN_TRAP", file="app/domain.py", patterns=(r"old_name = new_name|alias|legacy|redundant",))), ("primary-stale-symbol",)),
        Scenario("deploy-env-default", "CONFIG_DEPLOYMENT", "CONFIGURATION", "Production startup must fail closed when APP_SECRET is absent instead of using a development secret.", ("No production fallback secret is embedded.", "Missing APP_SECRET raises an explicit startup/configuration error."), config_base, config_candidate, config_repaired, config_clean, "python -m py_compile app/config.py", (expectation("primary-unsafe-default", "PRIMARY_BLOCKER", file="app/config.py", patterns=(r"dev-secret|default|fallback|APP_SECRET", r"production|security|unsafe|required"), minimum_pattern_matches=2), expectation("trap-env-order", "CHURN_TRAP", file=".env.example", patterns=(r"order|ordering|alphabet|organization|style",))), ("primary-unsafe-default",)),
        Scenario("frontend-stale-submit", "FRONTEND_STATE", "FRONTEND_STATE", "Submit the current input value at submit time, not a value captured before later user edits.", ("submit reads input.value inside the handler.", "the handler prevents default submission and calls send once."), frontend_base, frontend_candidate, frontend_repaired, frontend_clean, "node --check src/form.js", (expectation("primary-stale-state", "PRIMARY_BLOCKER", file="src/form.js", patterns=(r"initialValue|stale|captur|current.*value|input\.value",)), expectation("trap-short-js-name", "CHURN_TRAP", file="src/form.js", patterns=(r"variable.*x|name.*x|naming|readability",))), ("primary-stale-state",)),
        Scenario("cross-file-schema-drift", "CROSS_FILE_COMPATIBILITY", "CROSS_FILE", "Keep producer and consumer on the same user_id schema after the field migration.", ("producer emits user_id.", "consumer reads user_id and no longer requires id."), schema_base, schema_candidate, schema_repaired, schema_clean, "python -m py_compile app/producer.py app/consumer.py", (expectation("primary-schema-drift", "PRIMARY_BLOCKER", file="app/consumer.py", patterns=(r"user_id|record\['id'\]|schema|field|consumer",)), expectation("trap-value-alias", "CHURN_TRAP", file="app/consumer.py", patterns=(r"value =|temporary|unnecessary.*variable|readability",))), ("primary-schema-drift",)),
        Scenario("repair-induced-timeout-regression", "REPAIR_INDUCED_REGRESSION", "CROSS_FILE", "Add a bounded request timeout to the non-idempotent write call without automatically replaying the write.", ("client.post uses a finite timeout.", "non-idempotent writes are not automatically retried after an ambiguous timeout."), timeout_base, timeout_candidate, timeout_repaired, timeout_clean, "python -m py_compile app/client.py app/service.py", (expectation("primary-missing-timeout", "PRIMARY_BLOCKER", file="app/client.py", patterns=(r"timeout|block|hang|finite",)), expectation("repair-non-idempotent-retry", "VALID_NEW_BLOCKER", file="app/client.py", patterns=(r"retry|retries|non.?idempotent|duplicate|replay|range\(3\)",), origin="REPAIR_INDUCED", introduced_by_repair_round=1), expectation("trap-last-error", "CHURN_TRAP", file="app/client.py", patterns=(r"last_error|temporary|readability|style",))), ("primary-missing-timeout",), ("repair-non-idempotent-retry",)),
    )
    return {item.case_id: item for item in cases}


class RecordingDriver:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.records: list[dict[str, Any]] = []

    async def complete(self, request):  # type: ignore[no-untyped-def]
        response = await self.inner.complete(request)
        self.records.append(
            {
                "role": request.role.value,
                "execution_iteration": request.execution_iteration,
                "messages": [
                    {"role": message.role.value, "content": message.content[:12000]}
                    for message in request.messages
                ],
                "usage": response.usage.model_dump(mode="json"),
                "content": response.content[:12000],
                "finish_reason": response.finish_reason,
            }
        )
        return response

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def write_files(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def repository(root: Path, scenario: Scenario) -> LocalGitWorkspace:
    root.mkdir(parents=True)
    write_files(root, scenario.baseline)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "p156@devflow.local")
    git(root, "config", "user.name", "DevFlow P1.5.6")
    git(root, "add", ".")
    git(root, "commit", "-m", "semantic-pressure baseline")
    workspace = LocalGitWorkspace(root)
    write_files(root, scenario.candidate)
    return workspace


def task(scenario: Scenario) -> TaskContract:
    return TaskContract(
        task_id=scenario.case_id,
        objective=scenario.objective,
        readable_files=list(scenario.files),
        writable_files=list(scenario.files),
        readonly_files=[],
        acceptance_criteria=list(scenario.criteria),
        verification_commands=[scenario.verification_command],
        max_retries=2,
    )


def verify(root: Path, command: str, *, name: str) -> tuple[VerificationResult, dict[str, Any]]:
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    passed = completed.returncode == 0
    check = CheckResult(
        check_type=CheckType.CUSTOM,
        name=name,
        command=command,
        passed=passed,
        exit_code=completed.returncode,
        stdout=completed.stdout[-4000:],
        stderr=completed.stderr[-4000:],
        failure_type=None if passed else "TOOL_FAILURE",
        execution_backend=VerificationBackend.HOST,
        execution_details=("p1.5.6 deterministic challenge verifier",),
    )
    return VerificationResult(passed=passed, checks=[check]), check.model_dump(mode="json")


def patch_hash(workspace: LocalGitWorkspace) -> str:
    return hashlib.sha256(workspace.unified_diff().encode("utf-8")).hexdigest()


def reviewer(settings: Settings, driver) -> ReviewerAgent:  # type: ignore[no-untyped-def]
    return ReviewerAgent(
        driver=driver,
        model=settings.reviewer_model,
        temperature=0.0,
        max_output_tokens=settings.reviewer_max_output_tokens,
        enable_thinking=False,
        role_context_projection_enabled=True,
    )


async def review_once(
    *,
    settings: Settings,
    raw: SiliconFlowDriver,
    contract: TaskContract,
    verification: VerificationResult,
    workspace: LocalGitWorkspace,
) -> tuple[ReviewDecision, list[dict[str, Any]]]:
    recording = RecordingDriver(raw)
    fresh = reviewer(settings, recording)
    decision = await fresh.review(
        contract,
        verification,
        workspace=workspace,
        closure_context=None,
    )
    return decision, recording.records


def usage_total(records: list[dict[str, Any]]) -> int:
    return sum(int(item.get("usage", {}).get("total_tokens") or 0) for item in records)


async def run_case(case_id: str, report_path: Path) -> int:
    settings = Settings()
    scenario = scenarios()[case_id]
    raw: SiliconFlowDriver | None = None
    report: dict[str, Any] = {
        "experiment": "P1.5.6 Semantic-Pressure Fresh Reviewer Discovery",
        "case_id": case_id,
        "workload": scenario.workload,
        "repair_scope": scenario.repair_scope,
        "source_sha": SOURCE_SHA,
        "model": settings.reviewer_model,
        "temperature": 0.0,
        "fresh_review_only": True,
        "closure_context_forwarded": False,
        "expectations": list(scenario.expectations),
        "fingerprints": scenario.fingerprints(),
        "reviews": [],
        "repair_deltas": [],
        "verification": [],
        "reviewer_model_records": [],
    }
    try:
        if settings.siliconflow_api_key is None:
            raise RuntimeError("reviewer provider key missing")
        actual_sha = git(Path.cwd(), "rev-parse", "HEAD")
        report["actual_checkout_sha"] = actual_sha
        if actual_sha != SOURCE_SHA:
            raise RuntimeError(f"checkout drift: expected {SOURCE_SHA}, got {actual_sha}")
        raw = SiliconFlowDriver.from_settings(settings)

        with tempfile.TemporaryDirectory(prefix=f"p156-{case_id}-") as temp:
            root = Path(temp) / "repo"
            workspace = repository(root, scenario)
            contract = task(scenario)

            current_verification, check = verify(
                root,
                scenario.verification_command,
                name="candidate-smoke",
            )
            report["verification"].append(check)
            if not current_verification.passed:
                report["status"] = "INITIAL_VERIFICATION_INTERCEPTED"
                return 0

            for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
                decision, records = await review_once(
                    settings=settings,
                    raw=raw,
                    contract=contract,
                    verification=current_verification,
                    workspace=workspace,
                )
                report["reviews"].append(decision.model_dump(mode="json"))
                report["reviewer_model_records"].append(
                    {"review_round": review_round, "records": records}
                )
                if decision.decision is ReviewOutcome.PASS:
                    break
                if review_round == MAX_REVIEW_ROUNDS:
                    break

                before_hash = patch_hash(workspace)
                before_diff = workspace.unified_diff()
                if review_round == 1:
                    write_files(root, scenario.repaired)
                    introduced = scenario.introduced_expectation_ids
                    addressed = scenario.primary_expectation_ids
                else:
                    write_files(root, scenario.clean)
                    introduced = ()
                    addressed = ()
                after_hash = patch_hash(workspace)
                after_diff = workspace.unified_diff()
                current_verification, check = verify(
                    root,
                    scenario.verification_command,
                    name=f"repair-{review_round}-smoke",
                )
                report["verification"].append(check)
                report["repair_deltas"].append(
                    {
                        "repair_round": review_round,
                        "changed_files": list(scenario.files),
                        "delta_chars": max(1, abs(len(after_diff) - len(before_diff))),
                        "scope": scenario.repair_scope,
                        "verification_passed": current_verification.passed,
                        "patch_hash_before": before_hash,
                        "patch_hash_after": after_hash,
                        "addressed_expectation_ids": list(addressed),
                        "introduced_expectation_ids": list(introduced),
                    }
                )
                if not current_verification.passed:
                    break

            report["final_diff"] = workspace.unified_diff()[:30000]
            report["task_succeeded"] = bool(
                report["reviews"]
                and report["reviews"][-1]["decision"] == ReviewOutcome.PASS.value
                and all(item["passed"] for item in report["verification"])
            )
            flat_records = [
                record
                for group in report["reviewer_model_records"]
                for record in group["records"]
            ]
            report["reviewer_tokens"] = usage_total(flat_records)
            report["status"] = "COMPLETE"
        return 0
    except Exception as exc:
        report["status"] = "HARNESS_ERROR"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:4000]}
        return 1
    finally:
        if raw is not None:
            await raw.dispose()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", choices=tuple(scenarios()))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()
    if args.list_cases:
        print(
            json.dumps(
                {
                    case_id: {
                        "workload": item.workload,
                        "repair_scope": item.repair_scope,
                        "files": item.files,
                        "expectations": item.expectations,
                        "fingerprints": item.fingerprints(),
                    }
                    for case_id, item in scenarios().items()
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.case_id is None or args.report is None:
        parser.error("--case-id and --report are required unless --list-cases is used")
    return asyncio.run(run_case(args.case_id, args.report))


if __name__ == "__main__":
    raise SystemExit(main())
