from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing patch anchor {label!r} in {path}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if marker in text:
        raise RuntimeError(f"test marker already present in {path}: {marker}")
    file_path.write_text(text + dedent(addition), encoding="utf-8")


# TaskContract: keep write authorization separate from explicit mandatory outputs.
replace_once(
    "backend/app/models/task.py",
    "from __future__ import annotations\n\nfrom pydantic",
    "from __future__ import annotations\n\nimport re\n\nfrom pydantic",
    label="task imports",
)
replace_once(
    "backend/app/models/task.py",
    '''def _normalize_non_empty_text(value: str) -> str:\n    normalized = value.strip()\n    if not normalized:\n        raise ValueError("value must not be empty")\n    return normalized\n\n\nclass TaskContract(BaseModel):\n''',
    '''def _normalize_non_empty_text(value: str) -> str:\n    normalized = value.strip()\n    if not normalized:\n        raise ValueError("value must not be empty")\n    return normalized\n\n\ndef _normalize_required_output_path(value: str) -> str:\n    path = _normalize_scope_pattern(value)\n    if "*" in path or "?" in path or path.endswith("/"):\n        raise ValueError("required output files must be exact repository file paths")\n    return path\n\n\ndef _scope_pattern_matches(path: str, pattern: str) -> bool:\n    result: list[str] = []\n    index = 0\n    while index < len(pattern):\n        if pattern.startswith("**/", index):\n            result.append("(?:.*/)?")\n            index += 3\n            continue\n        if pattern.startswith("**", index):\n            result.append(".*")\n            index += 2\n            continue\n        char = pattern[index]\n        if char == "*":\n            result.append("[^/]*")\n        elif char == "?":\n            result.append("[^/]")\n        else:\n            result.append(re.escape(char))\n        index += 1\n    return re.fullmatch("".join(result), path) is not None\n\n\nclass TaskContract(BaseModel):\n''',
    label="task helper insertion",
)
replace_once(
    "backend/app/models/task.py",
    '''    writable_files: list[str] = Field(min_length=1)\n    readonly_files: list[str] = Field(default_factory=list)\n''',
    '''    writable_files: list[str] = Field(min_length=1)\n    required_output_files: list[str] | None = Field(default=None, min_length=1, max_length=16)\n    readonly_files: list[str] = Field(default_factory=list)\n''',
    label="required output field",
)
replace_once(
    "backend/app/models/task.py",
    '''    @field_validator("acceptance_criteria", "verification_commands")\n    @classmethod\n    def validate_non_empty_items(cls, values: list[str]) -> list[str]:\n''',
    '''    @field_validator("required_output_files")\n    @classmethod\n    def validate_required_output_files(cls, values: list[str] | None) -> list[str] | None:\n        if values is None:\n            return None\n        normalized = [_normalize_required_output_path(value) for value in values]\n        if len(normalized) != len(set(normalized)):\n            raise ValueError("required output files must not contain duplicates")\n        return normalized\n\n    @field_validator("acceptance_criteria", "verification_commands")\n    @classmethod\n    def validate_non_empty_items(cls, values: list[str]) -> list[str]:\n''',
    label="required output validator",
)
replace_once(
    "backend/app/models/task.py",
    '''        if overlap:\n            joined = ", ".join(sorted(overlap))\n            raise ValueError(f"writable_files and readonly_files overlap: {joined}")\n        return self\n''',
    '''        if overlap:\n            joined = ", ".join(sorted(overlap))\n            raise ValueError(f"writable_files and readonly_files overlap: {joined}")\n        for path in self.required_output_files or []:\n            if not any(_scope_pattern_matches(path, pattern) for pattern in self.writable_files):\n                raise ValueError(\n                    f"required output file is outside writable scope: {path}"\n                )\n            if any(_scope_pattern_matches(path, pattern) for pattern in self.readonly_files):\n                raise ValueError(\n                    f"required output file is protected by readonly scope: {path}"\n                )\n        return self\n''',
    label="required output scope validation",
)

