import json
from pathlib import Path

from app import cli, models
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState


def _task_payload() -> dict:
    return {
        "task_id": "CLI-001",
        "objective": "Update one file.",
        "readable_files": ["**"],
        "writable_files": ["module.py"],
        "readonly_files": ["tests/**"],
        "acceptance_criteria": ["The change is verified."],
        "verification_commands": ["pytest -q"],
        "max_retries": 1,
    }


def test_load_task_validates_json_contract(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_task_payload()), encoding="utf-8")

    task = cli.load_task(task_path)

    assert task.task_id == "CLI-001"
    assert task.writable_files == ["module.py"]


def test_cli_run_prints_terminal_json_and_exit_zero(monkeypatch, capsys, tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(_task_payload()), encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    async def fake_run_single_task(*, workspace_path: Path, task_path: Path):
        assert workspace_path == workspace
        assert task_path.name == "task.json"
        return SingleTaskRunResult(
            task_id="CLI-001",
            status=TaskRunState.SUCCEEDED,
            events=[
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="created"),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="passed"),
            ],
            changed_files=["module.py"],
            agent_models={models.AgentRole.DEVELOPER: "fake/developer"},
        )

    monkeypatch.setattr(cli, "run_single_task", fake_run_single_task)

    exit_code = cli.main(
        ["run", "--workspace", str(workspace), "--task", str(task_path)]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "SUCCEEDED"
    assert output["changed_files"] == ["module.py"]


def test_cli_invalid_task_returns_configuration_error(capsys, tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    task_path.write_text("{not-json", encoding="utf-8")

    exit_code = cli.main(
        ["run", "--workspace", str(tmp_path), "--task", str(task_path)]
    )
    error = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert "error" in error
