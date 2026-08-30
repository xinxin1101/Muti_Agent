from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.dag import TaskDAG, TaskNode
from app.models.task import TaskContract
from app.models.work_package import (
    InterfaceContract,
    PlanningComplexity,
    PlanningComplexityAssessment,
    TaskBudgetAllocation,
    WorkPackage,
    WorkPackagePlan,
    WorkPackageRoutingAudit,
    WorkPackageRoutingMode,
)

_DELIVERY_LAYERS = {
    "ui": re.compile(r"\b(ui|frontend|react|vue|html|css)\b|前端|界面", re.IGNORECASE),
    "data": re.compile(r"\b(database|storage|model|schema)\b|数据库|存储|模型", re.IGNORECASE),
    "algorithm": re.compile(
        r"\b(ai|algorithm|search|heuristic)\b|人工智能|算法|搜索", re.IGNORECASE
    ),
    "interface": re.compile(r"\b(api|endpoint|route|server)\b|接口|路由|服务端", re.IGNORECASE),
}
_CORE_DELIVERABLE = re.compile(
    r"\b(core|domain|model|algorithm|logic)\b|核心|模型|算法|逻辑", re.IGNORECASE
)
_INTERFACE_DELIVERABLE = re.compile(
    r"\b(ui|api|route|interface|frontend|server)\b|界面|接口|前端|路由|服务端",
    re.IGNORECASE,
)
_INTEGRATION_DELIVERABLE = re.compile(
    r"\b(test|integration|verify)\b|测试|集成|验证", re.IGNORECASE
)


class WorkPackagePlanError(ValueError):
    """A bounded, actionable reason a Planner proposal cannot become executable work."""


@dataclass(frozen=True)
class WorkPackagePlanningResult:
    dag: TaskDAG
    interface_contracts: tuple[InterfaceContract, ...]
    budget_allocations: tuple[TaskBudgetAllocation, ...]
    complexity_assessments: tuple[PlanningComplexityAssessment, ...]
    routing_audit: WorkPackageRoutingAudit


