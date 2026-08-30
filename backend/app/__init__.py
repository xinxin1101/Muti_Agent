"""DevFlow backend package initialization."""

import asyncio
import sys

# psycopg's asynchronous implementation does not support the Proactor loop
# selected by default on Windows. Configure the policy before any API, worker,
# CLI, or test entry point creates an event loop. Non-Windows platforms retain
# their native default policy.
if sys.platform == "win32":
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())
