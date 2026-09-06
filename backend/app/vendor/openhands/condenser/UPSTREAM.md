# OpenHands Event / View / Condenser source

Upstream repository: `OpenHands/software-agent-sdk`

Pinned commit: `e26683288ab4dd69518810016b74682de2a8c4e4`

License: MIT (see `backend/app/vendor/openhands/LICENSE`).

Vendored exact upstream files:

| DevFlow vendor file | OpenHands source | Upstream blob |
| --- | --- | --- |
| `upstream_base.py` | `openhands-sdk/openhands/sdk/context/condenser/base.py` | `8981e4f705ddcfceae90710c3be6992046d1ba73` |
| `upstream_pipeline.py` | `openhands-sdk/openhands/sdk/context/condenser/pipeline_condenser.py` | `e548a02d26fa4c875d5f63836a80720efe0501fb` |
| `upstream_llm_summarizing.py` | `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py` | `d6d9ae62b368962bbb35f1c89a224a70e75379fb` |
| `upstream_view.py` | `openhands-sdk/openhands/sdk/context/view/view.py` | `ad9838ab6e404b9584ce18d5c360acaa38a2ae87` |
| `upstream_manipulation_indices.py` | `openhands-sdk/openhands/sdk/context/view/manipulation_indices.py` | `f2352aabbc6d508b2b1536a5dd10562f40e608c6` |
| `upstream_event_condenser.py` | `openhands-sdk/openhands/sdk/event/condenser.py` | `724696b847bf678365918678d062c62945111fa8` |

These exact files are retained for provenance and source-level comparison and are not imported
directly because they depend on the full OpenHands SDK event, LLM, observability, and conversation
stack.

DevFlow integration lives in `app.integrations.openhands.condenser`. It preserves the architecture
that matters for Runtime V3:

1. completed tool groups are appended as events;
2. history is never deleted from the in-memory event stream;
3. a `CondensationEvent` records which prior group events are omitted from the provider View;
4. the View applies condensation events in order and inserts a bounded summary;
5. summaries are deterministic DevFlow working-state metadata, not correctness evidence.

DevFlow intentionally does not call OpenHands' separate summarization LLM. Model spend remains
authorized by the existing `BudgetedAgentDriver`, while Git and deterministic verification remain
the code/correctness authorities.
