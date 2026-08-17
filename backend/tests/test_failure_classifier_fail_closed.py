from app import models
from app.runtime import FailureClassifier


def test_mixed_repairable_and_safety_failures_do_not_enter_repair() -> None:
    test_failure = models.FailureReport(
        failure_type=models.FailureType.TEST_FAILURE,
        source=models.FailureSource.VERIFICATION,
        message="Tests failed.",
        retryable=True,
        evidence=["pytest failed"],
    )
    scope_failure = models.FailureReport(
        failure_type=models.FailureType.SCOPE_VIOLATION,
        source=models.FailureSource.VERIFICATION,
        message="Scope failed.",
        retryable=False,
        evidence=["protected test changed"],
    )

    assert FailureClassifier.repairable([test_failure, scope_failure]) == []
