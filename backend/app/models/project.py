from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse, urlunparse


class ProjectProvisionStatus(StrEnum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


def canonical_repository_url(value: str) -> str:
    """Canonicalize credential-free HTTPS repository identity without changing path case."""

    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("repository_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("repository_url must not embed credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("repository_url contains an invalid port") from exc
    hostname = parsed.hostname.lower()
    if port not in {None, 443}:
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    path = parsed.path.rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path or path == "/":
        raise ValueError("repository_url must include a repository path")
    return urlunparse(("https", netloc, path, "", "", ""))
