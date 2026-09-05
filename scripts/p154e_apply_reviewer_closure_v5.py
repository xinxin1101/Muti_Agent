from __future__ import annotations

from pathlib import Path


v4_path = Path("/tmp/p154e_apply_reviewer_closure_v4.py")
exec(compile(v4_path.read_text(encoding="utf-8"), str(v4_path), "exec"))

review_path = Path("backend/app/models/review.py")
review_path.write_text(
    review_path.read_text(encoding="utf-8").rstrip() + "\n",
    encoding="utf-8",
)
