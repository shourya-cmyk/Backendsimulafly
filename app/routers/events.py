"""Buyer-facing event ingestion endpoints."""
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models.merchant_product import MerchantProduct, MerchantProductExternalLink
from app.schemas.event import (
    BuyerEventOut,
    ClickEventIn,
    ExternalRedirectIn,
    ExternalRedirectOut,
    ImpressionBatchIn,
)
from app.services.billing import BillingService
from app.utils.dependencies import CurrentUser, DBSession

_settings = get_settings()
router = APIRouter(prefix="/events", tags=["events"])


class _ImpressionBatchResponse(BaseModel):
    recorded: int


@router.post("/click", response_model=BuyerEventOut)
@limiter.limit("30/minute")
async def record_click(
    body: ClickEventIn,
    user: CurrentUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
    request: Request,
):
    product = await db.get(MerchantProduct, body.product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    svc = BillingService(db)
    event = await svc.record_event(
        event_type="click",
        user_id=user.id,
        merchant_id=product.merchant_id,
        product_id=product.id,
        session_id=body.session_id,
        context=body.context,
        client_ip=request.client.host if request.client else None,
    )
    background_tasks.add_task(svc.pause_if_depleted_for, product.merchant_id)
    return event


@router.post("/external-redirect", response_model=ExternalRedirectOut)
@limiter.limit("10/minute")
async def record_external_redirect(
    body: ExternalRedirectIn,
    user: CurrentUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
    request: Request,
):
    product = await db.get(MerchantProduct, body.product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    link = await db.get(MerchantProductExternalLink, body.link_id)
    if not link or link.merchant_product_id != product.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="link not found for product"
        )

    svc = BillingService(db)
    event = await svc.record_event(
        event_type="external_redirect",
        user_id=user.id,
        merchant_id=product.merchant_id,
        product_id=product.id,
        session_id=body.session_id,
        context={"link_id": str(link.id), "platform": link.platform, "url": link.url},
        client_ip=request.client.host if request.client else None,
    )
    background_tasks.add_task(svc.pause_if_depleted_for, product.merchant_id)

    return {"target_url": link.url, "billed": event.billed}


@router.post("/impression-batch", response_model=_ImpressionBatchResponse)
@limiter.limit("20/minute")
async def record_impression_batch(
    body: ImpressionBatchIn,
    user: CurrentUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
    request: Request,
):
    res = await db.execute(
        select(MerchantProduct).where(MerchantProduct.id.in_(body.product_ids))
    )
    products = {p.id: p for p in res.scalars().all()}

    svc = BillingService(db)
    recorded = 0
    affected_merchants: set[uuid.UUID] = set()
    for pid in body.product_ids:
        product = products.get(pid)
        if not product:
            continue
        await svc.record_event(
            event_type="impression",
            user_id=user.id,
            merchant_id=product.merchant_id,
            product_id=product.id,
            session_id=body.session_id,
            context={},
            client_ip=request.client.host if request.client else None,
        )
        affected_merchants.add(product.merchant_id)
        recorded += 1

    for mid in affected_merchants:
        background_tasks.add_task(svc.pause_if_depleted_for, mid)

    return {"recorded": recorded}
