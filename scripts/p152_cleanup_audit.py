import ast
from pathlib import Path

loop_path = Path("backend/app/agent_runtime/loop.py")
loop = loop_path.read_text(encoding="utf-8")

deliverable_observation_block = '''            if (\n                policy.deliverable_convergence_enabled\n                and deliverable_completion_mode\n                and candidate_readiness_known\n                and missing_required_paths\n                and not turn_made_progress\n            ):\n                runtime_instruction = self._deliverable_completion_prompt(\n                    strict=deliverable_convergence_violations > 0,\n                    completed_paths=completed_required_paths,\n                    missing_paths=missing_required_paths,\n                    repeated_files=deliverable_focus_files,\n                )\n                convergence_nudge_triggered = True\n\n'''
while deliverable_observation_block + deliverable_observation_block in loop:
    loop = loop.replace(
        deliverable_observation_block + deliverable_observation_block,
        deliverable_observation_block,
        1,
    )

required_trace_fields = {
    "candidate_readiness_known",
    "candidate_ready",
    "missing_required_deliverables",
    "deliverable_progress",
    "deliverable_completion_mode",
    "deliverable_convergence_violations",
}


def runtime_progress_calls(source: str) -> list[ast.Call]:
    module = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "record_runtime_progress":
            calls.append(node)
    return calls


# Insert missing structural trace keywords by AST source locations instead of indentation guesses.
lines = loop.splitlines(keepends=True)
insertions: list[tuple[int, str]] = []
for call in runtime_progress_calls(loop):
    keywords = {item.arg for item in call.keywords if item.arg is not None}
    missing = required_trace_fields - keywords
    if not missing:
        continue
    if call.end_lineno is None:
        raise SystemExit("runtime-progress call has no end line")
    closing_index = call.end_lineno - 1
    closing_line = lines[closing_index]
    closing_indent = closing_line[: len(closing_line) - len(closing_line.lstrip())]
    keyword_indent = closing_indent + "    "
    insertion = (
        f"{keyword_indent}candidate_readiness_known=candidate_readiness_known,\n"
        f"{keyword_indent}candidate_ready=(candidate_ready if candidate_readiness_known else None),\n"
        f"{keyword_indent}missing_required_deliverables=missing_required_paths,\n"
        f"{keyword_indent}deliverable_progress=deliverable_progress,\n"
        f"{keyword_indent}deliverable_completion_mode=deliverable_completion_mode,\n"
        f"{keyword_indent}deliverable_convergence_violations=(\n"
        f"{keyword_indent}    deliverable_convergence_violations\n"
        f"{keyword_indent}),\n"
    )
    insertions.append((closing_index, insertion))

for closing_index, insertion in sorted(insertions, reverse=True):
    lines[closing_index:closing_index] = [insertion]
loop = "".join(lines)
loop_path.write_text(loop, encoding="utf-8")

progress_calls = runtime_progress_calls(loop)
for call in progress_calls:
    keywords = {item.arg for item in call.keywords if item.arg is not None}
    missing = required_trace_fields - keywords
    if missing:
        raise SystemExit(
            f"runtime-progress call on line {call.lineno} misses fields: {sorted(missing)}"
        )
if len(progress_calls) < 5:
    raise SystemExit(f"unexpected runtime-progress call count: {len(progress_calls)}")

if loop.count(deliverable_observation_block) != 1:
    raise SystemExit(
        "deliverable observation focus block must appear exactly once; "
        f"found {loop.count(deliverable_observation_block)}"
    )

# Guard the new convergence state machine itself.
if loop.count('detail="deliverable_completion_mode_entered"') != 1:
    raise SystemExit("completion-mode entry event must appear exactly once")
if loop.count('detail="deliverable_completion_gate_exhausted"') != 1:
    raise SystemExit("completion-gate exhaustion event must appear exactly once")

# Guard the test import/API shape as part of the audit.
test_path = Path("backend/tests/test_developer_mutation_convergence.py")
tests = test_path.read_text(encoding="utf-8")
if "from app.models.trace import TraceSpanKind\n" not in tests:
    raise SystemExit("TraceSpanKind test import is missing")
if "models.TraceSpanKind" in tests:
    raise SystemExit("test still references unexported models.TraceSpanKind")
ast.parse(tests)
