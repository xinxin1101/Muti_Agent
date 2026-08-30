from app.context.token_estimator import TokenEstimator


def test_context_window_units_are_not_reused_as_billable_token_reservation() -> None:
    estimator = TokenEstimator()
    source = "x" * 32_000

    assert estimator.context_window_units(source) == 32_000
    assert estimator.billable_token_estimate(source) < 12_000


def test_billable_estimate_handles_cjk_code_and_mixed_content() -> None:
    estimator = TokenEstimator()

    chinese = estimator.billable_token_estimate("实现五子棋游戏" * 20)
    code = estimator.billable_token_estimate("def hello():\n    return 'world'\n" * 20)
    mixed = estimator.billable_token_estimate("实现 hello world: print('你好')" * 20)

    assert chinese > 0
    assert code > 0
    assert mixed > 0
    assert mixed < estimator.context_window_units("实现 hello world: print('你好')" * 20)
