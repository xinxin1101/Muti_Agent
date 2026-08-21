from __future__ import annotations

import os
import socket
from collections.abc import Callable
from functools import lru_cache
from uuid import uuid4

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.core.settings import Settings, get_settings
from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence
from app.models.sandbox import DockerSandboxPolicy
from app.models.task import TaskContract
from app.persistence import (
    PostgresDAGStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.persistence.repair_completion import RepairAwarePostgresMultiTaskCompletionStore
from app.providers.siliconflow import SiliconFlowDriver
from app.runtime.integration_repair import IntegrationConflictRepairService
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.runtime.product_controller import DurableMultiAgentRunController
from app.runtime.reconciler import IdempotentTaskReconciler
from app.runtime.repair_execution_base import RepairAwareEvidenceBoundTaskExecutionBaseResolver
from app.runtime.run_reconciler import DAGRunReconciler
from app.trace.persistence import TraceAwarePostgresEvidenceStore
from app.trace.worker import (
    TraceAwareLocalQueuedTaskExecutionBackend,
    TraceAwareQueuedTaskWorker,
)
from app.verification import DeterministicVerifier, DockerSandboxRunner
from app.workers.executor import ManagedProjectWorkspaceResolver, QueuedTaskWorker
from app.workers.lease import LeasedQueuedTaskWorker


@lru_cache(maxsize=32)
def _generated_worker_id(process_id: int) -> str:
    """Create one stable fallback identity per actual worker process id."""

    return f"{socket.gethostname()}:{process_id}:{uuid4().hex[:12]}"


def resolve_worker_id(settings: Settings) -> str:
    """Return configured identity or one stable, fork-safe fallback per worker process."""

    return settings.worker_id or _generated_worker_id(os.getpid())


def build_verifier(settings: Settings) -> DeterministicVerifier:
    policy = DockerSandboxPolicy(
        image=settings.verification_sandbox_image,
        cpus=settings.verification_sandbox_cpus,
        memory_mb=settings.verification_sandbox_memory_mb,
        pids_limit=settings.verification_sandbox_pids_limit,
        tmpfs_mb=settings.verification_sandbox_tmpfs_mb,
        shm_mb=settings.verification_sandbox_shm_mb,
    )
    return DeterministicVerifier(
        command_timeout_seconds=settings.verification_sandbox_timeout_seconds,
        command_runner=DockerSandboxRunner(policy),
    )


def build_single_task_runner(settings: Settings) -> SingleTaskOrchestrator:
    driver = SiliconFlowDriver.from_settings(settings)
    developer = DeveloperAgent(driver=driver, model=settings.developer_model)
    reviewer = ReviewerAgent(driver=driver, model=settings.reviewer_model)
    repair = RepairAgent(driver=driver, model=settings.repair_model)
    return SingleTaskOrchestrator(
        developer=developer,
        verifier=build_verifier(settings),
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

    evidence_store = TraceAwarePostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    dag_store = PostgresDAGStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    lease_store = PostgresTaskLeaseStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    reconciliation_store = PostgresTaskReconciliationStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    completion_store = RepairAwarePostgresMultiTaskCompletionStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    task_reconciler = None
    try:
        resolver = ManagedProjectWorkspaceResolver(settings.workspace_root / "repos")
        execution_base_resolver = RepairAwareEvidenceBoundTaskExecutionBaseResolver(
            dag_reader=dag_store,
            workspace_resolver=resolver,
        )
        backend = TraceAwareLocalQueuedTaskExecutionBackend(
            workspace_resolver=resolver,
            worktree_root=settings.workspace_root / "worktrees",
            runner_factory=build_runner_factory(settings),
            git_fence=lease_store,
            trace_store=evidence_store,
        )
        queued_worker = TraceAwareQueuedTaskWorker(
            QueuedTaskWorker(
                store=evidence_store,
                backend=backend,
                execution_base_resolver=execution_base_resolver,
            )
        )
        leased_worker = LeasedQueuedTaskWorker(
            worker=queued_worker,
            lease_store=lease_store,
            worker_id=resolve_worker_id(settings),
            lease_seconds=settings.worker_lease_seconds,
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        )
        result = await leased_worker.execute(envelope)

        # Import only after app.workers.tasks has finished defining its actor. Importing it at
        # module load time would create a runtime.py <-> tasks.py cycle.
        from app.workers.tasks import execute_devflow_task

        task_reconciler = IdempotentTaskReconciler(
            store=reconciliation_store,
            actor=execute_devflow_task,
        )
        run_reconciler = DAGRunReconciler(
            run_reader=evidence_store,
            dag_reader=dag_store,
            lease_reader=lease_store,
            task_reconciler=task_reconciler,
            execution_base_resolver=execution_base_resolver,
        )
        driver = SiliconFlowDriver.from_settings(settings)
        conflict_repairer = IntegrationConflictRepairService(
            evidence_store=evidence_store,
            repair_store=completion_store,
            workspace_resolver=resolver,
            developer=DeveloperAgent(driver=driver, model=settings.developer_model),
            verifier=build_verifier(settings),
            repair_root=settings.workspace_root / "integration-repairs",
        )
        controller = DurableMultiAgentRunController(
            evidence_store=evidence_store,
            dag_store=dag_store,
            workspace_resolver=resolver,
            run_reconciler=run_reconciler,
            completion_store=completion_store,
            conflict_repairer=conflict_repairer,
        )
        await controller.advance(envelope.run_id)
        return result
    finally:
        # The task reconciler owns reconciliation_store once constructed. Before construction the
        # store still needs to be disposed directly.
        if task_reconciler is None:
            await reconciliation_store.dispose()
        else:
            await task_reconciler.dispose()
        await completion_store.dispose()
        await lease_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()