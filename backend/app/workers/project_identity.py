from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence
from app.persistence.types import PersistedRunSnapshot
from app.workspace import ManagedProjectProvisioner


class ProjectIdentityRunStore(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class ProjectIdentityQueuedWorker(Protocol):
    async def execute(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
    ) -> WorkerExecutionEvidence: ...


class ProjectIdentityValidatingQueuedTaskWorker:
    """Close the API→queue TOCTOU window by rechecking persisted Project Git identity."""

    def __init__(
        self,
        *,
        worker: ProjectIdentityQueuedWorker,
        run_store: ProjectIdentityRunStore,
        provisioner: ManagedProjectProvisioner,
    ) -> None:
        self._worker = worker
        self._run_store = run_store
        self._provisioner = provisioner

    async def execute(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
    ) -> WorkerExecutionEvidence:
        snapshot = await self._run_store.load_run(envelope.run_id)
        readiness = await asyncio.to_thread(
            self._provisioner.readiness,
            snapshot.project_id,
            repository_url=snapshot.repository_url,
            default_branch=snapshot.default_branch,
        )
        if not readiness.ready:
            raise WorkerExecutionBoundaryError(
                "managed Project Git identity changed after dispatch; worker execution is refused"
            )
        return await self._worker.execute(envelope, run_token=run_token)
