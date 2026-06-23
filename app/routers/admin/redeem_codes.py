"""Admin redeem-code router (Requirement 15).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step in task 16.1):

| Method | Path                            | Permission       | Req              |
|--------|---------------------------------|------------------|------------------|
| POST   | `/redeem-codes`                 | `redeem.manage`  | 15.1, 15.5, 15.6 |
| GET    | `/redeem-codes`                 | `redeem.read`    | 15.2, 15.3       |
| POST   | `/redeem-codes/{id}/deactivate` | `redeem.manage`  | 15.4             |

Reads are gated by ``require_permission("redeem.read")``; the mutating routes
by ``require_permission("redeem.manage")``. Both mutating routes are wrapped
with ``audited(...)`` so generation and deactivation each write one immutable
audit entry (R15.1 / R15.4 / R19.1). Generation parameters are validated by the
request schema (``quantity >= 1``, ``value > 0``) → HTTP 422 (R15.5); uniqueness
is enforced by the table's ``UNIQUE`` constraint plus retry-on-collision in the
service (R15.6). Deactivation of an already-redeemed code yields HTTP 409 and an
unknown code id yields HTTP 404, both from the service layer.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.listing import ListingEnvelope
from app.schemas.admin.redeem_codes import (
    RedeemCodeGenerateRequest,
    RedeemCodeGenerateResponse,
    RedeemCodeItem,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.redeem_service import RedeemCodeService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-redeem-codes"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/redeem-codes",
    response_model=RedeemCodeGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("redeem.manage"))],
)
async def generate_redeem_codes(
    payload: RedeemCodeGenerateRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("redeem.generate", "redeem_code_batch"))],
) -> RedeemCodeGenerateResponse:
    """Generate a batch of unique redeem codes and record the action (R15.1).

    A quantity below one or a non-positive value is rejected with HTTP 422 by
    request validation (R15.5). Generated codes are unique (R15.6).
    """
    batch_id, codes = await RedeemCodeService(db).generate(
        value=payload.value,
        quantity=payload.quantity,
        expiry=payload.expiry,
    )
    audit.set_target(batch_id)
    audit.add_metadata(
        quantity=len(codes),
        value=str(payload.value),
        expiry=payload.expiry.isoformat() if payload.expiry else None,
    )
    return RedeemCodeGenerateResponse(
        batch_id=batch_id,
        quantity=len(codes),
        items=[RedeemCodeItem.model_validate(code) for code in codes],
    )


@router.get(
    "/redeem-codes",
    response_model=ListingEnvelope[RedeemCodeItem],
    dependencies=[Depends(require_permission("redeem.read"))],
)
async def list_redeem_codes(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    sort: str | None = Query(default=None),
    search: str | None = Query(default=None),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by redemption status (active|redeemed|inactive|expired)",
    ),
) -> ListingEnvelope[RedeemCodeItem]:
    """Paginated redeem-code listing incl. code, value, status, expiry, and
    redemption details (R15.2); optional filter by status (R15.3)."""
    page_obj = await RedeemCodeService(db).list_codes(
        page=page,
        page_size=page_size,
        sort=sort,
        search=search,
        status_filter=status_filter,
    )
    return ListingEnvelope[RedeemCodeItem](
        items=[RedeemCodeItem.model_validate(code) for code in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.post(
    "/redeem-codes/{code_id}/deactivate",
    response_model=RedeemCodeItem,
    dependencies=[Depends(require_permission("redeem.manage"))],
)
async def deactivate_redeem_code(
    code_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("redeem.deactivate", "redeem_code"))],
) -> RedeemCodeItem:
    """Deactivate a not-yet-redeemed redeem code and record the action (R15.4).

    A redeemed code cannot be deactivated → HTTP 409; an unknown code id →
    HTTP 404.
    """
    code = await RedeemCodeService(db).deactivate(code_id)
    audit.set_target(code.id)
    audit.add_metadata(status=code.status)
    return RedeemCodeItem.model_validate(code)
