"""Unit tests for the Audit_Service (Requirement 19.1).

Covers the low-level :meth:`AuditService.record` writer and the
router-boundary :func:`audited` dependency: an entry is written after a
handler runs, capturing the actor, action, target, and outcome.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.admin import AdminAccount, AuditLog
from app.services.admin.audit_service import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    AuditContext,
    AuditService,
    audited,
)


@pytest_asyncio.fixture
async def admin_account(db_session) -> AdminAccount:
    account = AdminAccount(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x" * 20,
        full_name="Audit Actor",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def _all_logs(db_session) -> list[AuditLog]:
    result = await db_session.execute(select(AuditLog))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_record_writes_immutable_row(db_session, admin_account):
    service = AuditService(db_session)

    entry = await service.record(
        actor=admin_account,
        action="merchants.suspend",
        target_type="merchant",
        target_id=uuid.uuid4(),
        outcome=OUTCOME_SUCCESS,
        metadata={"reason": "fraud"},
    )

    assert entry.id is not None
    assert entry.actor_admin_id == admin_account.id
    assert entry.action == "merchants.suspend"
    assert entry.target_type == "merchant"
    assert isinstance(entry.target_id, str)
    assert entry.outcome == OUTCOME_SUCCESS
    assert entry.audit_metadata == {"reason": "fraud"}

    rows = await _all_logs(db_session)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_record_accepts_uuid_actor_and_null_target(db_session, admin_account):
    service = AuditService(db_session)

    entry = await service.record(
        actor=admin_account.id,
        action="system.task",
        target_type="job",
        target_id=None,
    )

    assert entry.actor_admin_id == admin_account.id
    assert entry.target_id is None
    assert entry.outcome == OUTCOME_SUCCESS
    assert entry.audit_metadata == {}


@pytest.mark.asyncio
async def test_record_allows_null_actor_for_system_actions(db_session):
    service = AuditService(db_session)

    entry = await service.record(
        actor=None,
        action="system.cron",
        target_type="job",
    )

    assert entry.actor_admin_id is None


@pytest.mark.asyncio
async def test_audited_records_after_handler_success(db_session, admin_account):
    target = uuid.uuid4()
    gen = audited("merchants.suspend", "merchant")(db=db_session, actor=admin_account)

    # Enter the dependency: receive the context the handler would fill in.
    context: AuditContext = await gen.asend(None)
    assert isinstance(context, AuditContext)
    assert context.outcome == OUTCOME_SUCCESS

    # Simulate the handler doing its work and recording the affected target.
    context.set_target(target)
    context.add_metadata(note="manual review")

    # Finalize the dependency (post-yield code runs => entry recorded).
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    rows = await _all_logs(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_admin_id == admin_account.id
    assert row.action == "merchants.suspend"
    assert row.target_type == "merchant"
    assert row.target_id == str(target)
    assert row.outcome == OUTCOME_SUCCESS
    assert row.audit_metadata == {"note": "manual review"}


@pytest.mark.asyncio
async def test_audited_records_failure_when_handler_raises(db_session, admin_account):
    gen = audited("merchants.suspend", "merchant")(db=db_session, actor=admin_account)
    context: AuditContext = await gen.asend(None)
    context.set_target("m-1")

    # Inject a handler exception; the dependency should record a failure and re-raise.
    with pytest.raises(ValueError):
        await gen.athrow(ValueError("boom"))

    rows = await _all_logs(db_session)
    assert len(rows) == 1
    assert rows[0].outcome == OUTCOME_FAILURE
    assert rows[0].target_id == "m-1"


@pytest.mark.asyncio
async def test_audited_skip_suppresses_recording(db_session, admin_account):
    gen = audited("merchants.suspend", "merchant")(db=db_session, actor=admin_account)
    context: AuditContext = await gen.asend(None)
    context.skip()

    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    rows = await _all_logs(db_session)
    assert rows == []
