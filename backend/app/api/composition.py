from __future__ import annotations

from uuid import UUID

from app.agents.dag_planner import MultiTaskPlannerAgent
from app.api.autonomous import AutonomousProductRuntimeService
from app.api.catalog import PostgresProductCatalog
from app.core.settings import Settings
from app.dispatch import DurableDramatiqTaskDispatcher
from app.persistence import (
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresGitHubPublicationStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.persistence.multi_completion import PostgresMultiTaskCompletionStore
from app.providers.siliconflow import SiliconFlowDriver
from app.publication import GitHubPublicationGateway
from app.runtime.execution_base import EvidenceBoundTaskExecutionBaseResolver
from app.runtime.product_controller import DurableMultiAgentRunController
from app.runtime.reconciler import IdempotentTaskReconciler
from app.runtime.run_reconciler import DAGRunReconciler
from app.workers.executor import ManagedProjectWorkspaceResolver
from app.workspace import ManagedProjectProvisioner


class _ProductRunController:
    """Own Product-API controller dependencies without sharing their engine lifecycle."""

    def __init__(
        self,
        *,
        controller: DurableMultiAgentRunController,
        task_reconciler: IdempotentTaskReconciler,
        completion_store: PostgresMultiTaskCompletionStore,
        lease_store: PostgresTaskLeaseStore,
    ) -> None:
        self._controller = controller
        self._task_reconciler = task_reconciler
        self._completion_store = completion_store
        self._lease_store = lease_store

    async def advance(self, run_id: UUID):
        return await self._controller.advance(run_id)

    async def dispose(self) -> None:
        await self._task_reconciler.dispose()
        await self._completion_store.dispose()
        await self._lease_store.dispose()


def build_product_service(settings: Settings) -> AutonomousProductRuntimeService:
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by the product API")

    from app.workers.tasks import execute_devflow_task

    evidence_store = PostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    dag_store = PostgresDAGStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    dispatch_store = PostgresDispatchAttemptStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    publication_store = PostgresGitHubPublicationStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    catalog = PostgresProductCatalog.from_url(
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
    completion_store = PostgresMultiTaskCompletionStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )

    repository_root = settings.workspace_root / "repos"
    provisioner = ManagedProjectProvisioner(repository_root)
    resolver = ManagedProjectWorkspaceResolver(repository_root)
    dispatcher = DurableDramatiqTaskDispatcher(
        run_store=evidence_store,
        ledger=dispatch_store,
        actor=execute_devflow_task,
    )
    execution_base_resolver = EvidenceBoundTaskExecutionBaseResolver(
        dag_reader=dag_store,
        workspace_resolver=resolver,
    )
    task_reconciler = IdempotentTaskReconciler(
        store=reconciliation_store,
        actor=execute_devflow_task,
    )
    dag_run_reconciler = DAGRunReconciler(
        run_reader=evidence_store,
        dag_reader=dag_store,
        lease_reader=lease_store,
        task_reconciler=task_reconciler,
        execution_base_resolver=execution_base_resolver,
    )
    run_controller = _ProductRunController(
        controller=DurableMultiAgentRunController(
            evidence_store=evidence_store,
            dag_store=dag_store,
            workspace_resolver=resolver,
            run_reconciler=dag_run_reconciler,
            completion_store=completion_store,
        ),
        task_reconciler=task_reconciler,
        completion_store=completion_store,
        lease_store=lease_store,
    )
    requirement_planner = (
        None
        if settings.siliconflow_api_key is None
        else MultiTaskPlannerAgent(
            driver=SiliconFlowDriver.from_settings(settings),
            model=settings.planner_model,
        )
    )
    github_publisher = (
        None
        if settings.github_token is None
        else GitHubPublicationGateway(
            settings.github_token,
            timeout_seconds=settings.github_publication_timeout_seconds,
        )
    )
    return AutonomousProductRuntimeService(
        catalog=catalog,
        evidence_store=evidence_store,
        dag_store=dag_store,
        provisioner=provisioner,
        workspace_resolver=resolver,
        dispatcher=dispatcher,
        publication_store=publication_store,
        github_publisher=github_publisher,
        requirement_planner=requirement_planner,
        run_controller=run_controller,
    )
