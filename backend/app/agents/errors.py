from app.models.failure import FailureReport


class InvalidPlannerOutputError(Exception):
    """Raised when Planner output remains invalid after bounded schema repair."""

    def __init__(self, failure: FailureReport) -> None:
        self.failure = failure
        super().__init__(failure.message)


class InvalidReviewerOutputError(Exception):
    """Raised when Reviewer output remains invalid after bounded schema repair."""

    def __init__(self, failure: FailureReport) -> None:
        self.failure = failure
        super().__init__(failure.message)


class RepairBudgetExhaustedError(Exception):
    """Raised before model execution when no repair attempts remain."""

    def __init__(self, failures: list[FailureReport]) -> None:
        self.failures = failures
        super().__init__("repair retry budget was exhausted")
