from __future__ import annotations

from collections.abc import Callable

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.core.settings import Settings, get_settings
from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence
from app.models.sandbox import DockerSandboxPolicy
from app.models.task import TaskContract
from app.persistence import PostgresEvidenceStore
from app.providers.siliconflow import SiliconFlowDriver
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.verification import DeterministicVerifier, DockerSandboxRunner
from app.workers.executor import (
    LocalQueuedTaskExecutionBackend,
    ManagedProjectWorkspaceResolver,
    QueuedTaskWorker,
)


def build_single_task_runner(settings: Settings) -> SingleTaskOrchestrator:
    driver = SiliconFlowDriver.from_settings(settings)
    developer = DeveloperAgent(driver=driver, model=settings.developer_model)
    reviewer = ReviewerAgent(driver=driver, model=settings.reviewer_model)
    repair = RepairAgent(driver=driver, model=settings.repair_model)
    policy = DockerSandboxPolicy(
        image=settings.verification_sandbox_image,
        cpus=settings.verification_sandbox_cpus,
        memory_mb=settings.verification_sandbox_memory_mb,
        pids_limit=settings.verification_sandbox_pids_limit,
        tmpfs_mb=settings.verification_sandbox_tmpfs_mb,
        shm_mb=settings.verification_sandbox_shm_mb,
    )
    verifier = DeterministicVerifier(
        command_timeout_seconds=settings.verification_sandbox_timeout_seconds,
        command_runner=DockerSandboxRunner(policy),
    )
    return SingleTaskOrchestrator(
        developer=developer,
        verifier=verifier,
        reviewer=reviewer,
        repair=repair,
        developer_model=settings.developer_model,
        reviewer_model=settings.reviewer_model,
        repair_model=settings.repair_model,
    )


def build_runner_factory(settings: Settings) -> Callable[[TaskContract], SingleTaskOrchestrator]:
    def factory(_task: TaskContract) -> SingleTaskOrchestrator:
        return build_single_task_runner(settings)

    return factory


async def execute_task_from_settings(
    envelope: TaskDispatchEnvelope,
) -> WorkerExecutionEvidence:
    """Production worker composition root loaded only after a task message is received."""

    settings = get_settings()
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by queued workers")

    store = PostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    try:
        resolver = ManagedProjectWorkspaceResolver(settings.workspace_root / "repos")
        backend = LocalQueuedTaskExecutionBackend(
            workspace_resolver=resolver,
            worktree_root=settings.workspace_root / "worktrees",
            runner_factory=build_runner_factory(settings),
        )
        worker = QueuedTaskWorker(store=store, backend=backend)
        return await worker.execute(envelope)
    finally:
        await store.dispose()
