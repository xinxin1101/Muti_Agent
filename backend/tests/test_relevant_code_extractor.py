from app.context.relevance import (
    RelevanceCandidate,
    RelevantCodeExtractor,
    RelevantRegionKind,
)
from app.models import ContextScopeKind, TaskContract


def _task() -> TaskContract:
    return TaskContract(
        task_id="REL-001",
        objective="Update create_user validation and preserve User model behavior.",
        readable_files=["src/**", "app/**", "tests/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["create_user validates the User name before persistence."],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=2,
    )


def _candidate(
    path: str,
    *scope_kinds: ContextScopeKind,
    changed: bool = False,
) -> RelevanceCandidate:
    return RelevanceCandidate(
        path=path,
        changed=changed,
        scope_kinds=tuple(scope_kinds),
    )


def test_ast_selector_prefers_task_symbol_and_keeps_import_preamble() -> None:
    sources = {
        "src/service.py": (
            "from app.models.user import User\n"
            "\n"
            "def unrelated_helper():\n"
            "    return 1\n"
            "\n"
            "def create_user(name):\n"
            "    return User(name=name)\n"
        ),
    }
    candidates = [
        _candidate(
            "src/service.py",
            ContextScopeKind.WRITABLE,
            ContextScopeKind.READABLE,
        )
    ]

    selection = RelevantCodeExtractor().select(
        _task(),
        candidates,
        load_source=sources.get,
    )[0]

    assert selection.path == "src/service.py"
    assert selection.python_ast_indexed is True
    assert selection.regions[0].kind is RelevantRegionKind.MODULE_PREAMBLE
    assert selection.regions[0].start_line == 1
    assert selection.regions[0].end_line == 1
    assert any(
        region.symbol == "create_user"
        and region.start_line == 6
        and "task_terms=create,user" in region.evidence
        for region in selection.regions
    )
    assert all(region.symbol != "unrelated_helper" for region in selection.regions)


def test_local_import_dependency_is_ranked_above_unrelated_readable_file() -> None:
    sources = {
        "src/service.py": (
            "from app.models.user import User\n"
            "\n"
            "def create_user(name):\n"
            "    return User(name=name)\n"
        ),
        "app/models/user.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "class AuditRecord:\n"
            "    pass\n"
        ),
        "app/utils/logging.py": "def log_event(message):\n    return message\n",
    }
    candidates = [
        _candidate(
            "src/service.py",
            ContextScopeKind.WRITABLE,
            ContextScopeKind.READABLE,
        ),
        _candidate("app/models/user.py", ContextScopeKind.READABLE),
        _candidate("app/utils/logging.py", ContextScopeKind.READABLE),
    ]

    selections = RelevantCodeExtractor().select(
        _task(),
        candidates,
        load_source=sources.get,
    )

    paths = [selection.path for selection in selections]
    assert paths.index("app/models/user.py") < paths.index("app/utils/logging.py")

    model_selection = next(
        selection for selection in selections if selection.path == "app/models/user.py"
    )
    assert "src/service.py" in next(
        evidence.split("=", 1)[1]
        for evidence in model_selection.evidence
        if evidence.startswith("local_import_from=")
    )
    assert any(
        region.symbol == "User" and "import_terms=user" in region.evidence
        for region in model_selection.regions
    )
    assert all(region.symbol != "AuditRecord" for region in model_selection.regions)


def test_relative_import_resolves_to_visible_local_module() -> None:
    sources = {
        "pkg/service.py": (
            "from .models import User\n\ndef create_user(name):\n    return User(name)\n"
        ),
        "pkg/models.py": "class User:\n    pass\n",
    }
    task = TaskContract(
        task_id="REL-RELATIVE",
        objective="Update create_user User handling.",
        readable_files=["pkg/**"],
        writable_files=["pkg/service.py"],
        readonly_files=[],
        acceptance_criteria=["User remains compatible."],
        verification_commands=["pytest -q"],
    )
    candidates = [
        _candidate(
            "pkg/service.py",
            ContextScopeKind.WRITABLE,
            ContextScopeKind.READABLE,
        ),
        _candidate("pkg/models.py", ContextScopeKind.READABLE),
    ]

    selections = RelevantCodeExtractor().select(
        task,
        candidates,
        load_source=sources.get,
    )
    service = next(selection for selection in selections if selection.path == "pkg/service.py")

    assert service.local_dependencies == ("pkg/models.py",)
    model = next(selection for selection in selections if selection.path == "pkg/models.py")
    assert any(region.symbol == "User" for region in model.regions)


