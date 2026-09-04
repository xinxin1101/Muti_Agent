from pathlib import Path

loop_path = Path("backend/app/agent_runtime/loop.py")
loop = loop_path.read_text(encoding="utf-8")
loop = loop.replace(
    "candidate_ready=(candidate_ready if candidate_readiness_known else None),",
    "candidate_ready=(\n"
    "                                        candidate_ready\n"
    "                                        if candidate_readiness_known\n"
    "                                        else None\n"
    "                                    ),",
    1,
)
loop = loop.replace(
    "candidate_ready=(candidate_ready if candidate_readiness_known else None),",
    "candidate_ready=(\n"
    "                                    candidate_ready\n"
    "                                    if candidate_readiness_known\n"
    "                                    else None\n"
    "                                ),",
    1,
)
loop_path.write_text(loop, encoding="utf-8")


test_path = Path("backend/tests/test_developer_mutation_convergence.py")
tests = test_path.read_text(encoding="utf-8")
marker = "def test_deliverable_completion_mode_focuses_missing_exact_path"
start = tests.find(marker)
if start == -1:
    raise SystemExit("P1.5.2 test marker not found")

formatted_tail = '''def test_deliverable_completion_mode_focuses_missing_exact_path(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\\n"},
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
    assert len(driver.requests) == 3
    completion_prompt = _prompt(driver.requests[2])
    assert "DELIVERABLE COMPLETION MODE" in completion_prompt
    assert "src/index.html" in completion_prompt
    assert "src/gomoku_ui.js" in completion_prompt
    assert "immediately after the mutation turn" in result.final_message


def test_deliverable_completion_mode_allows_one_bounded_correction(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-3",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v3</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\\n"},
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
    assert len(driver.requests) == 4
    assert "FINAL BOUNDED CHANCE" in _prompt(driver.requests[3])
    assert "src/gomoku_ui.js" in _prompt(driver.requests[3])
    assert result.changed_files == ["src/gomoku_ui.js", "src/index.html"]


def test_deliverable_completion_gate_stops_second_unproductive_rewrite(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-3",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v3</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-4",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v4</main>\\n"},
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

    assert result.stop_reason is models.DeveloperStopReason.NO_PROGRESS
    assert len(driver.requests) == 4
    assert driver.progress_outcomes == [True, True, True, True]
    assert "src/gomoku_ui.js" in result.final_message
    assert result.changed_files == ["src/index.html"]


def test_deliverable_completion_trace_records_structural_progress(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\\n"},
                    )
                ]
            ),
        ]
    )
    trace = TaskTraceCollector(
        run_id=uuid4(),
        task_id="developer-convergence",
        dispatch_id=uuid4(),
        generation=1,
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
            trace=trace,
        )
    )
    turns = {
        span.iteration: span
        for span in trace.batch().spans
        if span.agent_role is models.AgentRole.DEVELOPER
        and span.kind is TraceSpanKind.AGENT_TURN
    }

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert turns[2].candidate_readiness_known is True
    assert turns[2].candidate_ready is False
    assert turns[2].missing_required_deliverables == ("src/gomoku_ui.js",)
    assert turns[2].deliverable_progress is False
    assert turns[2].deliverable_completion_mode is True
    assert turns[2].deliverable_convergence_violations == 0
    assert turns[3].candidate_ready is True
    assert turns[3].missing_required_deliverables == ()
    assert turns[3].deliverable_progress is True
    assert turns[3].deliverable_completion_mode is False
'''

test_path.write_text(tests[:start] + formatted_tail, encoding="utf-8")
