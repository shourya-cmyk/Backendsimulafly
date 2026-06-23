"""Admin merchant directory router (Requirement 8).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step in task 16.1):

| Method | Path                          | Permission         | Req       |
|--------|-------------------------------|--------------------|-----------|
| GET    | `/merchants`                  | `merchants.read`   | 8.1–8.3   |
| GET    | `/merchants/{id}`             | `merchants.read`   | 8.4, 8.7  |
| POST   | `/merchants/{id}/suspend`     | `merchants.suspend`| 8.5, 8.7  |
| POST   | `/merchants/{id}/activate`    | `merchants.suspend`| 8.6, 8.7  |

Reads are gated by ``require_permission("merchants.read")``; the suspend and
activate actions by ``require_permission("merchants.suspend")``. Both mutating
routes are wrapped with ``audited(...)`` so each writes one immutable audit
entry (Requirement 19.1 / R8.5, R8.6). Missing merchant identifiers yield
HTTP 404 from the service layer (R8.7).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.merchant import Merchant, MerchantStatus
from app.models.wallet import Wallet
from app.schemas.admin.listing import ListingEnvelope
from app.schemas.admin.merchants import (
    MerchantDetail,
    MerchantListItem,
    MerchantMemberOut,
    MerchantStatusResponse,
    WalletSummary,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.merchant_directory_service import MerchantDirectoryService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-merchants"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_status_response(merchant: Merchant) -> MerchantStatusResponse:
    return MerchantStatusResponse(
        id=merchant.id,
        display_name=merchant.display_name,
        status=merchant.status,
    )


def _to_detail(merchant: Merchant, wallet: Wallet | None) -> MerchantDetail:
    members = [
        MerchantMemberOut(
            id=member.id,
            user_id=member.user_id,
            role=member.role,
            email=member.user.email if member.user is not None else None,
            full_name=member.user.full_name if member.user is not None else None,
            joined_at=member.joined_at,
        )
        for member in merchant.members
    ]
    return MerchantDetail(
        id=merchant.id,
        slug=merchant.slug,
        display_name=merchant.display_name,
        legal_name=merchant.legal_name,
        status=merchant.status,
        is_kyc_completed=merchant.is_kyc_completed,
        country=merchant.country,
        support_email=merchant.support_email,
        support_phone=merchant.support_phone,
        created_at=merchant.created_at,
        updated_at=merchant.updated_at,
        members=members,
        wallet=WalletSummary.model_validate(wallet) if wallet is not None else None,
    )


@router.get(
    "/merchants",
    response_model=ListingEnvelope[MerchantListItem],
    dependencies=[Depends(require_permission("merchants.read"))],
)
async def list_merchants(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    status: str | None = Query(default=None, description="Filter by merchant status"),
    is_kyc_completed: bool | None = Query(
        default=None, description="Filter by KYC completion"
    ),
) -> ListingEnvelope[MerchantListItem]:
    """Paginated, searchable, filterable merchant directory (R8.1, R8.2, R8.3)."""
    page_obj = await MerchantDirectoryService(db).list_merchants(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        status_filter=status,
        is_kyc_completed=is_kyc_completed,
    )
    return ListingEnvelope[MerchantListItem](
        items=[MerchantListItem.model_validate(m) for m in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/merchants/{merchant_id}",
    response_model=MerchantDetail,
    dependencies=[Depends(require_permission("merchants.read"))],
)
async def get_merchant(
    merchant_id: uuid.UUID,
    db: DBSession,
) -> MerchantDetail:
    """Single merchant detail incl. members, wallet summary, status (R8.4, R8.7)."""
    merchant, wallet = await MerchantDirectoryService(db).get_merchant(merchant_id)
    return _to_detail(merchant, wallet)


@router.post(
    "/merchants/{merchant_id}/suspend",
    response_model=MerchantStatusResponse,
    dependencies=[Depends(require_permission("merchants.suspend"))],
)
async def suspend_merchant(
    merchant_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("merchants.suspend", "merchant"))],
) -> MerchantStatusResponse:
    """Suspend a merchant (status → ``suspended``); audited (R8.5, R8.7)."""
    merchant = await MerchantDirectoryService(db).suspend(merchant_id)
    audit.set_target(merchant_id)
    audit.add_metadata(status=MerchantStatus.SUSPENDED.value)
    return _to_status_response(merchant)


@router.post(
    "/merchants/{merchant_id}/activate",
    response_model=MerchantStatusResponse,
    dependencies=[Depends(require_permission("merchants.suspend"))],
)
async def activate_merchant(
    merchant_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("merchants.activate", "merchant"))],
) -> MerchantStatusResponse:
    """Activate a merchant (status → ``active``); audited (R8.6, R8.7)."""
    merchant = await MerchantDirectoryService(db).activate(merchant_id)
    audit.set_target(merchant_id)
    audit.add_metadata(status=MerchantStatus.ACTIVE.value)
    return _to_status_response(merchant)
