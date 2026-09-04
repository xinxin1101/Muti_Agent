from pathlib import Path

path = Path("backend/app/agent_runtime/loop.py")
text = path.read_text(encoding="utf-8")
old = "                                    candidate_ready=(candidate_ready if candidate_readiness_known else None),\n"
new = (
    "                                    candidate_ready=(\n"
    "                                        candidate_ready\n"
    "                                        if candidate_readiness_known\n"
    "                                        else None\n"
    "                                    ),\n"
)
if old not in text:
    if new not in text:
        raise SystemExit("remaining candidate_ready formatting anchor not found")
else:
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
