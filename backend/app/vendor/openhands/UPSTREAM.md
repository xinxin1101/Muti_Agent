# OpenHands vendored source

- Upstream repository: https://github.com/OpenHands/software-agent-sdk
- Pinned upstream commit: `e26683288ab4dd69518810016b74682de2a8c4e4`
- Imported file: `openhands-tools/openhands/tools/apply_patch/core.py`
- Upstream blob SHA: `dcaf6da3a91225c848e18f67608df4fc031631e1`
- License: MIT (see `LICENSE` in this directory)

The vendored `core.py` is kept as the upstream parsing/application algorithm. DevFlow does not
use the OpenHands tool executor directly. `app.integrations.openhands.patch.OpenHandsPatchAdapter`
injects DevFlow-owned filesystem callbacks so ScopeEnforcer, workspace boundaries, file-size
limits, and mutation authority remain local to DevFlow.

The upstream file itself notes that its implementation is adapted from the OpenAI Cookbook
`apply_patch.py` reference. That provenance is intentionally preserved in the vendored source.
