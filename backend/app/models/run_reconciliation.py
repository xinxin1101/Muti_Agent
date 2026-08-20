from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.dispatch import WorkerExecutionStatus
from app.models.lease import TaskLeaseState
from app.models.reconciliation import TaskReconciliationOutcome


class TaskExecutionBaseBasis(StrEnum):
    """Durable evidence basis for one queued task's Git starting commit."""

    RUN_BASE = "RUN_BASE"
    MERGE_QUEUE_SNAPSHOT = "MERGE_QUEUE_SNAPSHOT"


class TaskExecutionBase(BaseModel):
    """Evidence-bound Git base selected without trusting a queue/browser supplied SHA."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    basis: TaskExecutionBaseBasis
    source_evidence_id: int | None = Field(default=None, ge=1)
    source_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    integration_ref: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_provenance(self) -> TaskExecutionBase:
        provenance = (
            self.source_evidence_id,
            self.source_evidence_sha256,
            self.integration_ref,
        )
        if self.basis is TaskExecutionBaseBasis.RUN_BASE:
            if any(value is not None for value in provenance):
                raise ValueError("RUN_BASE execution bases must not claim merge evidence")
            return self
        if any(value is None for value in provenance):
            raise ValueError("MERGE_QUEUE_SNAPSHOT execution bases require complete provenance")
        return self


class DAGTaskFrontierState(StrEnum):
    """Derived recovery-frontier state; never an independently persisted scheduler state."""

    RUN_TERMINAL = "RUN_TERMINAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED_UPSTREAM_FAILURE = "BLOCKED_UPSTREAM_FAILURE"
    WAIT_DEPENDENCIES = "WAIT_DEPENDENCIES"
    WAIT_ACTIVE_OWNER = "WAIT_ACTIVE_OWNER"
    BLOCKED_RECOVERY_GAP = "BLOCKED_RECOVERY_GAP"
    WAIT_INTEGRATION_BASE = "WAIT_INTEGRATION_BASE"
    RECONCILE_CANDIDATE = "RECONCILE_CANDIDATE"


class DAGTaskReconciliationRecord(BaseModel):
    """One task's derived position in a durable DAG reconciliation frontier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    topological_index: int = Field(ge=0)
    frontier_state: DAGTaskFrontierState
    lease_state: TaskLeaseState
    lease_generation: int = Field(ge=0)
    lease_dispatch_id: UUID | None = None
    worker_execution_status: WorkerExecutionStatus | None = None
    worker_execution_evidence_id: int | None = Field(default=None, ge=1)
    execution_base: TaskExecutionBase | None = None
    reason: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_record(self) -> DAGTaskReconciliationRecord:
        if (self.worker_execution_status is None) != (
            self.worker_execution_evidence_id is None
        ):
            raise ValueError("worker terminal status and evidence id must appear together")
        if self.execution_base is not None:
            if (
                self.execution_base.run_id != self.run_id
                or self.execution_base.task_id != self.task_id
            ):
                raise ValueError("execution-base identity must match the frontier task")
        if self.frontier_state is DAGTaskFrontierState.SUCCEEDED:
            if self.worker_execution_status is not WorkerExecutionStatus.SUCCEEDED:
                raise ValueError("SUCCEEDED frontier tasks require successful worker evidence")
        elif self.frontier_state is DAGTaskFrontierState.FAILED:
            if self.worker_execution_status is not WorkerExecutionStatus.FAILED:
                raise ValueError("FAILED frontier tasks require failed worker evidence")
        elif self.frontier_state is DAGTaskFrontierState.RECONCILE_CANDIDATE:
            if self.execution_base is None:
                raise ValueError("reconcile candidates require an evidence-bound execution base")
            if self.worker_execution_status is not None:
                raise ValueError("reconcile candidates must not already be terminal")
        return self


class DAGRunReconciliationPlan(BaseModel):
    """Read projection of the legal DAG recovery frontier at one observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    run_status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    dag_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    topology_source: Literal["PERSISTED", "IMPLICIT_SINGLE_TASK"]
    observed_at: datetime
    topological_order: tuple[str, ...] = Field(min_length=1)
    completed_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    failed_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    blocked_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    ready_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    reconcile_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    tasks: tuple[DAGTaskReconciliationRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan(self) -> DAGRunReconciliationPlan:
        order = self.topological_order
        if len(order) != len(set(order)):
            raise ValueError("topological_order must contain unique task ids")
        if tuple(item.task_id for item in self.tasks) != order:
            raise ValueError("frontier task records must follow the complete topological order")
        if any(item.run_id != self.run_id for item in self.tasks):
            raise ValueError("frontier task records must belong to the plan Run")

        known = set(order)
        named_sets = {
            "completed": set(self.completed_task_ids),
            "failed": set(self.failed_task_ids),
            "blocked": set(self.blocked_task_ids),
            "ready": set(self.ready_task_ids),
            "reconcile": set(self.reconcile_task_ids),
        }
        for label, task_ids in named_sets.items():
            if len(task_ids) != len(getattr(self, f"{label}_task_ids")):
                raise ValueError(f"{label} task ids must be unique")
            unknown = task_ids - known
            if unknown:
                raise ValueError(f"{label} task ids are not present in the DAG")
        if named_sets["completed"] & named_sets["failed"]:
            raise ValueError("completed and failed task ids must not overlap")
        if named_sets["reconcile"] - named_sets["ready"]:
            raise ValueError("only DAG-ready tasks may become reconciliation candidates")
        return self


class DAGRunReconciliationOutcome(BaseModel):
    """One frontier plan plus the Step 5.3 outcomes delegated from that plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: DAGRunReconciliationPlan
    task_outcomes: tuple[TaskReconciliationOutcome, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_outcomes(self) -> DAGRunReconciliationOutcome:
        task_ids = tuple(item.decision.task_id for item in self.task_outcomes)
        if task_ids != self.plan.reconcile_task_ids:
            raise ValueError(
                "successful DAG reconciliation must return one Step 5.3 outcome per candidate"
            )
        return self
