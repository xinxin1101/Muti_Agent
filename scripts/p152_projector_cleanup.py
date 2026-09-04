import ast
from pathlib import Path

path = Path("backend/app/trace/projector.py")
text = path.read_text(encoding="utf-8")
block = '''                    candidate_readiness_known=item.candidate_readiness_known,\n                    candidate_ready=item.candidate_ready,\n                    missing_required_deliverables=(\n                        item.missing_required_deliverables\n                    ),\n                    deliverable_progress=item.deliverable_progress,\n                    deliverable_completion_mode=item.deliverable_completion_mode,\n                    deliverable_convergence_violations=(\n                        item.deliverable_convergence_violations\n                    ),\n'''
while block + block in text:
    text = text.replace(block + block, block, 1)

# Import-time syntax must be valid, and the trace-batch projection block must be unique.
ast.parse(text)
if text.count(block) != 1:
    raise SystemExit(f"expected one trace-batch deliverable projection block, found {text.count(block)}")

path.write_text(text, encoding="utf-8")
