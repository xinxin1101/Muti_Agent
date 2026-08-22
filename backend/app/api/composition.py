from __future__ import annotations

from uuid import UUID

from app.agents.dag_planner import MultiTaskPlannerAgent
from app.agents.developer import DeveloperAgent
from app.api.catalog import PostgresProductCatalog
from app.api.hardened import HardenedOperatorAwareAutonomousProductRuntimeService
from app.api.repository_context import RepositoryPlanningContextBuilder
from app.core.settings import Settings
from app.dispatch import DurableDramatiqTaskDispatcher
from app.models.sandbox import DockerSandboxPolicy
from app.persistence import (
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresGitHubPublicationStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.persistence.operator import OperatorAwarePostgresEvidenceStore
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.persistence.repair_completion import RepairAwarePostgresMultiTaskCompletionStore
from app.providers.siliconflow import SiliconFlowDriver
from app.publication import GitHubPublicationGateway
from app.runtime.integration_repair import IntegrationConflictRepairService
from app.runtime.operator_recovery import OperatorRecoveryCoordinator, OperatorRecoveryPlanner
from app.runtime.product_controller import DurableMultiAgentRunController
from app.runtime.reconciler import IdempotentTaskReconciler
from app.runtime.repair_execution_base import RepairAwareEvidenceBoundTaskExecutionBaseResolver
from app.runtime.run_reconciler import DAGRunReconciler
from app.verification import DeterministicVerifier
from app.verification.project_profile import ProjectAwareVerificationRunner
from app.workers.executor import ManagedProjectWorkspaceResolver
from app.workspace import ManagedProjectProvisioner


class _ProductRunController:
    """Own Product-API controller dependencies without sharing their engine lifecycle."""

    def __init__(
        self,
        *,
        controller: DurableMultiAgentRunController,
        run_reconciler: DAGRunReconciler,
        task_reconciler: IdempotentTaskReconciler,
        completion_store: RepairAwarePostgresMultiTaskCompletionStore,
        lease_store: PostgresTaskLeaseStore,
    ) -> None:
        self._controller = controller
        self._run_reconciler = run_reconciler
        self._task_reconciler = task_reconciler
        self._completion_store = completion_store
        self._lease_store = lease_store

    async def advance(self, run_id: UUID):
        return await self._controller.advance(run_id)

    async def reconcile_run(self, run_id: UUID):
        return await self._run_reconciler.reconcile_run(run_id)

    async def dispose(self) -> None:
        await self._task_reconciler.dispose()
        await self._completion_store.dispose()
        await self._lease_store.dispose()


def build_product_service(settings: Settings) -> HardenedOperatorAwareAutonomousProductRuntimeService:
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by the product API")

    from app.workers.tasks import execute_devflow_task

    evidence_store = ProjectAwarePostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    operator_audit_store = OperatorAwarePostgresEvidenceStore.from_url(
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
    completion_store = RepairAwarePostgresMultiTaskCompletionStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )

    repository_root = settings.workspace_root / "repos"
    provisioner = ManagedProjectProvisioner(
        repository_root,
        git_timeout_seconds=settings.git_clone_timeout_seconds,
        read_token=settings.github_read_token,
    )
    resolver = ManagedProjectWorkspaceResolver(repository_root)
    dispatcher = DurableDramatiqTaskDispatcher(
        run_store=evidence_store,
        ledger=dispatch_store,
        actor=execute_devflow_task,
    )
    execution_base_resolver = RepairAwareEvidenceBoundTaskExecutionBaseResolver(
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

    driver = (
        None
        if settings.siliconflow_api_key is None
        else SiliconFlowDriver.from_settings(settings)
    )
    conflict_repairer = None
    if driver is not None:
        sandbox_policy = DockerSandboxPolicy(
            image=settings.verification_sandbox_image,
            cpus=settings.verification_sandbox_cpus,
            memory_mb=settings.verification_sandbox_memory_mb,
            pids_limit=settings.verification_sandbox_pids_limit,
            tmpfs_mb=settings.verification_sandbox_tmpfs_mb,
            shm_mb=settings.verification_sandbox_shm_mb,
        )
        conflict_repairer = IntegrationConflictRepairService(
            evidence_store=evidence_store,
            repair_store=completion_store,
            workspace_resolver=resolver,
            developer=DeveloperAgent(driver=driver, model=settings.developer_model),
            verifier=DeterministicVerifier(
                command_timeout_seconds=settings.verification_sandbox_timeout_seconds,
                command_runner=ProjectAwareVerificationRunner(
                    base_policy=sandbox_policy,
                    node_image=settings.verification_node_sandbox_image,
                    cache_root=settings.workspace_root / "verification-deps",
                ),
            ),
            repair_root=settings.workspace_root / "integration-repairs",
        )

    run_controller = _ProductRunController(
        controller=DurableMultiAgentRunController(
            evidence_store=evidence_store,
            dag_store=dag_store,
            workspace_resolver=resolver,
            run_reconciler=dag_run_reconciler,
            completion_store=completion_store,
            conflict_repairer=conflict_repairer,
        ),
        run_reconciler=dag_run_reconciler,
        task_reconciler=task_reconciler,
        completion_store=completion_store,
        lease_store=lease_store,
    )
    operator_recovery = OperatorRecoveryCoordinator(
        planner=OperatorRecoveryPlanner(
            run_reader=evidence_store,
            dag_reader=dag_store,
            lease_reader=lease_store,
            dispatch_reader=dispatch_store,
            execution_base_resolver=execution_base_resolver,
        ),
        audit_store=operator_audit_store,
        run_controller=run_controller,
        run_reconciler=run_controller,
    )
    requirement_planner = (
        None
        if driver is None
        else MultiTaskPlannerAgent(
            driver=driver,
            model=settings.planner_model,
        )
    )
    publication_token = settings.effective_github_publication_token
    github_publisher = (
        None
        if publication_token is None
        else GitHubPublicationGateway(
            publication_token,
            timeout_seconds=settings.github_publication_timeout_seconds,
        )
    )
    return HardenedOperatorAwareAutonomousProductRuntimeService(
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
        trace_dispatch_reader=dispatch_store,
        operator_recovery=operator_recovery,
        operator_audit_resource=operator_audit_store,
        planning_context_builder=RepositoryPlanningContextBuilder(),
    )
