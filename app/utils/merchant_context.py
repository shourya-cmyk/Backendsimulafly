"""Merchant-context dependency for the /merchant/* router.

Validates the X-Merchant-Id header against merchant_members and returns
a MerchantContext that downstream handlers depend on.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select

from app.models.merchant import Merchant, MerchantMember
from app.models.user import User
from app.utils.dependencies import CurrentUser, DBSession


@dataclass
class MerchantContext:
    merchant: Merchant
    member: MerchantMember
    role: str


async def get_current_merchant(
    user: CurrentUser,
    db: DBSession,
    x_merchant_id: Annotated[uuid.UUID, Header(alias="X-Merchant-Id")],
) -> MerchantContext:
    merchant = await db.get(Merchant, x_merchant_id)
    if not merchant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="merchant not found")

    res = await db.execute(
        select(MerchantMember).where(
            MerchantMember.merchant_id == merchant.id,
            MerchantMember.user_id == user.id,
        )
    )
    member = res.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not a member of this merchant"
        )

    return MerchantContext(merchant=merchant, member=member, role=member.role)


CurrentMerchantContext = Annotated[MerchantContext, Depends(get_current_merchant)]


def require_role(*allowed_roles: str):
    """Returns a callable that raises 403 unless ctx.role is in allowed_roles.

    Use as a FastAPI Depends inside a route to gate by role.
    """
    def guard(ctx: CurrentMerchantContext) -> MerchantContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role in {sorted(allowed_roles)}",
            )
        return ctx

    return guard