# WorkPackage proposal carries explicit mandatory files separately from owned paths.
replace_once(
    "backend/app/models/work_package.py",
    '''from app.models.task import _normalize_non_empty_text, _normalize_scope_pattern\n''',
    '''from app.models.task import (\n    _normalize_non_empty_text,\n    _normalize_required_output_path,\n    _normalize_scope_pattern,\n    _scope_pattern_matches,\n)\n''',
    label="work package task imports",
)
replace_once(
    "backend/app/models/work_package.py",
    '''    owned_paths: tuple[str, ...] = Field(min_length=1, max_length=5)\n    readable_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)\n''',
    '''    owned_paths: tuple[str, ...] = Field(min_length=1, max_length=5)\n    required_output_files: tuple[str, ...] | None = Field(\n        default=None, min_length=1, max_length=5\n    )\n    readable_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)\n''',
    label="work package required outputs",
)
replace_once(
    "backend/app/models/work_package.py",
    '''    @field_validator("produces", "consumes", "acceptance_criteria", "verification_commands")\n    @classmethod\n    def normalize_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:\n''',
    '''    @field_validator("required_output_files")\n    @classmethod\n    def normalize_required_outputs(\n        cls, values: tuple[str, ...] | None\n    ) -> tuple[str, ...] | None:\n        if values is None:\n            return None\n        normalized = tuple(_normalize_required_output_path(value) for value in values)\n        if len(normalized) != len(set(normalized)):\n            raise ValueError("required output files must not contain duplicates")\n        return normalized\n\n    @field_validator("produces", "consumes", "acceptance_criteria", "verification_commands")\n    @classmethod\n    def normalize_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:\n''',
    label="work package required output validator",
)
replace_once(
    "backend/app/models/work_package.py",
    '''        if set(self.owned_paths) & set(self.readable_paths):\n            raise ValueError("owned_paths and readable_paths must not overlap")\n        if (\n''',
    '''        if set(self.owned_paths) & set(self.readable_paths):\n            raise ValueError("owned_paths and readable_paths must not overlap")\n        for path in self.required_output_files or ():\n            if not any(_scope_pattern_matches(path, pattern) for pattern in self.owned_paths):\n                raise ValueError(\n                    f"required output file is outside owned path scope: {path}"\n                )\n        if (\n''',
    label="work package required output boundary",
)

# Convert required outputs into executable TaskContracts.
replace_once(
    "backend/app/planning/work_packages.py",
    '''                    readable_files=list(package.readable_paths),\n                    writable_files=list(package.owned_paths),\n                    readonly_files=[],\n''',
    '''                    readable_files=list(package.readable_paths),\n                    writable_files=list(package.owned_paths),\n                    required_output_files=(\n                        list(package.required_output_files)\n                        if package.required_output_files is not None\n                        else None\n                    ),\n                    readonly_files=[],\n''',
    label="work package conversion",
)

# Single-task Planner learns the optional readiness evidence field.
replace_once(
    "backend/app/agents/planner.py",
    '''            '{"task_id":"ascii-id","objective":"...","readable_files":["..."],'\n            '"writable_files":["..."],"readonly_files":["..."],'\n''',
    '''            '{"task_id":"ascii-id","objective":"...","readable_files":["..."],'\n            '"writable_files":["..."],"required_output_files":["src/file.py"],'\n            '"readonly_files":["..."],'\n''',
    label="single planner compact shape",
)
replace_once(
    "backend/app/agents/planner.py",
    '''            "4. writable_files must contain at least one narrowly scoped implementation path.\\n"\n            "5. readonly_files should protect tests or other files the implementation must not "\n''',
    '''            "4. writable_files defines write authorization and must contain at least one narrowly "\n            "scoped implementation path. required_output_files is separate completion evidence: "\n            "list only exact files that must be created or modified, never globs; use null when "\n            "the exact mandatory outputs are unknown.\\n"\n            "5. readonly_files should protect tests or other files the implementation must not "\n''',
    label="single planner readiness rule",
)

# Multi-task Planner exposes the same distinction at WorkPackage level.
replace_once(
    "backend/app/agents/dag_planner.py",
    '''            '{"packages":[{"package_id":"ascii-id","objective":"...",'\n            '"deliverable":"one concrete artifact","owned_paths":["src/file.py"],'\n            '"readable_paths":["src/shared/**"],"produces":["module.Symbol"],'\n''',
    '''            '{"packages":[{"package_id":"ascii-id","objective":"...",'\n            '"deliverable":"one concrete artifact","owned_paths":["src/file.py"],'\n            '"required_output_files":["src/file.py"],'\n            '"readable_paths":["src/shared/**"],"produces":["module.Symbol"],'\n''',
    label="multi planner compact shape",
)
replace_once(
    "backend/app/agents/dag_planner.py",
    '''            "4. File scopes must be repository-relative POSIX paths or glob patterns. Keep "\n            "owned_paths narrow and do not use more than five.\\n"\n''',
    '''            "4. File scopes must be repository-relative POSIX paths or glob patterns. Keep "\n            "owned_paths narrow and do not use more than five. owned_paths defines authorization "\n            "and ownership; required_output_files is separate completion evidence. List only exact "\n            "files that must be created or modified, never globs, and use null when exact mandatory "\n            "outputs are unknown. Every required output must be covered by owned_paths.\\n"\n''',
    label="multi planner readiness rule",
)
replace_once(
    "backend/app/agents/dag_planner.py",
    '''            "Each package has one deliverable, at most five owned_paths, non-empty produces, "\n            "acceptance_criteria and deterministic verification_commands; consumes must reference "\n''',
    '''            "Each package has one deliverable, at most five owned_paths, optional exact "\n            "required_output_files, non-empty produces, acceptance_criteria and deterministic "\n            "verification_commands; consumes must reference "\n''',
    label="multi planner recovery prompt",
)

