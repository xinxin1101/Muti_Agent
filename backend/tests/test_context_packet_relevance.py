import subprocess
from pathlib import Path

from app.context import ContextPacketBuilder
from app.models import ContextBudget, ContextTruncationReason, TaskContract
from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow-relevance@example.com")
    _git(root, "config", "user.name", "DevFlow Relevance Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")


def test_builder_uses_imported_symbol_regions_before_unrelated_readonly_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "app" / "models").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "service.py").write_text(
        "from app.models.user import User\n"
        "\n"
        "def unrelated_helper():\n"
        "    return 1\n"
        "\n"
        "def create_user(name):\n"
        "    return User(name=name)\n",
        encoding="utf-8",
    )
    (root / "app" / "models" / "user.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "class AuditRecord:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    _init_repo(root)
    task = TaskContract(
        task_id="CTX-REL-001",
        objective="Update create_user validation while preserving the User model.",
        readable_files=["src/**", "app/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["create_user validates User names."],
        verification_commands=["pytest -q"],
    )
    budget = ContextBudget(max_files=2)

    packet = ContextPacketBuilder(budget=budget).build(
        task,
        workspace=LocalGitWorkspace(root),
    )

    assert [item.path for item in packet.selected_files] == [
        "src/service.py",
        "app/models/user.py",
    ]
    service_text = "\n".join(snippet.content for snippet in packet.selected_files[0].snippets)
    model_text = "\n".join(snippet.content for snippet in packet.selected_files[1].snippets)
    assert "from app.models.user import User" in service_text
    assert "def create_user" in service_text
    assert "unrelated_helper" not in service_text
    assert "class User" in model_text
    assert "AuditRecord" not in model_text
    assert packet.selection_strategy.startswith("python_ast_import_relevance_v1")
    assert packet.snippet_strategy == ("python_ast_symbol_regions_v1+deterministic_prefix_fallback")
    assert packet.usage.omitted_files == 1
    assert packet.truncations[-1].reason is ContextTruncationReason.FILE_COUNT_LIMIT


def test_invalid_python_falls_back_to_deterministic_prefix(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    broken = "def broken(:\n    pass\n"
    (root / "broken.py").write_text(broken, encoding="utf-8")
    _init_repo(root)
    task = TaskContract(
        task_id="CTX-REL-BROKEN",
        objective="Repair broken parser code.",
        readable_files=[],
        writable_files=["broken.py"],
        readonly_files=[],
        acceptance_criteria=["broken.py remains available as bounded context."],
        verification_commands=["pytest -q"],
    )

    packet = ContextPacketBuilder().build(task, workspace=LocalGitWorkspace(root))

    assert len(packet.selected_files) == 1
    assert packet.selected_files[0].snippets[0].start_line == 1
    assert packet.selected_files[0].snippets[0].content == broken
    assert packet.selected_files[0].truncated is False


def test_ast_prescan_does_not_bypass_source_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "large.py").write_text(
        "def relevant_symbol():\n" + "    value = 1\n" * 300,
        encoding="utf-8",
    )
    _init_repo(root)
    task = TaskContract(
        task_id="CTX-REL-LARGE",
        objective="Update relevant_symbol.",
        readable_files=[],
        writable_files=["large.py"],
        readonly_files=[],
        acceptance_criteria=["Oversized Python source is not exposed to AST selection."],
        verification_commands=["pytest -q"],
    )
    budget = ContextBudget(max_source_file_bytes=1024)

    packet = ContextPacketBuilder(budget=budget).build(
        task,
        workspace=LocalGitWorkspace(root),
    )

    assert packet.selected_files == []
    assert packet.usage.omitted_files == 1
    assert packet.truncations[0].reason is ContextTruncationReason.SOURCE_FILE_TOO_LARGE
    assert packet.truncations[0].path == "large.py"


def test_import_target_outside_task_visible_scope_is_not_added(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "src" / "service.py").write_text(
        "from private.secret import Secret\n\ndef create_user():\n    return Secret()\n",
        encoding="utf-8",
    )
    (root / "private" / "secret.py").write_text(
        "class Secret:\n    pass\n",
        encoding="utf-8",
    )
    _init_repo(root)
    task = TaskContract(
        task_id="CTX-REL-SCOPE",
        objective="Update create_user without exposing private code.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=[],
        acceptance_criteria=["Private modules stay outside context."],
        verification_commands=["pytest -q"],
    )

    packet = ContextPacketBuilder().build(task, workspace=LocalGitWorkspace(root))

    assert [item.path for item in packet.selected_files] == ["src/service.py"]
    assert all(item.path != "private/secret.py" for item in packet.selected_files)


def test_ast_region_budget_records_existing_truncation_types(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    body = "\n".join(f"    value_{index} = {index}" for index in range(80))
    (root / "service.py").write_text(
        f"def create_user(name):\n{body}\n    return name\n",
        encoding="utf-8",
    )
    _init_repo(root)
    task = TaskContract(
        task_id="CTX-REL-BUDGET",
        objective="Update create_user.",
        readable_files=[],
        writable_files=["service.py"],
        readonly_files=[],
        acceptance_criteria=["create_user remains bounded by existing packet budgets."],
        verification_commands=["pytest -q"],
    )
    budget = ContextBudget(
        max_files=1,
        max_chars_per_file=100,
        max_total_chars=100,
        max_estimated_tokens=100,
        max_source_file_bytes=1_000_000,
    )

    packet = ContextPacketBuilder(budget=budget).build(
        task,
        workspace=LocalGitWorkspace(root),
    )

    selected = packet.selected_files[0]
    assert selected.path == "service.py"
    assert selected.truncated is True
    assert selected.selected_chars == 100
    reasons = {item.reason for item in packet.truncations}
    assert ContextTruncationReason.PER_FILE_CHAR_LIMIT in reasons
    assert ContextTruncationReason.TOTAL_CHAR_LIMIT in reasons
    assert ContextTruncationReason.TOKEN_BUDGET in reasons
