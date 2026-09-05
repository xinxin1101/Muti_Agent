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

packet_call = 'replace_once("backend/app/agents/reviewer.py", old_packet_return, new_packet_return)'
packet_replacement = '''reviewer_path = Path("backend/app/agents/reviewer.py")
reviewer_text = reviewer_path.read_text(encoding="utf-8")
packet_start = (
    '        return (\\n'
    '            "Review the implementation against the validated task using only the evidence "'
)
packet_end = "\\n\\n    def _reviewer_system_prompt"
start_count = reviewer_text.count(packet_start)
end_count = reviewer_text.count(packet_end)
print(
    f"PATCH_PACKET_BOUNDARY start_count={start_count} end_count={end_count}",
    flush=True,
)
if start_count != 1 or end_count != 1:
    raise SystemExit(
        f"review packet boundary mismatch: start={start_count};end={end_count}"
    )
start_index = reviewer_text.index(packet_start)
end_index = reviewer_text.index(packet_end, start_index)
reviewer_path.write_text(
    reviewer_text[:start_index] + new_packet_return + reviewer_text[end_index:],
    encoding="utf-8",
)'''
if source.count(packet_call) != 1:
    raise SystemExit(f"expected one packet replacement call, found {source.count(packet_call)}")
source = source.replace(packet_call, packet_replacement, 1)

exec(compile(source, str(patcher_path), "exec"))
