import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.context import ContextPacketBuilder
from app.models import (
    ContextBudget,
    ContextContinuationState,
    ContextFileDigest,
    ContextPacket,
    ContextScopeKind,
    ContextSelectionReason,
    ContextTruncationReason,
    TaskContract,
)
from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
    (root / "tests" / "test_b.py").write_text("def test_b(): pass\n", encoding="utf-8")
    (root / "docs" / "notes.md").write_text("not visible\n", encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow-context@example.com")
    _git(root, "config", "user.name", "DevFlow Context Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> TaskContract:
    return TaskContract(
        task_id="CTX-001",
        objective="Change src/b.py without modifying protected tests.",
        readable_files=["src/**"],
        writable_files=["src/b.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["src/b.py contains the requested implementation."],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=2,
    )


def test_builder_orders_changed_writable_readonly_readable_and_records_provenance(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    (root / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")

    packet = ContextPacketBuilder().build(_task(), workspace=workspace)

    assert packet.repository_head == workspace.head_commit()
    assert packet.changed_files == ["src/b.py"]
    assert packet.repository_map == ["src/a.py", "src/b.py", "tests/test_b.py"]
    assert [item.path for item in packet.selected_files] == [
        "src/b.py",
        "tests/test_b.py",
        "src/a.py",
    ]
    assert "docs/notes.md" not in [item.path for item in packet.selected_files]

    changed = packet.selected_files[0]
    assert changed.changed is True
    assert changed.tracked is True
    assert changed.selection_reasons == [
        ContextSelectionReason.CHANGED,
        ContextSelectionReason.WRITABLE_SCOPE,
        ContextSelectionReason.READABLE_SCOPE,
    ]
    assert [(match.kind, match.pattern) for match in changed.scope_matches] == [
        (ContextScopeKind.WRITABLE, "src/b.py"),
        (ContextScopeKind.READABLE, "src/**"),
    ]
    assert changed.snippets[0].start_line == 1
    assert changed.snippets[0].content == "B = 2\n"
    assert len(changed.source_sha256) == 64
    assert packet.usage.candidate_files == 3
    assert packet.usage.selected_files == 3
    assert packet.token_estimator == "utf8_bytes_upper_bound"


def test_same_state_and_budget_produce_same_fingerprint_content_change_changes_it(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    builder = ContextPacketBuilder()

    first = builder.build(_task(), workspace=workspace)
    second = builder.build(_task(), workspace=workspace)

    assert first == second
    assert first.fingerprint == second.fingerprint

    (root / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    changed = builder.build(_task(), workspace=workspace)

    assert changed.fingerprint != first.fingerprint
    assert changed.changed_files == ["src/a.py"]


def test_resumed_packet_reuses_unchanged_files_and_keeps_checkpoint_changes(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    builder = ContextPacketBuilder()
    first = builder.build(_task(), workspace=workspace)
    resume = ContextContinuationState(
        summary_version=first.repository_summary_version,
        repository_head=first.repository_head,
        read_files=tuple(
            ContextFileDigest(path=item.path, source_sha256=item.source_sha256)
            for item in first.selected_files
        ),
        changed_files=("src/b.py",),
        completed_summary="棋盘逻辑已经实现。",
        remaining_summary="继续完成界面层。",
        verification_summary="尚未验证。",
    )

    resumed = builder.build(_task(), workspace=workspace, resume=resume)

    assert resumed.resume == resume
    assert resumed.changed_files == ["src/b.py"]
    assert [item.path for item in resumed.selected_files] == ["src/b.py"]
    assert resumed.usage.reused_files == 2
    assert resumed.usage.prompt_estimated_tokens >= resumed.usage.estimated_tokens
    assert "repository_head=" in resumed.repository_summary


def test_planning_summary_is_cached_by_project_commit_and_version(tmp_path: Path) -> None:
    from app.api.repository_context import RepositoryPlanningContextBuilder

    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    builder = RepositoryPlanningContextBuilder()
    arguments = {
        "base_commit": workspace.head_commit(),
        "requirement": "Update the Python module.",
        "repository_url": "https://github.com/acme/context",
        "default_branch": "main",
        "project_id": uuid4(),
    }

    first = builder.build(workspace, **arguments)
    second = builder.build(workspace, **arguments)

    assert first == second
    assert builder.cached_summary_count == 1
    assert "repository_summary_version=repository_summary_v1" in first


def test_packet_rejects_tampered_payload_or_detached_fingerprint(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    packet = ContextPacketBuilder().build(_task(), workspace=LocalGitWorkspace(root))

    tampered_payload = packet.model_dump(mode="json")
    tampered_payload["objective"] = "Forged objective that was never fingerprinted."
    with pytest.raises(ValidationError, match="fingerprint"):
        ContextPacket.model_validate(tampered_payload)

    detached_fingerprint = packet.model_dump(mode="json")
    detached_fingerprint["fingerprint"] = "0" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        ContextPacket.model_validate(detached_fingerprint)


def test_builder_records_per_file_and_file_count_truncation(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "src" / "b.py").write_text("x" * 500, encoding="utf-8")
    _git(root, "add", "src/b.py")
    _git(root, "commit", "-m", "long writable file")
    workspace = LocalGitWorkspace(root)
    budget = ContextBudget(
        max_files=1,
        max_chars_per_file=100,
        max_total_chars=100,
        max_estimated_tokens=100,
        max_source_file_bytes=1_000_000,
    )

    packet = ContextPacketBuilder(budget=budget).build(_task(), workspace=workspace)

    assert len(packet.selected_files) == 1
    assert packet.selected_files[0].path == "src/b.py"
    assert packet.selected_files[0].selected_chars == 100
    assert packet.selected_files[0].truncated is True
    reasons = [item.reason for item in packet.truncations]
    assert ContextTruncationReason.PER_FILE_CHAR_LIMIT in reasons
    assert ContextTruncationReason.FILE_COUNT_LIMIT in reasons
    assert packet.usage.truncated_files == 1
    assert packet.usage.omitted_files == 2


def test_utf8_byte_estimator_enforces_conservative_token_budget(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    text = "中" * 100
    (root / "src" / "b.py").write_text(text, encoding="utf-8")
    _git(root, "add", "src/b.py")
    _git(root, "commit", "-m", "unicode source")
    workspace = LocalGitWorkspace(root)
    budget = ContextBudget(
        max_files=1,
        max_chars_per_file=100,
        max_total_chars=100,
        max_estimated_tokens=100,
        max_source_file_bytes=1_000_000,
    )

    packet = ContextPacketBuilder(budget=budget).build(_task(), workspace=workspace)
    selected = packet.selected_files[0]

    assert selected.path == "src/b.py"
    assert selected.selected_chars == 33
    assert selected.estimated_tokens == 99
    assert selected.snippets[0].content == "中" * 33
    assert any(
        item.reason is ContextTruncationReason.TOKEN_BUDGET and item.path == "src/b.py"
        for item in packet.truncations
    )


def test_non_utf8_visible_file_is_omitted_with_explicit_evidence(tmp_path: Path) -> None:
    root = tmp_path / "binary-repo"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"\xff\xfe\xfd")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow-context@example.com")
    _git(root, "config", "user.name", "DevFlow Context Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "binary baseline")
    workspace = LocalGitWorkspace(root)
    task = TaskContract(
        task_id="CTX-BINARY",
        objective="Inspect a binary path without exposing it as model text.",
        readable_files=[],
        writable_files=["payload.bin"],
        readonly_files=[],
        acceptance_criteria=["Binary data is omitted from model context."],
        verification_commands=["pytest -q"],
    )

    packet = ContextPacketBuilder().build(task, workspace=workspace)

    assert packet.selected_files == []
    assert packet.usage.candidate_files == 1
    assert packet.usage.omitted_files == 1
    assert packet.truncations[0].reason is ContextTruncationReason.NON_UTF8
    assert packet.truncations[0].path == "payload.bin"


def test_source_file_size_limit_fails_closed_per_file_without_reading_as_text(
    tmp_path: Path,
) -> None:
    root = tmp_path / "large-repo"
    root.mkdir()
    (root / "large.txt").write_text("a" * 2048, encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow-context@example.com")
    _git(root, "config", "user.name", "DevFlow Context Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "large baseline")
    workspace = LocalGitWorkspace(root)
    task = TaskContract(
        task_id="CTX-LARGE",
        objective="Bound source-file inspection.",
        readable_files=[],
        writable_files=["large.txt"],
        readonly_files=[],
        acceptance_criteria=["Oversized source is omitted."],
        verification_commands=["pytest -q"],
    )
    budget = ContextBudget(max_source_file_bytes=1024)

    packet = ContextPacketBuilder(budget=budget).build(task, workspace=workspace)

    assert packet.selected_files == []
    assert packet.usage.omitted_files == 1
    assert packet.truncations[0].reason is ContextTruncationReason.SOURCE_FILE_TOO_LARGE
