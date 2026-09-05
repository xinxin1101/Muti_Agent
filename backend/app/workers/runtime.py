from __future__ import annotations

import os
import socket
from collections.abc import Callable
from functools import lru_cache
from uuid import UUID, uuid4

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.context.token_estimator import TokenEstimator
from app.core.settings import Settings, get_settings
from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence
from app.models.sandbox import DockerSandboxPolicy
from app.models.task import TaskContract
from app.models.work_package import WorkPackageActivationMode
from app.persistence import (
    PostgresDAGStore,
    PostgresInterfaceContractRegistry,
    PostgresRunTokenBudgetStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.persistence.repair_completion import RepairAwarePostgresMultiTaskCompletionStore
from app.providers.budgeted import BudgetedAgentDriver
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
from app.verification import DeterministicVerifier
from app.verification.project_profile import ProjectAwareVerificationRunner
from app.workers.executor import ManagedProjectWorkspaceResolver, QueuedTaskWorker
from app.workers.lease import LeasedQueuedTaskWorker
from app.workers.project_identity import ProjectIdentityValidatingQueuedTaskWorker
from app.workflows import (
    DeterministicWorkflowRunner,
    WorkflowAwareTaskRunner,
    WorkflowMatcher,
    WorkflowRegistry,
)
from app.workspace import ManagedProjectProvisioner


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
        command_runner=ProjectAwareVerificationRunner(
            base_policy=policy,
            node_image=settings.verification_node_sandbox_image,
            cache_root=settings.workspace_root / "verification-deps",
            proxy_url=settings.dependency_proxy_url,
            python_index_url=settings.dependency_python_index_url,
            node_registry_url=settings.dependency_node_registry_url,
            build_timeout_seconds=settings.dependency_preflight_build_timeout_seconds,
            max_cache_bytes=settings.dependency_cache_max_bytes,
        ),
    )


def build_single_task_runner(
    settings: Settings,
    *,
    driver: SiliconFlowDriver | BudgetedAgentDriver | None = None,
) -> SingleTaskOrchestrator:
    driver = driver or SiliconFlowDriver.from_settings(settings)
    developer = DeveloperAgent(
        driver=driver,
        model=settings.developer_model,
        max_iterations=settings.developer_max_iterations,
        max_duration_seconds=settings.developer_max_duration_seconds,
        max_model_turn_seconds=settings.developer_max_model_turn_seconds,
        max_output_tokens=settings.developer_max_output_tokens,
        invalid_tool_retry_max_output_tokens=settings.developer_invalid_tool_retry_max_output_tokens,
        enable_thinking=settings.developer_enable_thinking,
        context_compaction_enabled=settings.context_compaction_enabled,
        role_context_projection_enabled=settings.role_context_projection_enabled,
        max_retained_tool_groups=settings.developer_max_retained_tool_groups,
        max_single_tool_result_tokens=settings.developer_max_single_tool_result_tokens,
        max_tool_results_per_turn_tokens=settings.developer_max_tool_results_per_turn_tokens,
        runtime_v3_enabled=(
            settings.agent_runtime_v3_enabled and settings.developer_runtime_v3_enabled
        ),
        runtime_mutation_gate_enabled=settings.runtime_mutation_gate_enabled,
        runtime_repo_map_enabled=settings.runtime_repo_map_enabled,
        runtime_event_condenser_enabled=settings.runtime_event_condenser_enabled,
        runtime_stuck_detector_enabled=settings.runtime_stuck_detector_enabled,
        openhands_patch_enabled=settings.openhands_patch_enabled,
    )
    reviewer = ReviewerAgent(
        driver=driver,
        model=settings.reviewer_model,
        max_output_tokens=settings.reviewer_max_output_tokens,
        enable_thinking=settings.reviewer_enable_thinking,
        role_context_projection_enabled=settings.role_context_projection_enabled,
    )
    repair = RepairAgent(
        driver=driver,
        model=settings.repair_model,
        max_iterations=settings.repair_max_iterations,
        max_duration_seconds=settings.repair_max_duration_seconds,
        max_model_turn_seconds=settings.repair_max_model_turn_seconds,
        max_output_tokens=settings.repair_max_output_tokens,
        enable_thinking=settings.repair_enable_thinking,
        context_compaction_enabled=settings.context_compaction_enabled,
        role_context_projection_enabled=settings.role_context_projection_enabled,
        max_single_tool_result_tokens=settings.repair_max_single_tool_result_tokens,
        max_tool_results_per_turn_tokens=settings.repair_max_tool_results_per_turn_tokens,
        max_read_range_lines=settings.repair_max_read_range_lines,
        runtime_v3_enabled=(
            settings.agent_runtime_v3_enabled and settings.repair_runtime_v3_enabled
        ),
        runtime_mutation_gate_enabled=settings.runtime_mutation_gate_enabled,
        runtime_import_prefetch_enabled=settings.runtime_import_prefetch_enabled,
        runtime_event_condenser_enabled=settings.runtime_event_condenser_enabled,
        runtime_stuck_detector_enabled=settings.runtime_stuck_detector_enabled,
        openhands_patch_enabled=settings.openhands_patch_enabled,
    )
    return SingleTaskOrchestrator(
        developer=developer,
        verifier=build_verifier(settings),
        reviewer=reviewer,
        repair=repair,
        developer_model=settings.developer_model,
        reviewer_model=settings.reviewer_model,
        repair_model=settings.repair_model,
        minimum_repair_attempts=settings.minimum_repair_attempts,
    )