# Runtime: explicit structural readiness can only accelerate P1.4; it never delays fallback.
replace_once(
    "backend/app/agent_runtime/loop.py",
    '''        start_snapshot = workspace.change_snapshot()\n        previous_turn_made_progress = False\n''',
    '''        start_snapshot = workspace.change_snapshot()\n        required_output_files = (\n            frozenset(toolbox.task.required_output_files)\n            if toolbox.task.required_output_files is not None\n            else None\n        )\n        previous_turn_made_progress = False\n''',
    label="runtime required outputs",
)
replace_once(
    "backend/app/agent_runtime/loop.py",
    '''            changed_files_this_turn = tuple(\n                after_turn_snapshot.files_changed_since(before_turn_snapshot)\n            )\n            progress = ToolProgressClassifier.summarize(\n''',
    '''            changed_files_this_turn = tuple(\n                after_turn_snapshot.files_changed_since(before_turn_snapshot)\n            )\n            candidate_changed_files = frozenset(\n                after_turn_snapshot.files_changed_since(start_snapshot)\n            )\n            candidate_ready = (\n                required_output_files is not None\n                and required_output_files.issubset(candidate_changed_files)\n                and all(\n                    workspace.resolve_path(path).is_file()\n                    for path in required_output_files\n                )\n            )\n            progress = ToolProgressClassifier.summarize(\n''',
    label="runtime readiness calculation",
)
replace_once(
    "backend/app/agent_runtime/loop.py",
    '''                if (\n                    policy.mutation_convergence_enabled\n                    and has_workspace_patch\n                    and observation_turns_without_mutation\n                    >= policy.post_mutation_observation_handoff_threshold\n                ):\n                    events.append(\n                        AgentRuntimeEvent(\n                            sequence=len(events),\n                            kind=AgentRuntimeEventKind.MUTATION_GATE,\n                            iteration=iteration,\n                            progress_kind=ToolProgressKind.OBSERVATION,\n                            detail="candidate_handoff_after_observation",\n                        )\n                    )\n''',
    '''                handoff_threshold = (\n                    1\n                    if candidate_ready\n                    else policy.post_mutation_observation_handoff_threshold\n                )\n                if (\n                    policy.mutation_convergence_enabled\n                    and has_workspace_patch\n                    and observation_turns_without_mutation >= handoff_threshold\n                ):\n                    readiness_handoff = candidate_ready and handoff_threshold == 1\n                    events.append(\n                        AgentRuntimeEvent(\n                            sequence=len(events),\n                            kind=AgentRuntimeEventKind.MUTATION_GATE,\n                            iteration=iteration,\n                            progress_kind=ToolProgressKind.OBSERVATION,\n                            detail=(\n                                "candidate_handoff_after_ready_observation"\n                                if readiness_handoff\n                                else "candidate_handoff_after_observation"\n                            ),\n                        )\n                    )\n''',
    label="runtime early handoff condition",
)
replace_once(
    "backend/app/agent_runtime/loop.py",
    '''                        final_message=(\n                            "Candidate implementation handed to deterministic verification "\n                            f"after {observation_turns_without_mutation} consecutive "\n                            "observation-only turns with no repository progress."\n                        ),\n''',
    '''                        final_message=(\n                            (\n                                "Structurally ready candidate handed to deterministic verification "\n                                "after the first observation-only turn with no repository progress."\n                            )\n                            if readiness_handoff\n                            else (\n                                "Candidate implementation handed to deterministic verification "\n                                f"after {observation_turns_without_mutation} consecutive "\n                                "observation-only turns with no repository progress."\n                            )\n                        ),\n''',
    label="runtime early handoff message",
)

