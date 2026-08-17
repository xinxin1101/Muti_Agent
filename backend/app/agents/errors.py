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
