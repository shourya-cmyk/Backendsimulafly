import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.cart import CartItem
from app.models.merchant_product import MerchantProduct
from app.schemas.cart import CartItemAdd, CartItemOut, CartItemUpdate, CartSummary
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/cart", tags=["cart"])


async def _load_cart(db, user_id: uuid.UUID) -> list[CartItem]:
    res = await db.execute(
        select(CartItem)
        .options(selectinload(CartItem.merchant_product))
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.added_at.desc())
    )
    return list(res.scalars().all())


def _summary(items: list[CartItem]) -> CartSummary:
    total = sum((item.merchant_product.in_app_price or 0) * item.quantity for item in items)
    out_items = []
    for i in items:
        # Expose merchant_product_id as product_id for API compat
        out_items.append(
            CartItemOut(
                id=i.id,
                product_id=i.merchant_product_id,
                quantity=i.quantity,
                added_at=i.added_at,
                product=i.merchant_product,
            )
        )
    return CartSummary(
        items=out_items,
        estimated_total=round(total, 2),
        item_count=sum(i.quantity for i in items),
    )


@router.get("/", response_model=CartSummary)
async def get_cart(user: CurrentUser, db: DBSession) -> CartSummary:
    items = await _load_cart(db, user.id)
    return _summary(items)


@router.post("/", response_model=CartSummary, status_code=status.HTTP_201_CREATED)
async def add_to_cart(body: CartItemAdd, user: CurrentUser, db: DBSession) -> CartSummary:
    # body.product_id is the merchant_product_id from the frontend
    stmt = (
        select(MerchantProduct)
        .options(selectinload(MerchantProduct.merchant))
        .where(MerchantProduct.id == body.product_id)
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    # Check range restriction
    if user.latitude is not None and user.longitude is not None and product.merchant:
        m = product.merchant
        if m.latitude is not None and m.longitude is not None and m.range_km is not None:
            import math
            def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
                R = 6371.0  # Earth's radius in km
                dlat = math.radians(lat2 - lat1)
                dlon = math.radians(lon2 - lon1)
                a = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(math.radians(lat1))
                    * math.cos(math.radians(lat2))
                    * math.sin(dlon / 2) ** 2
                )
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                return R * c

            dist = calculate_distance(user.latitude, user.longitude, m.latitude, m.longitude)
            if dist > m.range_km:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This shop does not serve your location."
                )

    existing = await db.execute(
        select(CartItem).where(
            CartItem.user_id == user.id,
            CartItem.merchant_product_id == body.product_id,
        )
    )
    item = existing.scalar_one_or_none()
    if item:
        item.quantity = min(10, item.quantity + (body.quantity or 1))
    else:
        db.add(
            CartItem(
                user_id=user.id,
                merchant_product_id=body.product_id,
                quantity=body.quantity or 1,
            )
        )
    await db.commit()
    items = await _load_cart(db, user.id)
    return _summary(items)


@router.patch("/{item_id}", response_model=CartItemOut)
async def update_quantity(
    item_id: uuid.UUID, body: CartItemUpdate, user: CurrentUser, db: DBSession
) -> CartItemOut:
    res = await db.execute(
        select(CartItem)
        .options(selectinload(CartItem.merchant_product))
        .where(CartItem.id == item_id, CartItem.user_id == user.id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cart item not found")
    item.quantity = body.quantity
    await db.commit()
    await db.refresh(item)
    return CartItemOut(
        id=item.id,
        product_id=item.merchant_product_id,
        quantity=item.quantity,
        added_at=item.added_at,
        product=item.merchant_product,
    )


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: uuid.UUID, user: CurrentUser, db: DBSession) -> None:
    res = await db.execute(
        select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user.id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cart item not found")
    await db.delete(item)
    await db.commit()


@router.delete("/", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cart(user: CurrentUser, db: DBSession) -> None:
    items = await _load_cart(db, user.id)
    for item in items:
        await db.delete(item)
    await db.commit()