# Fixed real-token diagnostic explicitly declares its structural outputs.
for writable, required in (
    (
        '        writable_files=["examples/gomoku/gomoku_core.js"],\n',
        '        writable_files=["examples/gomoku/gomoku_core.js"],\n'
        '        required_output_files=["examples/gomoku/gomoku_core.js"],\n',
    ),
    (
        '        writable_files=["examples/gomoku/index.html", "examples/gomoku/gomoku_ui.js"],\n',
        '        writable_files=["examples/gomoku/index.html", "examples/gomoku/gomoku_ui.js"],\n'
        '        required_output_files=[\n'
        '            "examples/gomoku/index.html",\n'
        '            "examples/gomoku/gomoku_ui.js",\n'
        '        ],\n',
    ),
    (
        '        writable_files=["examples/gomoku/gomoku_integration.test.cjs"],\n',
        '        writable_files=["examples/gomoku/gomoku_integration.test.cjs"],\n'
        '        required_output_files=["examples/gomoku/gomoku_integration.test.cjs"],\n',
    ),
):
    replace_once(
        "scripts/real_token_budget_diagnostic.py",
        writable,
        required,
        label=f"diagnostic required output {writable.strip()}",
    )

# Model regressions.
replace_once(
    "backend/tests/test_models.py",
    '''    assert contract.writable_files == ["src/**"]\n    assert contract.readonly_files == ["tests/**"]\n''',
    '''    assert contract.writable_files == ["src/**"]\n    assert contract.required_output_files is None\n    assert contract.readonly_files == ["tests/**"]\n''',
    label="model backward compatibility assertion",
)
append_once(
    "backend/tests/test_models.py",
    "def test_task_contract_accepts_explicit_required_output_within_writable_scope",
    r'''


def test_task_contract_accepts_explicit_required_output_within_writable_scope() -> None:
    contract = make_task_contract(required_output_files=["src/auth/service.py"])

    assert contract.required_output_files == ["src/auth/service.py"]


@pytest.mark.parametrize("path", ["src/**", "src/*.py", "src/auth/"])
def test_task_contract_rejects_non_exact_required_output(path: str) -> None:
    with pytest.raises(ValidationError, match="exact repository file paths"):
        make_task_contract(required_output_files=[path])


def test_task_contract_rejects_required_output_outside_writable_scope() -> None:
    with pytest.raises(ValidationError, match="outside writable scope"):
        make_task_contract(required_output_files=["tests/test_auth.py"])


def test_task_contract_rejects_required_output_protected_by_readonly_scope() -> None:
    with pytest.raises(ValidationError, match="protected by readonly scope"):
        make_task_contract(
            writable_files=["src/**", "tests/generated.py"],
            readonly_files=["tests/**"],
            required_output_files=["tests/generated.py"],
        )
''',
)

# Single Planner contract/prompt regression.
replace_once(
    "backend/tests/test_planner_agent.py",
    '''    "writable_files": ["app/auth/**"],\n    "readonly_files": ["tests/**"],\n''',
    '''    "writable_files": ["app/auth/**"],\n    "required_output_files": ["app/auth/service.py"],\n    "readonly_files": ["tests/**"],\n''',
    label="single planner valid task",
)
replace_once(
    "backend/tests/test_planner_agent.py",
    '''    assert task.writable_files == ["app/auth/**"]\n    assert len(driver.requests) == 1\n''',
    '''    assert task.writable_files == ["app/auth/**"]\n    assert task.required_output_files == ["app/auth/service.py"]\n    assert len(driver.requests) == 1\n''',
    label="single planner required output assertion",
)
replace_once(
    "backend/tests/test_planner_agent.py",
    '''    assert "compact shape" in driver.requests[0].messages[0].content\n    assert "TaskContract JSON Schema" not in driver.requests[0].messages[0].content\n''',
    '''    assert "compact shape" in driver.requests[0].messages[0].content\n    assert "required_output_files" in driver.requests[0].messages[0].content\n    assert "TaskContract JSON Schema" not in driver.requests[0].messages[0].content\n''',
    label="single planner prompt assertion",
)

