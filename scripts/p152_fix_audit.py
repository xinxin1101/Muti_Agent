from pathlib import Path

loop_path = Path("backend/app/agent_runtime/loop.py")
loop = loop_path.read_text(encoding="utf-8")
old_trace_tail = '''                    same_file_mutation_streak=same_file_mutation_streak,\n                    convergence_nudge_triggered=convergence_nudge_triggered,\n                )\n'''
new_trace_tail = '''                    same_file_mutation_streak=same_file_mutation_streak,\n                    convergence_nudge_triggered=convergence_nudge_triggered,\n                    candidate_readiness_known=candidate_readiness_known,\n                    candidate_ready=(candidate_ready if candidate_readiness_known else None),\n                    missing_required_deliverables=missing_required_paths,\n                    deliverable_progress=deliverable_progress,\n                    deliverable_completion_mode=deliverable_completion_mode,\n                    deliverable_convergence_violations=(\n                        deliverable_convergence_violations\n                    ),\n                )\n'''
replaced = 0
while old_trace_tail in loop:
    loop = loop.replace(old_trace_tail, new_trace_tail, 1)
    replaced += 1
if replaced == 0 and loop.count("missing_required_deliverables=missing_required_paths") < 4:
    raise SystemExit("expected remaining runtime-progress trace call sites")
loop_path.write_text(loop, encoding="utf-8")

test_path = Path("backend/tests/test_developer_mutation_convergence.py")
tests = test_path.read_text(encoding="utf-8")
import_anchor = "from app import agents, models\n"
trace_import = "from app.models.trace import TraceSpanKind\n"
if trace_import not in tests:
    if import_anchor not in tests:
        raise SystemExit("test import anchor not found")
    tests = tests.replace(import_anchor, import_anchor + trace_import, 1)
tests = tests.replace("models.TraceSpanKind.AGENT_TURN", "TraceSpanKind.AGENT_TURN")
test_path.write_text(tests, encoding="utf-8")