def test_ambiguous_import_suffix_does_not_guess_dependency() -> None:
    sources = {
        "src/service.py": ("import user\n\ndef create_user():\n    return user.User()\n"),
        "pkg/user.py": "class User:\n    pass\n",
        "other/user.py": "class User:\n    pass\n",
    }
    task = TaskContract(
        task_id="REL-AMBIGUOUS",
        objective="Update create_user without guessing which user module is local.",
        readable_files=["src/**", "pkg/**", "other/**"],
        writable_files=["src/service.py"],
        readonly_files=[],
        acceptance_criteria=["Ambiguous local module suffixes do not create guessed edges."],
        verification_commands=["pytest -q"],
    )
    candidates = [
        _candidate("src/service.py", ContextScopeKind.WRITABLE),
        _candidate("pkg/user.py", ContextScopeKind.READABLE),
        _candidate("other/user.py", ContextScopeKind.READABLE),
    ]

    selections = RelevantCodeExtractor().select(
        task,
        candidates,
        load_source=sources.get,
    )
    service = next(selection for selection in selections if selection.path == "src/service.py")

    assert service.local_dependencies == ()
    for selection in selections:
        assert all(not evidence.startswith("local_import_from=") for evidence in selection.evidence)


def test_invalid_python_and_non_python_files_fall_back_without_ast_regions() -> None:
    sources = {
        "src/service.py": "def broken(:\n",
        "README.md": "create_user documentation\n",
    }
    candidates = [
        _candidate("src/service.py", ContextScopeKind.WRITABLE),
        _candidate("README.md", ContextScopeKind.READABLE),
    ]

    selections = RelevantCodeExtractor().select(
        _task(),
        candidates,
        load_source=sources.get,
    )
    by_path = {selection.path: selection for selection in selections}

    assert by_path["src/service.py"].python_ast_indexed is False
    assert by_path["src/service.py"].regions == ()
    assert by_path["README.md"].python_ast_indexed is False
    assert by_path["README.md"].regions == ()


def test_selection_is_deterministic_for_same_task_and_sources() -> None:
    sources = {
        "src/service.py": (
            "from app.models.user import User\n"
            "\n"
            "def create_user(name):\n"
            "    return User(name=name)\n"
        ),
        "app/models/user.py": "class User:\n    pass\n",
    }
    candidates = [
        _candidate(
            "src/service.py",
            ContextScopeKind.WRITABLE,
            ContextScopeKind.READABLE,
            changed=True,
        ),
        _candidate("app/models/user.py", ContextScopeKind.READABLE),
    ]
    extractor = RelevantCodeExtractor()

    first = extractor.select(_task(), candidates, load_source=sources.get)
    second = extractor.select(_task(), candidates, load_source=sources.get)

    assert first == second


def test_index_file_bound_is_deterministic_and_keeps_writable_seed_first() -> None:
    sources = {
        "src/service.py": "def create_user(name):\n    return name\n",
        "app/a.py": "def alpha():\n    pass\n",
        "app/b.py": "def beta():\n    pass\n",
    }
    candidates = [
        _candidate("app/a.py", ContextScopeKind.READABLE),
        _candidate("app/b.py", ContextScopeKind.READABLE),
        _candidate("src/service.py", ContextScopeKind.WRITABLE),
    ]

    selections = RelevantCodeExtractor(max_index_files=1).select(
        _task(),
        candidates,
        load_source=sources.get,
    )
    by_path = {selection.path: selection for selection in selections}

    assert by_path["src/service.py"].python_ast_indexed is True
    assert by_path["app/a.py"].python_ast_indexed is False
    assert by_path["app/b.py"].python_ast_indexed is False
