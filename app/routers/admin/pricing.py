"""Admin pricing router — pricing-rule listing/create/update (R13).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                   | Permission       | Req                 |
|--------|------------------------|------------------|---------------------|
| GET    | `/pricing-rules`       | `pricing.read`   | 13.1                |
| POST   | `/pricing-rules`       | `pricing.manage` | 13.2, 13.4, 13.5    |
| PATCH  | `/pricing-rules/{id}`  | `pricing.manage` | 13.3, 13.4, 13.5    |

Reads are gated by ``require_permission("pricing.read")`` and the mutating
routes by ``require_permission("pricing.manage")``. Both mutating routes are
wrapped with ``audited(...)`` so each create/update writes one immutable audit
entry (Requirement 13.2 / 13.3 / 19.1). The update opens a *new* effective
window rather than mutating history (R13.3).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.pricing import (
    PricingRuleCreate,
    PricingRuleItem,
    PricingRuleListResponse,
    PricingRuleUpdate,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.pricing_service import PricingService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-pricing"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/pricing-rules",
    response_model=PricingRuleListResponse,
    dependencies=[Depends(require_permission("pricing.read"))],
)
async def list_pricing_rules(
    db: DBSession,
    include_history: bool = Query(default=False),
) -> PricingRuleListResponse:
    """Return the current pricing rules: event type, rate, rate type, currency,
    and effective window (R13.1).

    By default only currently-effective rules are returned; pass
    ``include_history=true`` to include superseded windows.
    """
    rules = await PricingService(db).list_rules(include_history=include_history)
    return PricingRuleListResponse(
        items=[PricingRuleItem.model_validate(rule) for rule in rules]
    )


@router.post(
    "/pricing-rules",
    response_model=PricingRuleItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("pricing.manage"))],
)
async def create_pricing_rule(
    payload: PricingRuleCreate,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("pricing.create", "pricing_rule"))],
) -> PricingRuleItem:
    """Create a pricing rule and record the action (R13.2).

    A negative rate (R13.4) or an ``effective_until`` earlier than
    ``effective_from`` (R13.5) is rejected with HTTP 422.
    """
    rule = await PricingService(db).create_rule(payload)
    audit.set_target(rule.id)
    audit.add_metadata(
        event_type=rule.event_type,
        rate=str(rule.rate),
        rate_type=rule.rate_type,
    )
    return PricingRuleItem.model_validate(rule)


@router.patch(
    "/pricing-rules/{rule_id}",
    response_model=PricingRuleItem,
    dependencies=[Depends(require_permission("pricing.manage"))],
)
async def update_pricing_rule(
    rule_id: uuid.UUID,
    payload: PricingRuleUpdate,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("pricing.update", "pricing_rule"))],
) -> PricingRuleItem:
    """Update a pricing rule by opening a new effective window and record the
    action (R13.3).

    The original rule is closed and a new rule is inserted; history is never
    mutated. A negative rate (R13.4) or an inverted window (R13.5) is rejected
    with HTTP 422; a missing rule returns 404.
    """
    new_rule = await PricingService(db).update_rule(rule_id, payload)
    audit.set_target(new_rule.id)
    audit.add_metadata(
        superseded_rule_id=str(rule_id),
        event_type=new_rule.event_type,
        rate=str(new_rule.rate),
        rate_type=new_rule.rate_type,
    )
    return PricingRuleItem.model_validate(new_rule)
