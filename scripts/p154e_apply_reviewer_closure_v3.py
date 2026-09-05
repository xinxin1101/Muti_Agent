from __future__ import annotations

from pathlib import Path


patcher_path = Path("/tmp/p154e_apply_reviewer_closure.py")
source = patcher_path.read_text(encoding="utf-8")

helper_anchor = '''def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
'''
helper_replacement = '''def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    print(f"PATCH_ONCE path={path} count={count} anchor={old[:120]!r}", flush=True)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}; anchor={old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_first(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    print(f"PATCH_FIRST path={path} count={count} anchor={old[:120]!r}", flush=True)
    if count < 1:
        raise SystemExit(f"{path}: expected at least one match, found {count}; anchor={old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
'''
if source.count(helper_anchor) != 1:
    raise SystemExit("patcher helper anchor mismatch")
source = source.replace(helper_anchor, helper_replacement, 1)

ambiguous_call = '''replace_once(
    "backend/app/agents/reviewer.py",
    "                    content=self._reviewer_system_prompt(),",
'''
count = source.count(ambiguous_call)
if count != 2:
    raise SystemExit(f"expected two reviewer system-prompt patch sites, found {count}")
source = source.replace(ambiguous_call, ambiguous_call.replace("replace_once", "replace_first"))

exec(compile(source, str(patcher_path), "exec"))
