from __future__ import annotations

import re

from dramatiq.brokers.redis import RedisBroker
from pydantic import SecretStr

from app.dispatch.errors import TaskDispatchError

_ALLOWED_REDIS_SCHEMES = ("redis://", "rediss://")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def reveal_redis_url(value: SecretStr | str) -> str:
    url = value.get_secret_value() if isinstance(value, SecretStr) else value
    normalized = url.strip()
    if not normalized:
        raise TaskDispatchError("Redis URL must not be empty")
    if not normalized.startswith(_ALLOWED_REDIS_SCHEMES):
        raise TaskDispatchError("Redis broker URL must use redis:// or rediss://")
    return normalized


def create_redis_broker(
    redis_url: SecretStr | str,
    *,
    namespace: str,
) -> RedisBroker:
    normalized_namespace = namespace.strip()
    if _NAMESPACE_RE.fullmatch(normalized_namespace) is None:
        raise TaskDispatchError(
            "Dramatiq Redis namespace must be 1-64 characters using letters, digits, "
            "'.', '_' or '-'"
        )
    return RedisBroker(
        url=reveal_redis_url(redis_url),
        namespace=normalized_namespace,
    )
