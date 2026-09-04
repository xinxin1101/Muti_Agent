from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing test migration anchor: {label}")
    return text.replace(old, new, 1)


path = Path("backend/tests/test_developer_mutation_convergence.py")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''    assert [request.budget_progress for request in driver.requests] == [\n        False,\n        False,\n        True,\n        False,\n    ]\n    assert [request.liveness_credit for request in driver.requests] == [\n        models.LivenessCredit.INITIAL_STARTUP,\n        models.LivenessCredit.NORMAL,\n        models.LivenessCredit.VERIFIED_PROGRESS,\n        models.LivenessCredit.NORMAL,\n    ]\n''',
    '''    assert [request.budget_progress for request in driver.requests] == [\n        False,\n        False,\n        True,\n    ]\n    assert [request.liveness_credit for request in driver.requests] == [\n        models.LivenessCredit.INITIAL_STARTUP,\n        models.LivenessCredit.NORMAL,\n        models.LivenessCredit.VERIFIED_PROGRESS,\n    ]\n''',
    label="previous-turn progress credit request count",
)

text = replace_once(
    text,
    '''    assert len(driver.requests) == 3\n    assert driver.progress_outcomes == [True, False, False]\n    assert "deterministic verification" in result.final_message\n''',
    '''    assert len(driver.requests) == 2\n    assert driver.progress_outcomes == [True, False]\n    assert "deterministic verification" in result.final_message\n''',
    label="ready candidate handoff request count",
)

text = replace_once(
    text,
    '''        if span.agent_role is models.AgentRole.DEVELOPER and span.iteration == 3\n''',
    '''        if span.agent_role is models.AgentRole.DEVELOPER and span.iteration == 2\n''',
    label="terminal handoff trace iteration",
)

path.write_text(text, encoding="utf-8")
