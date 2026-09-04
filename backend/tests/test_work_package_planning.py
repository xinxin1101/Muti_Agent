import pytest

from app.models.work_package import (
    PlanningComplexity,
    WorkPackage,
    WorkPackagePlan,
    WorkPackageRoutingMode,
)
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
    assert result.routing_audit.mode is WorkPackageRoutingMode.MULTI
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


def test_cross_layer_single_subsystem_remains_single_package() -> None:
    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(
            packages=(
                _package(
                    package_id="auth",
                    objective="实现认证模块。",
                    deliverable="认证服务",
                    owned_paths=("app/auth.py", "tests/test_auth.py"),
                    produces=("app.auth.authenticate",),
                    verification_commands=("pytest tests/test_auth.py -q",),
                ),
            )
        ),
        requirement="实现 auth.py 与 test_auth.py，包含数据库访问和 API 描述。",
        max_tasks=8,
    )

    assert result.routing_audit.mode is WorkPackageRoutingMode.SINGLE


def test_long_single_deliverable_requirement_does_not_force_multi_package() -> None:
    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(packages=(_package(),)),
        requirement="编写一个 Python 命令行脚本，" * 80,
        max_tasks=8,
    )

    assert result.routing_audit.mode is WorkPackageRoutingMode.SINGLE
    assert result.routing_audit.delivery_layers == ()


def test_independent_subsystems_with_interface_remain_multi_package() -> None:
    core = _package()
    interface = _package(
        package_id="game-api",
        objective="实现游戏 API。",
        deliverable="游戏接口路由",
        owned_paths=("app/routes/game.py",),
        produces=("app.GameApi",),
        consumes=("gomoku.core.GomokuGame",),
    )
    extra = _package(
        package_id="game-ui",
        objective="实现游戏 UI。",
        deliverable="游戏界面组件",
        owned_paths=("web/game.ts",),
        produces=("web.GameView",),
        consumes=("app.GameApi",),
    )

    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(packages=(core, interface, extra)),
        requirement="实现独立核心、API 接口和前端页面，并通过明确接口连接。",
        max_tasks=8,
    )

    assert result.routing_audit.mode is WorkPackageRoutingMode.MULTI
    assert any("declared_interface_dependencies=" in item for item in result.routing_audit.reasons)



def test_converter_keeps_required_outputs_separate_from_owned_paths() -> None:
    package = _package(
        owned_paths=("gomoku/**",),
        required_output_files=("gomoku/core.py",),
    )

    result = WorkPackagePlanValidator().validate_and_convert(
        WorkPackagePlan(packages=(package,)),
        requirement="实现核心模型。",
        max_tasks=8,
    )

    task = result.dag.node("core-model").task
    assert task.writable_files == ["gomoku/**"]
    assert task.required_output_files == ["gomoku/core.py"]


def test_work_package_rejects_required_output_outside_owned_scope() -> None:
    with pytest.raises(ValueError, match="outside owned path scope"):
        _package(required_output_files=("other/core.py",))
