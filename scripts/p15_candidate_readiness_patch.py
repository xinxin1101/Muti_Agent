from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


def patch_loop() -> None:
    path = Path("backend/app/agent_runtime/loop.py")
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        """        start_snapshot = workspace.change_snapshot()\n        previous_turn_made_progress = False\n""",
        """        start_snapshot = workspace.change_snapshot()\n        candidate_required_paths = self._exact_candidate_required_paths(toolbox)\n        candidate_readiness_known = candidate_required_paths is not None\n        previous_turn_made_progress = False\n""",
        label="start snapshot",
    )

    text = replace_once(
        text,
        """            changed_files_this_turn = tuple(\n                after_turn_snapshot.files_changed_since(before_turn_snapshot)\n            )\n            progress = ToolProgressClassifier.summarize(\n""",
        """            changed_files_this_turn = tuple(\n                after_turn_snapshot.files_changed_since(before_turn_snapshot)\n            )\n            candidate_changed_files = set(\n                after_turn_snapshot.files_changed_since(start_snapshot)\n            )\n            candidate_ready = (\n                candidate_required_paths is not None\n                and set(candidate_required_paths).issubset(candidate_changed_files)\n            )\n            progress = ToolProgressClassifier.summarize(\n""",
        label="candidate snapshot",
    )

    text = replace_once(
        text,
        """                if (\n                    policy.mutation_convergence_enabled\n                    and has_workspace_patch\n                    and observation_turns_without_mutation\n                    >= policy.post_mutation_observation_handoff_threshold\n                ):\n                    events.append(\n                        AgentRuntimeEvent(\n                            sequence=len(events),\n                            kind=AgentRuntimeEventKind.MUTATION_GATE,\n                            iteration=iteration,\n                            progress_kind=ToolProgressKind.OBSERVATION,\n                            detail=\"candidate_handoff_after_observation\",\n                        )\n                    )\n""",
        """                candidate_handoff_ready = (\n                    candidate_ready\n                    if candidate_readiness_known\n                    else observation_turns_without_mutation\n                    >= policy.post_mutation_observation_handoff_threshold\n                )\n                if (\n                    policy.mutation_convergence_enabled\n                    and has_workspace_patch\n                    and candidate_handoff_ready\n                ):\n                    handoff_detail = (\n                        \"candidate_handoff_after_ready_observation\"\n                        if candidate_readiness_known\n                        else \"candidate_handoff_after_observation\"\n                    )\n                    events.append(\n                        AgentRuntimeEvent(\n                            sequence=len(events),\n                            kind=AgentRuntimeEventKind.MUTATION_GATE,\n                            iteration=iteration,\n                            progress_kind=ToolProgressKind.OBSERVATION,\n                            detail=handoff_detail,\n                        )\n                    )\n""",
        label="candidate handoff",
    )

    text = replace_once(
        text,
        """                        final_message=(\n                            \"Candidate implementation handed to deterministic verification \"\n                            f\"after {observation_turns_without_mutation} consecutive \"\n                            \"observation-only turns with no repository progress.\"\n                        ),\n""",
        """                        final_message=(\n                            (\n                                \"Structurally complete candidate implementation handed to \"\n                                \"deterministic verification after the first observation-only \"\n                                \"turn with no repository progress.\"\n                            )\n                            if candidate_readiness_known\n                            else (\n                                \"Candidate implementation handed to deterministic verification \"\n                                f\"after {observation_turns_without_mutation} consecutive \"\n                                \"observation-only turns with no repository progress.\"\n                            )\n                        ),\n""",
        label="candidate handoff message",
    )

    text = replace_once(
        text,
        """    @staticmethod\n    def _mutation_required_prompt(*, strict: bool, reason: str) -> str:\n""",
        """    @staticmethod\n    def _exact_candidate_required_paths(\n        toolbox: RepositoryToolbox,\n    ) -> tuple[str, ...] | None:\n        \"\"\"Return exact writable deliverables, or None when completeness is unknowable.\n\n        TaskContract.writable_files also defines authorization scope and may contain globs.\n        A glob describes where the Agent may write, not how many files must be delivered, so\n        it cannot safely prove candidate completeness. Exact paths are conservative structural\n        evidence: every path must remain changed relative to the task's starting snapshot.\n        \"\"\"\n\n        writable_files = tuple(toolbox.task.writable_files)\n        if any(\n            \"*\" in path or \"?\" in path or path.endswith(\"/\")\n            for path in writable_files\n        ):\n            return None\n        return writable_files\n\n    @staticmethod\n    def _mutation_required_prompt(*, strict: bool, reason: str) -> str:\n""",
        label="candidate readiness helper",
    )

    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = Path("backend/tests/test_developer_mutation_convergence.py")
    text = path.read_text(encoding="utf-8")
    marker = "def test_structurally_ready_exact_candidate_hands_off_after_one_observation("
    if marker in text:
        raise RuntimeError("P1.5 tests already present")

    text += dedent(
        r'''


def test_structurally_ready_exact_candidate_hands_off_after_one_observation(
    tmp_path: Path,
) -> None:
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
            _task(),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 2
    assert driver.progress_outcomes == [True, False]
    assert result.changed_files == ["src/gomoku_logic.py"]
    assert "Structurally complete candidate" in result.final_message


def test_incomplete_exact_candidate_does_not_handoff_until_all_deliverables_change(
    tmp_path: Path,
) -> None:
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
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 5
    assert driver.progress_outcomes == [True, False, False, True, False]
    assert result.changed_files == ["src/gomoku_ui.js", "src/index.html"]
    assert "Structurally complete candidate" in result.final_message


def test_glob_write_scope_preserves_p14_two_observation_handoff(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-helper",
                        "write_file",
                        {"path": "src/helper.py", "content": "HELPER = True\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-helper-1",
                        "read_range",
                        {"path": "src/helper.py", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-helper-2",
                        "read_range",
                        {"path": "src/helper.py", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    assert driver.progress_outcomes == [True, False, False]
    assert result.changed_files == ["src/helper.py"]
    assert "after 2 consecutive observation-only turns" in result.final_message
'''
    )
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_loop()
    patch_tests()
