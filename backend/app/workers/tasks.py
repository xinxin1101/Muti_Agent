from __future__ import annotations

import dramatiq

from app.core.runtime_identity import assert_runtime_fingerprint
from app.core.settings import get_settings
from app.dispatch import create_redis_broker
from app.workers.actor import create_task_actor
from app.workers.runtime import execute_task_from_settings

assert_runtime_fingerprint()
settings = get_settings()
broker = create_redis_broker(
    settings.redis_url,
    namespace=settings.dramatiq_namespace,
)
dramatiq.set_broker(broker)

execute_devflow_task = create_task_actor(
    broker=broker,
    handler=execute_task_from_settings,
    queue_name=settings.dramatiq_queue_name,
    task_time_limit_seconds=settings.worker_task_time_limit_seconds,
)
