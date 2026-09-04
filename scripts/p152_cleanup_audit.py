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

old_24 = '''                        same_file_mutation_streak=same_file_mutation_streak,\n                        convergence_nudge_triggered=convergence_nudge_triggered,\n                    )\n'''
new_24 = '''                        same_file_mutation_streak=same_file_mutation_streak,\n                        convergence_nudge_triggered=convergence_nudge_triggered,\n                        candidate_readiness_known=candidate_readiness_known,\n                        candidate_ready=(candidate_ready if candidate_readiness_known else None),\n                        missing_required_deliverables=missing_required_paths,\n                        deliverable_progress=deliverable_progress,\n                        deliverable_completion_mode=deliverable_completion_mode,\n                        deliverable_convergence_violations=(\n                            deliverable_convergence_violations\n                        ),\n                    )\n'''
loop = loop.replace(old_24, new_24)

old_36 = '''                                    same_file_mutation_streak=same_file_mutation_streak,\n                                    convergence_nudge_triggered=(convergence_nudge_triggered),\n                                )\n'''
new_36 = '''                                    same_file_mutation_streak=same_file_mutation_streak,\n                                    convergence_nudge_triggered=(convergence_nudge_triggered),\n                                    candidate_readiness_known=candidate_readiness_known,\n                                    candidate_ready=(\n                                        candidate_ready if candidate_readiness_known else None\n                                    ),\n                                    missing_required_deliverables=missing_required_paths,\n                                    deliverable_progress=deliverable_progress,\n                                    deliverable_completion_mode=deliverable_completion_mode,\n                                    deliverable_convergence_violations=(\n                                        deliverable_convergence_violations\n                                    ),\n                                )\n'''
loop = loop.replace(old_36, new_36)

loop_path.write_text(loop, encoding="utf-8")

module = ast.parse(loop)
progress_calls = []
for node in ast.walk(module):
    if not isinstance(node, ast.Call):
        continue
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "record_runtime_progress":
        progress_calls.append(node)
        keywords = {item.arg for item in node.keywords if item.arg is not None}
        required = {
            "candidate_readiness_known",
            "candidate_ready",
            "missing_required_deliverables",
            "deliverable_progress",
            "deliverable_completion_mode",
            "deliverable_convergence_violations",
        }
        missing = required - keywords
        if missing:
            raise SystemExit(
                f"runtime-progress call on line {node.lineno} misses fields: {sorted(missing)}"
            )
if len(progress_calls) < 5:
    raise SystemExit(f"unexpected runtime-progress call count: {len(progress_calls)}")

if loop.count(deliverable_observation_block) != 1:
    raise SystemExit(
        "deliverable observation focus block must appear exactly once; "
        f"found {loop.count(deliverable_observation_block)}"
    )

# Guard the test import/API shape as part of the audit.
test_path = Path("backend/tests/test_developer_mutation_convergence.py")
tests = test_path.read_text(encoding="utf-8")
if "from app.models.trace import TraceSpanKind\n" not in tests:
    raise SystemExit("TraceSpanKind test import is missing")
if "models.TraceSpanKind" in tests:
    raise SystemExit("test still references unexported models.TraceSpanKind")
ast.parse(tests)
