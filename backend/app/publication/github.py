from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr, ValidationError

from app.models.publication import GitHubPublicationIntent, GitHubRemotePullRequest
from app.workspace import LocalGitWorkspace

_GITHUB_API_VERSION = "2026-03-10"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class GitHubPublicationGatewayError(RuntimeError):
    """Bounded external-publication failure safe to persist and expose."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def parse_github_repository_url(repository_url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` only for credential-free github.com HTTPS repositories."""

    normalized = repository_url.strip()
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "github.com"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        raise GitHubPublicationGatewayError(
            "UNSUPPORTED_REPOSITORY",
            "GitHub publication requires a credential-free https://github.com/owner/repo URL.",
        )

    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) != 2:
        raise GitHubPublicationGatewayError(
            "UNSUPPORTED_REPOSITORY",
            "GitHub publication requires an owner/repository path.",
        )
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo or _NAME_RE.fullmatch(owner) is None or _NAME_RE.fullmatch(repo) is None:
        raise GitHubPublicationGatewayError(
            "UNSUPPORTED_REPOSITORY",
            "GitHub publication repository identity is invalid.",
        )
    return owner, repo


class GitHubPublicationGateway:
    """Publish one validated local commit to a DevFlow branch and Draft Pull Request.

    The gateway never chooses the source commit, base branch, or remote branch name. Those are
    already frozen in ``GitHubPublicationIntent`` by the backend evidence resolver.
    """

    def __init__(
        self,
        token: SecretStr,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("GitHub publication timeout must be between 0 and 120 seconds")
        secret = token.get_secret_value()
        if not secret:
            raise ValueError("GitHub publication token must not be empty")
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def dispose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def publish(
        self,
        *,
        workspace: LocalGitWorkspace,
        intent: GitHubPublicationIntent,
        title: str,
        body: str,
    ) -> GitHubRemotePullRequest:
        owner, repo = parse_github_repository_url(intent.repository_url)
        if f"{owner}/{repo}" != intent.repository_slug:
            raise GitHubPublicationGatewayError(
                "REPOSITORY_IDENTITY_MISMATCH",
                "Persisted GitHub repository identity does not match the publication URL.",
            )

        await asyncio.to_thread(self._publish_branch, workspace, intent)
        existing = await self._find_pull_request(owner, repo, intent)
        if existing is not None:
            return existing

        try:
            return await self._create_pull_request(owner, repo, intent, title=title, body=body)
        except GitHubPublicationGatewayError as exc:
            if exc.code != "GITHUB_API_422":
                raise
            recovered = await self._find_pull_request(owner, repo, intent)
            if recovered is not None:
                return recovered
            raise

    def _publish_branch(
        self,
        workspace: LocalGitWorkspace,
        intent: GitHubPublicationIntent,
    ) -> None:
        self._assert_local_commit(workspace.root, intent.source_commit)
        existing = self._remote_branch_head(
            workspace.root,
            repository_url=intent.repository_url,
            branch_name=intent.branch_name,
        )
        if existing is not None:
            if existing != intent.source_commit:
                raise GitHubPublicationGatewayError(
                    "REMOTE_BRANCH_CONFLICT",
                    "The DevFlow publication branch already exists at a different commit.",
                )
            return

        result = self._run_git(
            workspace.root,
            [
                "push",
                "--porcelain",
                "--no-verify",
                intent.repository_url,
                f"{intent.source_commit}:refs/heads/{intent.branch_name}",
            ],
            authenticated=True,
        )
        if result.returncode != 0:
            raced = self._remote_branch_head(
                workspace.root,
                repository_url=intent.repository_url,
                branch_name=intent.branch_name,
            )
            if raced == intent.source_commit:
                return
            if raced is not None:
                raise GitHubPublicationGatewayError(
                    "REMOTE_BRANCH_CONFLICT",
                    "The DevFlow publication branch changed during publication.",
                )
            raise GitHubPublicationGatewayError(
                "GIT_PUSH_FAILED",
                "GitHub branch publication failed without changing accepted local Git truth.",
            )

        observed = self._remote_branch_head(
            workspace.root,
            repository_url=intent.repository_url,
            branch_name=intent.branch_name,
        )
        if observed != intent.source_commit:
            raise GitHubPublicationGatewayError(
                "REMOTE_BRANCH_VERIFY_FAILED",
                "Published GitHub branch does not match the evidence-bound source commit.",
            )

    def _assert_local_commit(self, root: Path, source_commit: str) -> None:
        if _COMMIT_RE.fullmatch(source_commit) is None:
            raise GitHubPublicationGatewayError(
                "INVALID_SOURCE_COMMIT",
                "Evidence-bound publication source commit is invalid.",
            )
        result = self._run_git(
            root,
            ["cat-file", "-e", f"{source_commit}^{{commit}}"],
            authenticated=False,
        )
        if result.returncode != 0:
            raise GitHubPublicationGatewayError(
                "SOURCE_COMMIT_MISSING",
                "Evidence-bound publication source commit is unavailable locally.",
            )

    def _remote_branch_head(
        self,
        root: Path,
        *,
        repository_url: str,
        branch_name: str,
    ) -> str | None:
        result = self._run_git(
            root,
            ["ls-remote", "--heads", repository_url, f"refs/heads/{branch_name}"],
            authenticated=True,
        )
        if result.returncode != 0:
            raise GitHubPublicationGatewayError(
                "REMOTE_BRANCH_LOOKUP_FAILED",
                "GitHub branch lookup failed.",
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) != 1:
            raise GitHubPublicationGatewayError(
                "REMOTE_BRANCH_AMBIGUOUS",
                "GitHub returned an ambiguous publication branch result.",
            )
        parts = lines[0].split("\t", 1)
        if len(parts) != 2 or parts[1] != f"refs/heads/{branch_name}":
            raise GitHubPublicationGatewayError(
                "REMOTE_BRANCH_INVALID",
                "GitHub returned an invalid publication branch result.",
            )
        commit = parts[0].strip().lower()
        if _COMMIT_RE.fullmatch(commit) is None:
            raise GitHubPublicationGatewayError(
                "REMOTE_BRANCH_INVALID",
                "GitHub publication branch returned an invalid commit identity.",
            )
        return commit

    def _run_git(
        self,
        root: Path,
        arguments: list[str],
        *,
        authenticated: bool,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if authenticated:
            token = self._token.get_secret_value()
            credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
            env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise GitHubPublicationGatewayError(
                "GIT_UNAVAILABLE",
                "Git executable is unavailable for GitHub publication.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubPublicationGatewayError(
                "GIT_TIMEOUT",
                "GitHub publication Git operation exceeded its timeout.",
            ) from exc

    async def _find_pull_request(
        self,
        owner: str,
        repo: str,
        intent: GitHubPublicationIntent,
    ) -> GitHubRemotePullRequest | None:
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": "all",
                "head": f"{owner}:{intent.branch_name}",
                "base": intent.base_branch,
                "per_page": "100",
            },
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise GitHubPublicationGatewayError(
                "GITHUB_RESPONSE_INVALID",
                "GitHub Pull Request lookup returned an invalid response.",
            )
        if len(payload) > 1:
            raise GitHubPublicationGatewayError(
                "REMOTE_PR_AMBIGUOUS",
                "Multiple Pull Requests already target the DevFlow publication branch.",
            )
        if not payload:
            return None
        return self._decode_pull_request(payload[0], intent)

    async def _create_pull_request(
        self,
        owner: str,
        repo: str,
        intent: GitHubPublicationIntent,
        *,
        title: str,
        body: str,
    ) -> GitHubRemotePullRequest:
        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title[:256],
                "body": body[:8000],
                "head": intent.branch_name,
                "base": intent.base_branch,
                "draft": True,
                "maintainer_can_modify": False,
            },
            expected_status=201,
        )
        pull_request = self._decode_pull_request(response.json(), intent)
        if not pull_request.draft:
            raise GitHubPublicationGatewayError(
                "REMOTE_PR_NOT_DRAFT",
                "GitHub did not create the publication Pull Request as a Draft.",
            )
        return pull_request

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        expected_status: int = 200,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._api_base_url}{path}",
                params=params,
                json=json,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token.get_secret_value()}",
                    "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                    "User-Agent": "DevFlow",
                },
            )
        except httpx.HTTPError as exc:
            raise GitHubPublicationGatewayError(
                "GITHUB_API_UNAVAILABLE",
                "GitHub API request failed without changing accepted runtime truth.",
            ) from exc
        if response.status_code != expected_status:
            raise GitHubPublicationGatewayError(
                f"GITHUB_API_{response.status_code}",
                f"GitHub API rejected the bounded publication request with HTTP {response.status_code}.",
            )
        return response

    @staticmethod
    def _decode_pull_request(
        payload: object,
        intent: GitHubPublicationIntent,
    ) -> GitHubRemotePullRequest:
        if not isinstance(payload, dict):
            raise GitHubPublicationGatewayError(
                "GITHUB_RESPONSE_INVALID",
                "GitHub Pull Request response is not an object.",
            )
        head = payload.get("head")
        base = payload.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise GitHubPublicationGatewayError(
                "GITHUB_RESPONSE_INVALID",
                "GitHub Pull Request response lacks head/base facts.",
            )
        candidate = {
            "number": payload.get("number"),
            "html_url": payload.get("html_url"),
            "state": payload.get("state"),
            "draft": payload.get("draft"),
            "head_branch": head.get("ref"),
            "head_commit": head.get("sha"),
            "base_branch": base.get("ref"),
        }
        try:
            result = GitHubRemotePullRequest.model_validate(candidate)
        except ValidationError as exc:
            raise GitHubPublicationGatewayError(
                "GITHUB_RESPONSE_INVALID",
                "GitHub Pull Request response failed typed validation.",
            ) from exc
        if (
            result.head_branch != intent.branch_name
            or result.head_commit != intent.source_commit
            or result.base_branch != intent.base_branch
        ):
            raise GitHubPublicationGatewayError(
                "REMOTE_PR_IDENTITY_MISMATCH",
                "GitHub Pull Request does not match the evidence-bound publication target.",
            )
        expected_prefix = f"https://github.com/{intent.repository_slug}/pull/"
        if not result.html_url.startswith(expected_prefix):
            raise GitHubPublicationGatewayError(
                "REMOTE_PR_IDENTITY_MISMATCH",
                "GitHub Pull Request URL does not match the persisted repository.",
            )
        return result
