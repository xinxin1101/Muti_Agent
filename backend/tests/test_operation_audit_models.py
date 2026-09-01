from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.operation_audit import (
    OperationAuditAction,
    OperationAuditOutcome,
    OperationAuditRecord,
)


def test_operation_audit_requires_a_target() -> None:
    with pytest.raises(ValidationError, match="require at least one target"):
        OperationAuditRecord(
            audit_id=uuid4(),
            operation_key="delete-project:preview",
            actor="local-product-user",
            action=OperationAuditAction.PROJECT_DELETED,
            outcome=OperationAuditOutcome.REJECTED,
            created_at=datetime.now(UTC),
        )


def test_operation_audit_accepts_a_confirmed_recovery_target() -> None:
    record = OperationAuditRecord(
        audit_id=uuid4(),
        operation_key="continue:one",
        actor="local-product-user",
        action=OperationAuditAction.DEVELOPMENT_SESSION_CONTINUED,
        outcome=OperationAuditOutcome.SUCCEEDED,
        run_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert record.run_id is not None
