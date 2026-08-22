from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.project import ProjectProvisionStatus, canonical_repository_url
from app.persistence.errors import PersistenceConflictError
from app.persistence.repository import PostgresEvidenceStore


class ProjectAwarePostgresEvidenceStore(PostgresEvidenceStore):
    """Extend accepted evidence persistence with durable Project lifecycle facts."""

    async def ensure_project(
        self,
        *,
        repository_url: str,
        default_branch: str,
        project_id: UUID | None = None,
    ) -> UUID:
        canonical = canonical_repository_url(repository_url)
        branch = self._required_text(default_branch, "default_branch", max_length=255)
        candidate_id = project_id or uuid4()
        async with self._session_factory.begin() as session:
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO projects "
                        "(id, repository_url, canonical_repository_url, default_branch, "
                        "provision_status) "
                        "VALUES (:id, :repository, :canonical, :branch, :status) "
                        "ON CONFLICT (canonical_repository_url, default_branch) DO NOTHING "
                        "RETURNING id"
                    ),
                    {
                        "id": candidate_id,
                        "repository": canonical,
                        "canonical": canonical,
                        "branch": branch,
                        "status": ProjectProvisionStatus.PROVISIONING.value,
                    },
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return inserted
            existing = (
                await session.execute(
                    text(
                        "SELECT id FROM projects WHERE canonical_repository_url = :canonical "
                        "AND default_branch = :branch"
                    ),
                    {"canonical": canonical, "branch": branch},
                )
            ).scalar_one_or_none()
            if existing is None:
                raise PersistenceConflictError("project identity conflict could not be resolved")
            return existing

    async def mark_project_provisioning(self, project_id: UUID) -> None:
        await self._set_status(project_id, ProjectProvisionStatus.PROVISIONING)

    async def mark_project_ready(self, project_id: UUID, *, synced_commit: str) -> None:
        commit = synced_commit.strip().lower()
        if len(commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in commit):
            raise ValueError("synced_commit must be a lowercase Git object id")
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE projects SET provision_status = 'READY', "
                    "provision_error_code = NULL, provision_error_message = NULL, "
                    "last_provisioned_at = now(), last_synced_at = now(), "
                    "last_synced_commit = :commit WHERE id = :project_id"
                ),
                {"project_id": project_id, "commit": commit},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence project: {project_id}")

    async def mark_project_failed(
        self,
        project_id: UUID,
        *,
        code: str,
        message: str,
    ) -> None:
        normalized_code = self._required_text(code, "code", max_length=64)
        normalized_message = self._required_text(message, "message", max_length=512)
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE projects SET provision_status = 'FAILED', "
                    "provision_error_code = :code, provision_error_message = :message "
                    "WHERE id = :project_id"
                ),
                {
                    "project_id": project_id,
                    "code": normalized_code,
                    "message": normalized_message,
                },
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence project: {project_id}")

    async def record_project_sync(self, project_id: UUID, *, commit: str) -> None:
        normalized = commit.strip().lower()
        if len(normalized) not in {40, 64} or any(
            ch not in "0123456789abcdef" for ch in normalized
        ):
            raise ValueError("commit must be a lowercase Git object id")
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE projects SET last_synced_at = now(), last_synced_commit = :commit "
                    "WHERE id = :project_id"
                ),
                {"project_id": project_id, "commit": normalized},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence project: {project_id}")

    async def update_project_branch_if_unused(self, project_id: UUID, *, branch: str) -> None:
        normalized_branch = self._required_text(branch, "default_branch", max_length=255)
        try:
            async with self._session_factory.begin() as session:
                run_count = (
                    await session.execute(
                        text("SELECT count(*) FROM runs WHERE project_id = :project_id"),
                        {"project_id": project_id},
                    )
                ).scalar_one()
                if run_count:
                    raise PersistenceConflictError(
                        "Project identity is immutable after the first persisted Run"
                    )
                result = await session.execute(
                    text(
                        "UPDATE projects SET default_branch = :branch, "
                        "provision_status = 'PROVISIONING', provision_error_code = NULL, "
                        "provision_error_message = NULL, last_synced_at = NULL, "
                        "last_synced_commit = NULL WHERE id = :project_id"
                    ),
                    {"project_id": project_id, "branch": normalized_branch},
                )
                if result.rowcount != 1:
                    raise ValueError(f"unknown persistence project: {project_id}")
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "repository/default-branch Project identity already exists"
            ) from exc

    async def _set_status(
        self,
        project_id: UUID,
        status: ProjectProvisionStatus,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE projects SET provision_status = :status, "
                    "provision_error_code = NULL, provision_error_message = NULL "
                    "WHERE id = :project_id"
                ),
                {"project_id": project_id, "status": status.value},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence project: {project_id}")
