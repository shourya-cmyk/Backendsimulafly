"""Admin consumer-user directory router (Requirement 7).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                         | Permission      | Req       |
|--------|------------------------------|-----------------|-----------|
| GET    | `/users`                     | `users.read`    | 7.1–7.4, 7.9 |
| GET    | `/users/attribution`         | `users.read`    | 7.9       |
| GET    | `/users/{id}`                | `users.read`    | 7.5, 7.6  |
| POST   | `/users/{id}/suspend`        | `users.suspend` | 7.7       |
| POST   | `/users/{id}/reactivate`     | `users.suspend` | 7.8       |

Reads are gated by ``require_permission("users.read")`` and status transitions
by ``require_permission("users.suspend")``. The mutating routes are wrapped by
``audited(...)`` so each writes one immutable audit entry (Requirement 19.1).
Listing/search/filter/sort/pagination is delegated to the shared listing engine
via :class:`app.services.admin.directory_service.DirectoryService`.

The static ``/users/attribution`` route is declared *before* the dynamic
``/users/{user_id}`` route so the literal path is matched first.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.listing import ListingEnvelope
from app.schemas.admin.users import (
    UserAttributionItem,
    UserDetail,
    UserListItem,
    UserStatusResponse,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.directory_service import DirectoryService
from app.services.admin.listing import ListParams
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-users"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _list_params(
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
) -> ListParams:
    """Translate query params into a :class:`ListParams` for the listing engine.

    ``is_active`` is exposed as a first-class conjunctive filter (R7.3); omit it
    to span both active and suspended users.
    """
    filters: dict[str, object] = {}
    if is_active is not None:
        filters["is_active"] = is_active
    kwargs: dict[str, object] = {
        "page": page,
        "search": search,
        "sort": sort,
        "filters": filters,
    }
    if page_size is not None:
        kwargs["page_size"] = page_size
    return ListParams(**kwargs)


ListParamsDep = Annotated[ListParams, Depends(_list_params)]


@router.get(
    "/users",
    response_model=ListingEnvelope[UserListItem],
    dependencies=[Depends(require_permission("users.read"))],
)
async def list_users(
    db: DBSession,
    params: ListParamsDep,
) -> ListingEnvelope[UserListItem]:
    """Paginated user directory with search/filter/sort and attribution (R7.1–R7.4, R7.9)."""
    service = DirectoryService(db)
    page = await service.list_users(params)
    attributions = await service.resolve_attributions(page.items)
    items = [
        UserListItem(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            is_active=user.is_active,
            created_at=user.created_at,
            attribution=attributions[user.id],
        )
        for user in page.items
    ]
    return ListingEnvelope[UserListItem](
        items=items,
        page=page.page,
        page_size=page.page_size,
        total=page.total,
        total_pages=page.total_pages,
        has_next=page.has_next,
        next_page=page.next_page,
    )


@router.get(
    "/users/attribution",
    response_model=ListingEnvelope[UserAttributionItem],
    dependencies=[Depends(require_permission("users.read"))],
)
async def list_user_attribution(
    db: DBSession,
    params: ListParamsDep,
) -> ListingEnvelope[UserAttributionItem]:
    """Referral source & acquisition attribution for the requested users (R7.9)."""
    service = DirectoryService(db)
    page = await service.list_users(params)
    attributions = await service.resolve_attributions(page.items)
    items = [
        UserAttributionItem(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            attribution=attributions[user.id],
        )
        for user in page.items
    ]
    return ListingEnvelope[UserAttributionItem](
        items=items,
        page=page.page,
        page_size=page.page_size,
        total=page.total,
        total_pages=page.total_pages,
        has_next=page.has_next,
        next_page=page.next_page,
    )


@router.get(
    "/users/{user_id}",
    response_model=UserDetail,
    dependencies=[Depends(require_permission("users.read"))],
)
async def get_user(
    user_id: uuid.UUID,
    db: DBSession,
) -> UserDetail:
    """Single user detail incl. profile, credit balance, attribution; 404 if missing (R7.5, R7.6)."""
    service = DirectoryService(db)
    user = await service.get_user(user_id)
    attributions = await service.resolve_attributions([user])
    return UserDetail(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        credit_balance=user.credit_balance,
        design_profile=user.design_profile,
        attribution=attributions[user.id],
    )


@router.post(
    "/users/{user_id}/suspend",
    response_model=UserStatusResponse,
    dependencies=[Depends(require_permission("users.suspend"))],
)
async def suspend_user(
    user_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("users.suspend", "user"))],
) -> UserStatusResponse:
    """Suspend a user (``is_active=False``); audited (R7.7)."""
    user = await DirectoryService(db).suspend_user(user_id)
    audit.set_target(user_id)
    return UserStatusResponse(id=user.id, email=user.email, is_active=user.is_active)


@router.post(
    "/users/{user_id}/reactivate",
    response_model=UserStatusResponse,
    dependencies=[Depends(require_permission("users.suspend"))],
)
async def reactivate_user(
    user_id: uuid.UUID,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("users.reactivate", "user"))],
) -> UserStatusResponse:
    """Reactivate a suspended user (``is_active=True``); audited (R7.8)."""
    user = await DirectoryService(db).reactivate_user(user_id)
    audit.set_target(user_id)
    return UserStatusResponse(id=user.id, email=user.email, is_active=user.is_active)