# Multi Planner carries required outputs through conversion.
replace_once(
    "backend/tests/test_multi_task_planner.py",
    '''    "owned_paths": ["app/auth/tokens.py"],\n    "readable_paths": ["app/**"],\n''',
    '''    "owned_paths": ["app/auth/tokens.py"],\n    "required_output_files": ["app/auth/tokens.py"],\n    "readable_paths": ["app/**"],\n''',
    label="multi package A required output",
)
replace_once(
    "backend/tests/test_multi_task_planner.py",
    '''    "owned_paths": ["app/api/auth.py"],\n    "readable_paths": ["app/auth/**"],\n''',
    '''    "owned_paths": ["app/api/auth.py"],\n    "required_output_files": ["app/api/auth.py"],\n    "readable_paths": ["app/auth/**"],\n''',
    label="multi package B required output",
)
replace_once(
    "backend/tests/test_multi_task_planner.py",
    '''    assert "owned_paths" in driver.requests[0].messages[0].content\n    assert driver.requests[0].max_output_tokens == 1_000\n''',
    '''    assert "owned_paths" in driver.requests[0].messages[0].content\n    assert "required_output_files" in driver.requests[0].messages[0].content\n    assert dag.node("auth-model").task.required_output_files == ["app/auth/tokens.py"]\n    assert driver.requests[0].max_output_tokens == 1_000\n''',
    label="multi planner required output assertion",
)

# WorkPackage conversion and ownership semantics.
append_once(
    "backend/tests/test_work_package_planning.py",
    "def test_converter_keeps_required_outputs_separate_from_owned_paths",
    r'''


def test_converter_keeps_required_outputs_separate_from_owned_paths() -> None:
    package = _package(
        owned_paths=("gomoku/**",),
        required_output_files=("gomoku/core.py",),
    )

    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(packages=(package,)),
        requirement="实现核心模型。",
        max_tasks=8,
    )

    task = result.dag.node("core-model").task
    assert task.writable_files == ["gomoku/**"]
    assert task.required_output_files == ["gomoku/core.py"]


def test_work_package_rejects_required_output_outside_owned_scope() -> None:
    with pytest.raises(ValueError, match="outside owned path scope"):
        _package(required_output_files=("other/core.py",))
''',
)

# Developer convergence: explicit readiness accelerates only after all mandatory files exist.
replace_once(
    "backend/tests/test_developer_mutation_convergence.py",
    '''def _task(*, writable_files: list[str] | None = None) -> models.TaskContract:\n    return models.TaskContract(\n''',
    '''def _task(\n    *,\n    writable_files: list[str] | None = None,\n    required_output_files: list[str] | None = None,\n) -> models.TaskContract:\n    return models.TaskContract(\n''',
    label="developer task helper signature",
)
replace_once(
    "backend/tests/test_developer_mutation_convergence.py",
    '''        writable_files=writable_files or ["src/gomoku_logic.py"],\n        readonly_files=[],\n''',
    '''        writable_files=writable_files or ["src/gomoku_logic.py"],\n        required_output_files=required_output_files,\n        readonly_files=[],\n''',
    label="developer task helper required outputs",
)
append_once(
    "backend/tests/test_developer_mutation_convergence.py",
    "def test_explicit_ready_candidate_hands_off_after_first_observation",
    r'''


def test_explicit_ready_candidate_hands_off_after_first_observation(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-ready",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ready",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(
                writable_files=["src/gomoku_logic.py", "src/optional.py"],
                required_output_files=["src/gomoku_logic.py"],
            ),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 2
    assert driver.progress_outcomes == [True, False]
    assert "Structurally ready candidate" in result.final_message
    assert any(
        event.detail == "candidate_handoff_after_ready_observation"
        for event in result.events
    )


def test_incomplete_required_outputs_do_not_trigger_early_handoff(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>board</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-index",
                        "read_range",
                        {"path": "src/index.html", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ui",
                        "read_range",
                        {"path": "src/gomoku_ui.js", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(
                writable_files=["src/index.html", "src/gomoku_ui.js"],
                required_output_files=["src/index.html", "src/gomoku_ui.js"],
            ),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 4
    assert driver.progress_outcomes == [True, False, True, False]
    assert "Structurally ready candidate" in result.final_message


def test_missing_required_output_preserves_p14_fallback_handoff(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>board</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-index-1",
                        "read_range",
                        {"path": "src/index.html", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-index-2",
                        "read_range",
                        {"path": "src/index.html", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(
                writable_files=["src/index.html", "src/gomoku_ui.js"],
                required_output_files=["src/index.html", "src/gomoku_ui.js"],
            ),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    assert driver.progress_outcomes == [True, False, False]
    assert "after 2 consecutive observation-only turns" in result.final_message
''',
)
