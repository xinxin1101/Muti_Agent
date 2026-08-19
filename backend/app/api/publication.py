from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.api.models import ProductGitHubPublication
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.publication import (
    GitHubPublicationIntent,
    GitHubPublicationSourceBasis,
    GitHubPublicationState,
    PersistedGitHubPublication,
)
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.serialization import canonical_payload
from app.persistence.types import (
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistenceEvidenceKind,
)
from app.publication import GitHubPublicationGatewayError, parse_github_repository_url
from app.workspace import CommitDiffError, LocalGitWorkspace, ReadOnlyCommitDiffReader


class ProductGitHubPublicationUnavailableError(RuntimeError):
    """Raised when accepted runtime/Git facts do not define a publishable source."""


@dataclass(frozen=True)
class _PublicationSource:
    basis: GitHubPublicationSourceBasis
    commit: str
    evidence_id: int
    evidence_sha256: str


def resolve_github_publication_intent(
    snapshot: PersistedRunSnapshot,
    workspace: LocalGitWorkspace,
) -> GitHubPublicationIntent:
    """Resolve exactly one source commit from already accepted terminal Run evidence."""

    if snapshot.status is not PersistedRunStatus.SUCCEEDED:
        raise ProductGitHubPublicationUnavailableError(
            "GitHub publication requires an already-SUCCEEDED persisted Run"
        )

    try:
        owner, repo = parse_github_repository_url(snapshot.repository_url)
    except GitHubPublicationGatewayError as exc:
        raise ProductGitHubPublicationUnavailableError(exc.public_message) from exc

    source = _integration_source(snapshot, workspace)
    if source is None:
        if len(snapshot.tasks) != 1:
            raise ProductGitHubPublicationUnavailableError(
                "multi-task GitHub publication requires complete accepted integration evidence"
            )
        source = _single_task_source(snapshot, workspace)

    return GitHubPublicationIntent(
        run_id=snapshot.run_id,
        project_id=snapshot.project_id,
        repository_url=snapshot.repository_url,
        repository_slug=f"{owner}/{repo}",
        base_branch=snapshot.default_branch,
        branch_name=f"devflow/run-{snapshot.run_id}",
        source_basis=source.basis,
        source_commit=source.commit,
        source_evidence_id=source.evidence_id,
        source_evidence_sha256=source.evidence_sha256,
    )


def build_product_publication(
    intent: GitHubPublicationIntent,
    persisted: PersistedGitHubPublication | None,
    *,
    publisher_configured: bool,
) -> ProductGitHubPublication:
    if persisted is not None:
        _, expected_digest = canonical_payload(intent)
        if persisted.intent_sha256 != expected_digest or persisted.intent != intent:
            raise PersistenceCorruptionError(
                "persisted GitHub publication audit disagrees with current accepted source facts"
            )
        state = persisted.state
        attempt_count = persisted.attempt_count
        pull_request_number = persisted.pull_request_number
        pull_request_url = persisted.pull_request_url
        pull_request_state = persisted.pull_request_state
        pull_request_draft = persisted.pull_request_draft
        last_error_code = persisted.last_error_code
        last_error_message = persisted.last_error_message
    else:
        state = GitHubPublicationState.READY
        attempt_count = 0
        pull_request_number = None
        pull_request_url = None
        pull_request_state = None
        pull_request_draft = None
        last_error_code = None
        last_error_message = None

    return ProductGitHubPublication(
        run_id=intent.run_id,
        project_id=intent.project_id,
        state=state,
        source_basis=intent.source_basis,
        source_commit=intent.source_commit,
        source_evidence_id=intent.source_evidence_id,
        source_evidence_sha256=intent.source_evidence_sha256,
        repository_slug=intent.repository_slug,
        base_branch=intent.base_branch,
        branch_name=intent.branch_name,
        publisher_configured=publisher_configured,
        attempt_count=attempt_count,
        pull_request_number=pull_request_number,
        pull_request_url=pull_request_url,
        pull_request_state=pull_request_state,
        pull_request_draft=pull_request_draft,
        last_error_code=last_error_code,
        last_error_message=last_error_message,
    )