def build_runner_factory(settings: Settings) -> Callable[[TaskContract], WorkflowAwareTaskRunner]:
    matcher = WorkflowMatcher(WorkflowRegistry.default())

    def factory(_task: TaskContract) -> WorkflowAwareTaskRunner:
        return WorkflowAwareTaskRunner(
            matcher=matcher,
            workflow_runner=DeterministicWorkflowRunner(
                matcher=matcher,
                verifier=build_verifier(settings),
                estimated_tokens_saved=settings.developer_max_output_tokens,
            ),
            agent_runner_factory=lambda: build_single_task_runner(settings),
            activation_mode=settings.workflow_activation_mode,
        )

    return factory


def build_budgeted_runner_factory(
    settings: Settings,
    *,
    budget_store: PostgresRunTokenBudgetStore,
) -> Callable[[TaskContract, UUID], WorkflowAwareTaskRunner]:
    matcher = WorkflowMatcher(WorkflowRegistry.default())

    def factory(task: TaskContract, run_id: UUID) -> WorkflowAwareTaskRunner:
        workflow_runner = DeterministicWorkflowRunner(
            matcher=matcher,
            verifier=build_verifier(settings),
            estimated_tokens_saved=settings.developer_max_output_tokens,
        )

        def build_agent_runner() -> SingleTaskOrchestrator:
            raw_driver = SiliconFlowDriver.from_settings(settings)
            return build_single_task_runner(
                settings,
                driver=BudgetedAgentDriver(
                    driver=raw_driver,
                    budget_store=budget_store,
                    run_id=run_id,
                    task_id=task.task_id,
                    token_estimator=TokenEstimator(
                        safety_factor=settings.token_estimate_safety_factor
                    ),
                ),
            )

        return WorkflowAwareTaskRunner(
            matcher=matcher,
            workflow_runner=workflow_runner,
            agent_runner_factory=build_agent_runner,
            activation_mode=settings.workflow_activation_mode,
        )

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
    token_budget_store = PostgresRunTokenBudgetStore.from_url(
        settings.database_url,
        default_total_budget_tokens=settings.run_token_budget_tokens,
        adaptive_package_budget_enabled=settings.adaptive_package_budget_enabled,
        token_estimate_safety_factor=settings.token_estimate_safety_factor,
        echo=settings.database_echo,
    )
    interface_contract_registry = PostgresInterfaceContractRegistry.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    completion_store = RepairAwarePostgresMultiTaskCompletionStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    task_reconciler = None
    try:
        repository_root = settings.workspace_root / "repos"
        resolver = ManagedProjectWorkspaceResolver(repository_root)
        provisioner = ManagedProjectProvisioner(
            repository_root,
            git_timeout_seconds=settings.git_clone_timeout_seconds,
            read_token=settings.github_read_token,
        )
        execution_base_resolver = RepairAwareEvidenceBoundTaskExecutionBaseResolver(
            dag_reader=dag_store,
            workspace_resolver=resolver,
        )
        backend = TraceAwareLocalQueuedTaskExecutionBackend(
            workspace_resolver=resolver,
            worktree_root=settings.workspace_root / "worktrees",
            runner_factory=build_runner_factory(settings),
            runner_factory_for_execution=build_budgeted_runner_factory(
                settings,
                budget_store=token_budget_store,
            ),
            git_fence=lease_store,
            trace_store=evidence_store,
        )
        identity_validated_worker = ProjectIdentityValidatingQueuedTaskWorker(
            worker=QueuedTaskWorker(
                store=evidence_store,
                backend=backend,
                execution_base_resolver=execution_base_resolver,
                continuation_max_slices=settings.continuation_max_slices,
                continuation_total_budget_seconds=settings.continuation_total_budget_seconds,
                continuation_max_repeated_file_slices=settings.continuation_max_repeated_file_slices,
                interface_contract_registry=(
                    interface_contract_registry
                    if settings.work_package_activation_mode
                    is WorkPackageActivationMode.CONTRACT_GATED
                    else None
                ),
                dag_reader=dag_store,
                token_budget_reader=token_budget_store,
                token_budget_manager=token_budget_store,
            ),
            run_store=evidence_store,
            provisioner=provisioner,
        )
        queued_worker = TraceAwareQueuedTaskWorker(identity_validated_worker)
        leased_worker = LeasedQueuedTaskWorker(
            worker=queued_worker,
            lease_store=lease_store,
            worker_id=resolve_worker_id(settings),
            lease_seconds=settings.worker_lease_seconds,
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        )
        result = await leased_worker.execute(envelope)

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
            max_concurrent_tasks=settings.dag_max_concurrent_tasks,
            interface_contract_registry=(
                interface_contract_registry
                if settings.work_package_activation_mode is WorkPackageActivationMode.CONTRACT_GATED
                else None
            ),
        )
        driver = SiliconFlowDriver.from_settings(settings)
        conflict_repairer = IntegrationConflictRepairService(
            evidence_store=evidence_store,
            repair_store=completion_store,
            workspace_resolver=resolver,
            developer=DeveloperAgent(
                driver=BudgetedAgentDriver(
                    driver=driver,
                    budget_store=token_budget_store,
                    run_id=envelope.run_id,
                    task_id="integration",
                    token_estimator=TokenEstimator(
                        safety_factor=settings.token_estimate_safety_factor
                    ),
                ),
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
                runtime_event_condenser_enabled=settings.runtime_event_condenser_enabled,
                openhands_patch_enabled=settings.openhands_patch_enabled,
            ),
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
        if task_reconciler is None:
            await reconciliation_store.dispose()
        else:
            await task_reconciler.dispose()
        await completion_store.dispose()
        await lease_store.dispose()
        await dag_store.dispose()
        await token_budget_store.dispose()
        await interface_contract_registry.dispose()
        await evidence_store.dispose()