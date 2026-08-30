import pytest

from app.models.work_package import PlanningComplexity, WorkPackage, WorkPackagePlan
from app.planning.work_packages import WorkPackagePlanError, WorkPackagePlanValidator


def _package(**updates) -> WorkPackage:
    values = {
        "package_id": "core-model",
        "objective": "实现棋盘状态模型。",
        "deliverable": "棋盘状态模块",
        "owned_paths": ("gomoku/core.py",),
        "readable_paths": (),
        "produces": ("gomoku.core.GomokuGame",),
        "consumes": (),
        "acceptance_criteria": ("GomokuGame 提供 15x15 棋盘。",),
        "verification_commands": ("pytest tests/test_core.py -q",),
        "estimated_complexity": PlanningComplexity.MEDIUM,
        "recommended_token_budget": 6000,
    }
    values.update(updates)
    return WorkPackage(**values)


def test_converter_derives_dependency_and_interface_contract() -> None:
    core = _package()
    api = _package(
        package_id="game-api",
        objective="实现游戏 HTTP 接口。",
        deliverable="游戏 API 路由",
        owned_paths=("app/routes/game.py",),
        readable_paths=("gomoku/core.py",),
        produces=("app.routes.game.GameRouter",),
        consumes=("gomoku.core.GomokuGame",),
        acceptance_criteria=("创建游戏接口返回棋盘数据。",),
        verification_commands=("pytest tests/test_game_api.py -q",),
    )

    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(packages=(core, api)),
        requirement="实现核心逻辑和 API。",
        max_tasks=8,
    )

    assert result.dag.node("game-api").depends_on == ("core-model",)
    assert result.dag.node("core-model").produces == ("gomoku.core.GomokuGame",)
    assert result.dag.node("game-api").consumes == ("gomoku.core.GomokuGame",)
    assert result.dag.node("core-model").budget_allocation is not None
    assert result.dag.node("core-model").complexity is PlanningComplexity.MEDIUM
    core_contract = next(
        item for item in result.interface_contracts if item.interface_id == "gomoku.core.GomokuGame"
    )
    assert core_contract.consumer_package_ids == ("game-api",)
    assert result.budget_allocations[0].recommended_token_budget == 6000


def test_converter_rejects_unproduced_consumed_interface() -> None:
    plan = WorkPackagePlan(packages=(_package(consumes=("missing.Interface",)),))

    with pytest.raises(WorkPackagePlanError, match="undeclared interface"):
        WorkPackagePlanValidator().validate_and_convert(
            plan,
            requirement="实现核心模型。",
            max_tasks=8,
        )


def test_converter_rejects_cross_layer_delivery_in_one_package() -> None:
    plan = WorkPackagePlan(
        packages=(
            _package(
                objective="实现 UI 和 API。",
                deliverable="界面和服务端接口",
            ),
        )
    )

    with pytest.raises(WorkPackagePlanError, match="multiple delivery layers"):
        WorkPackagePlanValidator().validate_and_convert(
            plan,
            requirement="实现界面和接口。",
            max_tasks=8,
        )


def test_multi_layer_requirement_requires_core_interface_and_integration_packages() -> None:
    plan = WorkPackagePlan(packages=(_package(), _package(package_id="api", produces=("app.Api",))))

    with pytest.raises(WorkPackagePlanError, match="core, interface, and test/integration"):
        WorkPackagePlanValidator().validate_and_convert(
            plan,
            requirement="实现一个包含 UI、API 和数据库的游戏。",
            max_tasks=8,
        )


def test_multi_layer_requirement_rejects_three_packages_without_integration_role() -> None:
    core = _package()
    interface = _package(
        package_id="game-api",
        objective="实现游戏 API。",
        deliverable="游戏接口路由",
        owned_paths=("app/routes/game.py",),
        produces=("app.GameApi",),
    )
    extra = _package(
        package_id="game-ui",
        objective="实现游戏 UI。",
        deliverable="游戏界面组件",
        owned_paths=("web/game.ts",),
        produces=("web.GameView",),
    )

    with pytest.raises(WorkPackagePlanError, match="core, interface, and test/integration"):
        WorkPackagePlanValidator().validate_and_convert(
            WorkPackagePlan(packages=(core, interface, extra)),
            requirement="实现一个包含 UI、API 和数据库的游戏。",
            max_tasks=8,
        )
