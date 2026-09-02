from __future__ import annotations

from uuid import UUID

from app.agents.dag_planner import MultiTaskPlannerAgent
from app.agents.developer import DeveloperAgent
from app.api.catalog import PostgresProductCatalog
from app.api.failure_explanation import FailureExplanationService
from app.api.hardened import HardenedOperatorAwareAutonomousProductRuntimeService
from app.api.lifecycle_rollout import LifecycleRolloutGate
from app.api.repository_context import RepositoryPlanningContextBuilder
from app.context.token_estimator import TokenEstimator
from app.core.settings import Settings
from app.dispatch import DurableDramatiqTaskDispatcher
from app.models.sandbox import DockerSandboxPolicy
from app.persistence import (
    PostgresDAGStore,
    PostgresDevelopmentSessionStore,
    PostgresDispatchAttemptStore,
    PostgresFailureExplanationStore,
    PostgresGitHubPublicationStore,
    PostgresInterfaceContractRegistry,
    PostgresLifecycleStore,
    PostgresOperationAuditStore,
    PostgresPlanningTokenBudgetStore,
    PostgresProjectCredentialStore,
    PostgresRunRecoveryStore,
    PostgresRunTokenBudgetStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
    ProjectDeletionTokenSigner,
)
from app.persistence.operator import OperatorAwarePostgresEvidenceStore
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.persistence.repair_completion import RepairAwarePostgresMultiTaskCompletionStore
from app.providers.siliconflow import SiliconFlowDriver
from app.publication import PersistentProjectGitHubPublisher
from app.runtime.integration_repair import IntegrationConflictRepairService
from app.runtime.operator_recovery import OperatorRecoveryCoordinator, OperatorRecoveryPlanner
from app.runtime.product_controller import DurableMultiAgentRunController
from app.runtime.reconciler import IdempotentTaskReconciler
from app.runtime.repair_execution_base import RepairAwareEvidenceBoundTaskExecutionBaseResolver
from app.runtime.run_reconciler import DAGRunReconciler
from app.verification import DeterministicVerifier
from app.verification.dependency_preflight import DependencyEnvironmentPreflight
from app.verification.project_profile import ProjectAwareVerificationRunner
from app.workers.executor import ManagedProjectWorkspaceResolver
from app.workspace import ManagedProjectProvisioner
from app.workspace.lifecycle import LocalProjectArtifacts


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


