from collections.abc import Sequence

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.review import ReviewDecision, ReviewOutcome
from app.models.verification import VerificationResult
from app.verification import DeterministicVerifier

_REPAIRABLE_FAILURES = {
    FailureType.TEST_FAILURE,
    FailureType.LINT_FAILURE,
    FailureType.REVIEW_REJECTED,
}


class FailureClassifier:
    """Convert verification/review evidence into targeted repair inputs."""

    @staticmethod
    def from_verification(result: VerificationResult) -> list[FailureReport]:
        return DeterministicVerifier.failure_reports(result)

    @staticmethod
    def from_review(decision: ReviewDecision) -> list[FailureReport]:
        if decision.decision is ReviewOutcome.PASS:
            return []

        evidence = [f"review_summary={decision.summary}"]
        for index, issue in enumerate(decision.issues, start=1):
            location = issue.file or "unknown"
            if issue.line is not None:
                location = f"{location}:{issue.line}"
            evidence.append(
                f"issue_{index}=severity:{issue.severity.value};location:{location};"
                f"message:{issue.message}"
            )

        return [
            FailureReport(
                failure_type=FailureType.REVIEW_REJECTED,
                source=FailureSource.REVIEW,
                message="Independent semantic review requested targeted changes.",
                retryable=True,
                evidence=evidence,
            )
        ]

    @staticmethod
    def repairable(reports: Sequence[FailureReport]) -> list[FailureReport]:
        return [
            report
            for report in reports
            if report.retryable and report.failure_type in _REPAIRABLE_FAILURES
        ]

    @staticmethod
    def terminalize(
        reports: Sequence[FailureReport],
        *,
        max_retries: int,
    ) -> list[FailureReport]:
        return [
            report.model_copy(
                update={
                    "retryable": False,
                    "message": f"{report.message} Repair retry budget was exhausted.",
                    "evidence": [
                        *report.evidence,
                        f"repair_attempts_exhausted={max_retries}",
                    ],
                }
            )
            for report in reports
        ]
