# OpenHands stuck detector source

- Upstream repository: https://github.com/OpenHands/software-agent-sdk
- Pinned upstream commit: `e26683288ab4dd69518810016b74682de2a8c4e4`
- Imported source: `openhands-sdk/openhands/sdk/conversation/stuck_detector.py`
- Upstream blob SHA: `3d685e4c9358a34f59aa3f44db2bc84daf6744fd`
- License: MIT (in `backend/app/vendor/openhands/LICENSE`)

`upstream.py` is the exact pinned source for provenance and review. It is not imported directly
because it depends on the full OpenHands ConversationState/Event stack.

`core.py` is the standalone DevFlow adaptation of the same bounded-window algorithm and defaults:
action-observation=4, action-error=3, monologue=3, alternating-pattern=6, recent event window=20.
It replaces OpenHands SDK event payloads with small signature-bearing records. DevFlow's adapter
hashes raw tool arguments/results before adding them to this detector, so repository source remains
outside the stuck-detection state.