def build_product_service(
    settings: Settings,
) -> HardenedOperatorAwareAutonomousProductRuntimeService:
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
    credential_store = PostgresProjectCredentialStore.from_url(
        settings.database_url,
        encryption_key=settings.secrets_encryption_key,
        echo=settings.database_echo,
    )
    failure_explanation_store = PostgresFailureExplanationStore.from_url(
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
    interface_contract_registry = PostgresInterfaceContractRegistry.from_url(
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
        max_concurrent_tasks=settings.dag_max_concurrent_tasks,
        # Keep the registry owned by the Product service in every rollout mode; the service
        # only declares/enforces contracts when ``contract_gated`` is selected.
        interface_contract_registry=interface_contract_registry,
    )
    token_budget_store = PostgresRunTokenBudgetStore.from_url(
        settings.database_url,
        default_total_budget_tokens=settings.run_token_budget_tokens,
        adaptive_package_budget_enabled=settings.adaptive_package_budget_enabled,
        token_estimate_safety_factor=settings.token_estimate_safety_factor,
        echo=settings.database_echo,
    )
    planning_budget_store = PostgresPlanningTokenBudgetStore.from_url(
        settings.database_url,
        default_total_budget_tokens=settings.planner_token_budget_tokens,
        default_max_attempts=settings.planner_max_attempts,
        echo=settings.database_echo,
    )
    development_session_store = PostgresDevelopmentSessionStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    operation_audit_store = PostgresOperationAuditStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    lifecycle_store = PostgresLifecycleStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    run_recovery_store = PostgresRunRecoveryStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
        startup_timeout_seconds=settings.recovery_startup_timeout_seconds,
        stale_progress_seconds=settings.recovery_stale_progress_seconds,
    )
    # Prefer the dedicated local encryption key.  Older local installations that have not yet
    # configured it still receive a process-local signing secret derived from the database URL;
    # neither value is returned to the browser or written to audit records.
    deletion_token_signer = ProjectDeletionTokenSigner(
        settings.secrets_encryption_key or settings.database_url
    )

    driver = (
        None if settings.siliconflow_api_key is None else SiliconFlowDriver.from_settings(settings)
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
            developer=DeveloperAgent(
                driver=driver,
                model=settings.developer_model,
                max_iterations=settings.developer_max_iterations,
                max_duration_seconds=settings.developer_max_duration_seconds,
                max_model_turn_seconds=settings.developer_max_model_turn_seconds,
                max_output_tokens=settings.developer_max_output_tokens,
                invalid_tool_retry_max_output_tokens=(
                    settings.developer_invalid_tool_retry_max_output_tokens
                ),
                enable_thinking=settings.developer_enable_thinking,
                context_compaction_enabled=settings.context_compaction_enabled,
                role_context_projection_enabled=settings.role_context_projection_enabled,
                max_retained_tool_groups=settings.developer_max_retained_tool_groups,
                max_single_tool_result_tokens=settings.developer_max_single_tool_result_tokens,
                max_tool_results_per_turn_tokens=settings.developer_max_tool_results_per_turn_tokens,
            ),
            verifier=DeterministicVerifier(
                command_timeout_seconds=settings.verification_sandbox_timeout_seconds,
                command_runner=ProjectAwareVerificationRunner(
                    base_policy=sandbox_policy,
                    node_image=settings.verification_node_sandbox_image,
                    cache_root=settings.workspace_root / "verification-deps",
                    proxy_url=settings.dependency_proxy_url,
                    python_index_url=settings.dependency_python_index_url,
                    node_registry_url=settings.dependency_node_registry_url,
                    build_timeout_seconds=settings.dependency_preflight_build_timeout_seconds,
                    max_cache_bytes=settings.dependency_cache_max_bytes,
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
            max_schema_repair_attempts=settings.planner_max_attempts - 1,
            initial_max_output_tokens=settings.planner_initial_max_output_tokens,
            json_repair_max_output_tokens=settings.planner_json_repair_max_output_tokens,
            budget_replan_max_output_tokens=settings.planner_budget_replan_max_output_tokens,
            enable_thinking=settings.planner_enable_thinking,
            adaptive_work_package_routing_enabled=settings.adaptive_work_package_routing_enabled,
        )
    )
    publication_token = settings.effective_github_publication_token
    github_publisher = PersistentProjectGitHubPublisher(
        credential_store=credential_store,
        fallback_token=publication_token,
    )
    failure_explainer = FailureExplanationService(
        driver=driver,
        model=settings.failure_explanation_model,
        cache=failure_explanation_store,
        max_output_tokens=settings.failure_explanation_max_output_tokens,
        enable_thinking=settings.failure_explanation_enable_thinking,
    )
    dependency_preflight = DependencyEnvironmentPreflight(
        cache_root=settings.workspace_root / "verification-deps",
        python_image=settings.verification_sandbox_image,
        node_image=settings.verification_node_sandbox_image,
        proxy_url=settings.dependency_proxy_url,
        python_index_url=settings.dependency_python_index_url,
        node_registry_url=settings.dependency_node_registry_url,
        timeout_seconds=settings.dependency_preflight_timeout_seconds,
        build_timeout_seconds=settings.dependency_preflight_build_timeout_seconds,
        max_cache_bytes=settings.dependency_cache_max_bytes,
    )
    return HardenedOperatorAwareAutonomousProductRuntimeService(
        catalog=catalog,
        evidence_store=evidence_store,
        dag_store=dag_store,
        provisioner=provisioner,
        workspace_resolver=resolver,
        dispatcher=dispatcher,
        dependency_preflight=dependency_preflight,
        token_budget_store=token_budget_store,
        interface_contract_registry=interface_contract_registry,
        planning_budget_store=planning_budget_store,
        development_session_store=development_session_store,
        operation_audit_store=operation_audit_store,
        lifecycle_store=lifecycle_store,
        deletion_token_signer=deletion_token_signer,
        local_project_artifacts=LocalProjectArtifacts(
            repository_root=repository_root,
            project_cache_root=settings.workspace_root / "project-caches",
        ),
        token_estimator=TokenEstimator(safety_factor=settings.token_estimate_safety_factor),
        workflow_activation_mode=settings.workflow_activation_mode,
        work_package_activation_mode=settings.work_package_activation_mode,
        publication_store=publication_store,
        github_publisher=github_publisher,
        project_publication_token_recorder=github_publisher.remember,
        requirement_planner=requirement_planner,
        failure_explainer=failure_explainer,
        run_controller=run_controller,
        trace_dispatch_reader=dispatch_store,
        operator_recovery=operator_recovery,
        operator_audit_resource=operator_audit_store,
        run_recovery_store=run_recovery_store,
        recovery_check_interval_seconds=settings.recovery_check_interval_seconds,
        lifecycle_rollout_gate=LifecycleRolloutGate(
            mode=settings.lifecycle_rollout_mode,
            environment=settings.environment,
            test_repository_url=settings.lifecycle_test_repository_url,
            project_allowlist=settings.lifecycle_project_ids,
        ),
        # Planning receives immutable repository metadata only.  The launch budget preflights
        # one initial plan plus one compact recovery before any provider request is made.
        planning_context_builder=RepositoryPlanningContextBuilder(
            max_directory_entries=100,
            max_context_chars=2_400,
        ),
    )