class WorkPackagePlanValidator:
    """Apply deterministic package boundaries before accepting a Planner proposal."""

    def __init__(self, *, adaptive_routing_enabled: bool = True) -> None:
        self._adaptive_routing_enabled = adaptive_routing_enabled

    def validate_and_convert(
        self,
        plan: WorkPackagePlan,
        *,
        requirement: str,
        max_tasks: int,
    ) -> WorkPackagePlanningResult:
        if len(plan.packages) > max_tasks:
            raise WorkPackagePlanError(
                f"WorkPackagePlan contains {len(plan.packages)} packages; maximum is {max_tasks}."
            )
        delivery_layers = self._delivery_layers(requirement)
        if (
            not self._adaptive_routing_enabled
            and len(requirement.strip()) > 180
            and len(plan.packages) < 3
        ):
            raise WorkPackagePlanError(
                "Legacy routing requires a long requirement to be split into at least three "
                "packages."
            )
        produced_by = {
            interface: package.package_id
            for package in plan.packages
            for interface in package.produces
        }
        for package in plan.packages:
            self._validate_single_deliverable(package)
            for interface in package.consumes:
                if interface.startswith("repository:"):
                    continue
                if interface not in produced_by:
                    raise WorkPackagePlanError(
                        f"{package.package_id} consumes undeclared interface {interface!r}."
                    )
                if produced_by[interface] == package.package_id:
                    raise WorkPackagePlanError(
                        f"{package.package_id} cannot consume its own produced interface "
                        f"{interface!r}."
                    )

        assessments = {package.package_id: self._assessment(package) for package in plan.packages}
        tasks = tuple(
            TaskNode(
                task=TaskContract(
                    task_id=package.package_id,
                    objective=package.objective,
                    readable_files=list(package.readable_paths),
                    writable_files=list(package.owned_paths),
                    readonly_files=[],
                    acceptance_criteria=list(package.acceptance_criteria),
                    verification_commands=list(package.verification_commands),
                    max_retries=(
                        2 if package.estimated_complexity is not PlanningComplexity.LOW else 1
                    ),
                ),
                depends_on=tuple(
                    sorted(
                        {
                            produced_by[interface]
                            for interface in package.consumes
                            if interface in produced_by
                        }
                    )
                ),
                produces=package.produces,
                consumes=package.consumes,
                complexity=package.estimated_complexity,
                budget_allocation=TaskBudgetAllocation(
                    package_id=package.package_id,
                    recommended_token_budget=package.recommended_token_budget,
                ),
                complexity_assessment=assessments[package.package_id],
            )
            for package in plan.packages
        )
        try:
            dag = TaskDAG(tasks=tasks)
        except ValueError as exc:
            raise WorkPackagePlanError(str(exc)) from exc
        return WorkPackagePlanningResult(
            dag=dag,
            interface_contracts=tuple(
                InterfaceContract(
                    interface_id=interface,
                    producer_package_id=producer,
                    consumer_package_ids=tuple(
                        package.package_id
                        for package in plan.packages
                        if interface in package.consumes
                    ),
                )
                for interface, producer in sorted(produced_by.items())
            ),
            budget_allocations=tuple(
                TaskBudgetAllocation(
                    package_id=package.package_id,
                    recommended_token_budget=package.recommended_token_budget,
                )
                for package in plan.packages
            ),
            complexity_assessments=tuple(
                assessments[package.package_id] for package in plan.packages
            ),
            routing_audit=self._routing_audit(plan, delivery_layers),
        )

    @staticmethod
    def _delivery_layers(requirement: str) -> tuple[str, ...]:
        """Return explicit delivery concerns; prose length is deliberately ignored."""

        text = requirement.strip()
        return tuple(
            name for name, pattern in _DELIVERY_LAYERS.items() if pattern.search(text)
        )

    @staticmethod
    def _routing_audit(
        plan: WorkPackagePlan,
        delivery_layers: tuple[str, ...],
    ) -> WorkPackageRoutingAudit:
        owned_roots = {
            path.split("/", 1)[0]
            for package in plan.packages
            for path in package.owned_paths
            if "/" in path
        }
        dependencies = sum(len(package.consumes) for package in plan.packages)
        verification_sets = {
            tuple(sorted(package.verification_commands)) for package in plan.packages
        }
        parallel_packages = sum(not package.consumes for package in plan.packages)
        readable_by_package = [set(package.readable_paths) for package in plan.packages]
        overlap = sum(
            bool(left & right)
            for index, left in enumerate(readable_by_package)
            for right in readable_by_package[index + 1 :]
        )
        reasons = [f"delivery_layers={','.join(delivery_layers) or 'none'}"]
        reasons.append(f"owned_path_roots={len(owned_roots)}")
        reasons.append(f"declared_interface_dependencies={dependencies}")
        reasons.append(f"independent_verification_sets={len(verification_sets)}")
        reasons.append(f"parallel_candidates={parallel_packages}; readable_overlap={overlap}")
        if len(plan.packages) == 1:
            reasons.append("one bounded ownership/interface boundary; kept as a single package")
            mode = WorkPackageRoutingMode.SINGLE
        else:
            reasons.append("Planner declared independent ownership/interface boundaries")
            mode = WorkPackageRoutingMode.MULTI
        return WorkPackageRoutingAudit(
            mode=mode,
            reasons=tuple(reasons),
            delivery_layers=delivery_layers,
            package_count=len(plan.packages),
        )

    @staticmethod
    def _validate_single_deliverable(package: WorkPackage) -> None:
        text = f"{package.objective}\n{package.deliverable}"
        layers = [name for name, pattern in _DELIVERY_LAYERS.items() if pattern.search(text)]
        if len(layers) >= 2:
            raise WorkPackagePlanError(
                f"{package.package_id} combines multiple delivery layers ({', '.join(layers)}); "
                "split it into one primary deliverable per work package."
            )

    @staticmethod
    def _validate_required_package_roles(plan: WorkPackagePlan) -> None:
        texts = tuple(f"{item.objective}\n{item.deliverable}" for item in plan.packages)
        has_core = any(_CORE_DELIVERABLE.search(text) for text in texts)
        has_interface = any(_INTERFACE_DELIVERABLE.search(text) for text in texts)
        has_integration = any(_INTEGRATION_DELIVERABLE.search(text) for text in texts)
        if not (has_core and has_interface and has_integration):
            raise WorkPackagePlanError(
                "A multi-layer requirement must include distinct core, interface, and "
                "test/integration work packages."
            )

    @staticmethod
    def _assessment(package: WorkPackage) -> PlanningComplexityAssessment:
        score = len(package.owned_paths) * 5 + len(package.consumes) * 4
        score += len(package.verification_commands) * 3
        declared_weight = {
            PlanningComplexity.LOW: 5,
            PlanningComplexity.MEDIUM: 20,
            PlanningComplexity.HIGH: 40,
        }
        score += declared_weight[package.estimated_complexity]
        reasons = (
            f"owned_paths={len(package.owned_paths)}",
            f"consumes={len(package.consumes)}",
            f"verification_commands={len(package.verification_commands)}",
            f"declared_complexity={package.estimated_complexity.value}",
        )
        return PlanningComplexityAssessment(
            package_id=package.package_id,
            complexity=package.estimated_complexity,
            score=score,
            reasons=reasons,
        )