def _integration_source(
    snapshot: PersistedRunSnapshot,
    workspace: LocalGitWorkspace,
) -> _PublicationSource | None:
    expected_tasks = {item.task.task_id for item in snapshot.tasks}
    candidates: list[tuple[_PublicationSource, MergeQueueSnapshot]] = []

    for evidence in snapshot.evidence:
        if evidence.kind is not PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT:
            continue
        try:
            merge_snapshot = MergeQueueSnapshot.model_validate(evidence.payload)
        except ValidationError as exc:
            raise PersistenceCorruptionError(
                "persisted merge queue snapshot failed schema validation"
            ) from exc
        if merge_snapshot.run_base_commit != snapshot.base_commit:
            raise PersistenceCorruptionError(
                "persisted merge queue snapshot does not match the Run base commit"
            )
        if merge_snapshot.stopped or set(merge_snapshot.integrated_task_ids) != expected_tasks:
            continue
        if len(merge_snapshot.integrated_task_ids) != len(expected_tasks):
            raise PersistenceCorruptionError(
                "complete merge queue snapshot duplicates integrated task identity"
            )
        candidates.append(
            (
                _PublicationSource(
                    basis=GitHubPublicationSourceBasis.INTEGRATION,
                    commit=merge_snapshot.head_commit,
                    evidence_id=evidence.id,
                    evidence_sha256=evidence.payload_sha256,
                ),
                merge_snapshot,
            )
        )

    if not candidates:
        return None

    identities = {
        (source.commit, merge_snapshot.integration_ref)
        for source, merge_snapshot in candidates
    }
    if len(identities) != 1:
        raise PersistenceCorruptionError(
            "complete merge queue evidence defines conflicting publication sources"
        )

    source, merge_snapshot = max(candidates, key=lambda item: item[0].evidence_id)
    reader = ReadOnlyCommitDiffReader(workspace)
    try:
        for attempt in merge_snapshot.attempts:
            if attempt.outcome is not MergeAttemptOutcome.INTEGRATED:
                raise PersistenceCorruptionError(
                    "complete publication integration evidence contains a non-integrated attempt"
                )
            task_parents = reader.commit_parents(attempt.task_commit)
            if task_parents != (attempt.task_base_commit,):
                raise PersistenceCorruptionError(
                    "publication task commit no longer matches its accepted task base"
                )
            if attempt.integration_commit is None:
                raise PersistenceCorruptionError(
                    "complete publication integration attempt lacks integration commit"
                )
            integration_parents = reader.commit_parents(attempt.integration_commit)
            if integration_parents != (
                attempt.previous_integration_commit,
                attempt.task_commit,
            ):
                raise PersistenceCorruptionError(
                    "publication integration commit no longer matches its accepted parent pair"
                )
    except CommitDiffError as exc:
        raise PersistenceCorruptionError(
            "accepted integration publication source cannot be reproduced from local Git"
        ) from exc
    return source


def _single_task_source(
    snapshot: PersistedRunSnapshot,
    workspace: LocalGitWorkspace,
) -> _PublicationSource:
    task_id = snapshot.tasks[0].task.task_id
    candidates: list[tuple[_PublicationSource, str]] = []
    for evidence in snapshot.evidence:
        if (
            evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION
            or evidence.task_id != task_id
        ):
            continue
        try:
            execution = WorkerExecutionEvidence.model_validate(evidence.payload)
        except ValidationError as exc:
            raise PersistenceCorruptionError(
                "persisted worker execution evidence failed schema validation"
            ) from exc
        if execution.run_id != snapshot.run_id or execution.task_id != task_id:
            raise PersistenceCorruptionError(
                "persisted worker execution publication evidence has mismatched identity"
            )
        if execution.status is not WorkerExecutionStatus.SUCCEEDED:
            continue
        if execution.commit_sha is None:
            raise PersistenceCorruptionError(
                "successful worker publication evidence lacks task commit"
            )
        candidates.append(
            (
                _PublicationSource(
                    basis=GitHubPublicationSourceBasis.SINGLE_TASK,
                    commit=execution.commit_sha,
                    evidence_id=evidence.id,
                    evidence_sha256=evidence.payload_sha256,
                ),
                execution.base_commit,
            )
        )

    if not candidates:
        raise ProductGitHubPublicationUnavailableError(
            "single-task GitHub publication requires accepted successful worker commit evidence"
        )
    identities = {(source.commit, base_commit) for source, base_commit in candidates}
    if len(identities) != 1:
        raise PersistenceCorruptionError(
            "successful worker evidence defines conflicting publication sources"
        )

    source, base_commit = max(candidates, key=lambda item: item[0].evidence_id)
    reader = ReadOnlyCommitDiffReader(workspace)
    try:
        parents = reader.commit_parents(source.commit)
    except CommitDiffError as exc:
        raise PersistenceCorruptionError(
            "accepted single-task publication source cannot be reproduced from local Git"
        ) from exc
    if parents != (base_commit,):
        raise PersistenceCorruptionError(
            "single-task publication commit no longer has its accepted base as sole parent"
        )
    return source
