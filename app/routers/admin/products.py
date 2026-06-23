"""Admin product directory router — browse, inspect, manage visibility (R10).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path                       | Permission        | Req            |
|--------|----------------------------|-------------------|----------------|
| GET    | `/products`                | `products.read`   | 10.1, 10.2, 10.3 |
| GET    | `/products/{id}`           | `products.read`   | 10.4, 10.6     |
| PATCH  | `/products/{id}/status`    | `products.manage` | 10.5           |

Reads are gated by ``require_permission("products.read")`` and the status
mutation by ``require_permission("products.manage")``; the mutating route is
wrapped by ``audited(...)`` so it writes one immutable audit entry
(Requirement 19.1 / R10.5).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin.products import (
    ProductDetail,
    ProductListEnvelope,
    ProductListItem,
    ProductStatusResponse,
    ProductStatusUpdateRequest,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.product_service import ProductListParams, ProductService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin-products"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/products",
    response_model=ProductListEnvelope,
    dependencies=[Depends(require_permission("products.read"))],
)
async def list_products(
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    merchant_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
) -> ProductListEnvelope:
    """Paginated product directory: search by title, filter by merchant/status/
    price range, sort, paginate (R10.1, R10.2, R10.3)."""
    page_obj = await ProductService(db).list_products(
        ProductListParams(
            page=page,
            page_size=page_size,
            search=search,
            sort=sort,
            merchant_id=merchant_id,
            status=status,
            min_price=min_price,
            max_price=max_price,
        )
    )
    return ProductListEnvelope(
        items=[ProductListItem.model_validate(row) for row in page_obj.items],
        page=page_obj.page,
        page_size=page_obj.page_size,
        total=page_obj.total,
        total_pages=page_obj.total_pages,
        has_next=page_obj.has_next,
        next_page=page_obj.next_page,
    )


@router.get(
    "/products/{product_id}",
    response_model=ProductDetail,
    dependencies=[Depends(require_permission("products.read"))],
)
async def get_product(
    product_id: uuid.UUID,
    db: DBSession,
) -> ProductDetail:
    """Single product detail by identifier; 404 when missing (R10.4, R10.6)."""
    service = ProductService(db)
    product = await service.get_product(product_id)
    merchant_name = await service.get_merchant_name(product.merchant_id)
    return ProductDetail(
        id=product.id,
        merchant_id=product.merchant_id,
        merchant_name=merchant_name,
        sku=product.sku,
        title=product.title,
        description=product.description,
        category=product.category,
        subcategory=product.subcategory,
        brand=product.brand,
        status=product.status,
        primary_image_url=product.primary_image_url,
        has_simulafly_listing=product.has_simulafly_listing,
        price=product.in_app_price,
        in_app_stock=product.in_app_stock,
        health_score=product.health_score,
        health_reason=product.health_reason,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.patch(
    "/products/{product_id}/status",
    response_model=ProductStatusResponse,
    dependencies=[Depends(require_permission("products.manage"))],
)
async def update_product_status(
    product_id: uuid.UUID,
    payload: ProductStatusUpdateRequest,
    db: DBSession,
    audit: Annotated[
        AuditContext, Depends(audited("products.update_status", "merchant_product"))
    ],
) -> ProductStatusResponse:
    """Persist a product's new visibility status; audited (R10.5, R10.6)."""
    product = await ProductService(db).update_status(product_id, payload.status)
    audit.set_target(product_id)
    audit.add_metadata(status=product.status)
    return ProductStatusResponse.model_validate(product)
