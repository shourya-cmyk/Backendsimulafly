import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.merchant_product import MerchantProduct
from app.models.saved_item import SavedItem
from app.models.session import DesignSession
from app.schemas.saved import SavedItemAdd, SavedItemOut, SavedItemUpdate, SavedList
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/saved", tags=["saved"])


async def _load_saved(db, user_id: uuid.UUID) -> list[SavedItem]:
    res = await db.execute(
        select(SavedItem)
        .options(selectinload(SavedItem.merchant_product))
        .where(SavedItem.user_id == user_id)
        .order_by(SavedItem.added_at.desc())
    )
    return list(res.scalars().all())


def _item_out(item: SavedItem) -> SavedItemOut:
    return SavedItemOut(
        id=item.id,
        product_id=item.merchant_product_id,
        note=item.note,
        room_session_id=item.room_session_id,
        added_at=item.added_at,
        product=item.merchant_product,
    )


def _summary(items: list[SavedItem]) -> SavedList:
    return SavedList(
        items=[_item_out(i) for i in items],
        item_count=len(items),
    )


@router.get("/", response_model=SavedList)
async def list_saved(user: CurrentUser, db: DBSession) -> SavedList:
    return _summary(await _load_saved(db, user.id))


@router.post("/", response_model=SavedList, status_code=status.HTTP_201_CREATED)
async def add_saved(body: SavedItemAdd, user: CurrentUser, db: DBSession) -> SavedList:
    # body.product_id is the merchant_product_id from the frontend
    product = await db.get(MerchantProduct, body.product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    if body.room_session_id is not None:
        session = await db.get(DesignSession, body.room_session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )

    existing = await db.execute(
        select(SavedItem).where(
            SavedItem.user_id == user.id,
            SavedItem.merchant_product_id == body.product_id,
        )
    )
    item = existing.scalar_one_or_none()
    if item:
        # Idempotent — update note/session if provided.
        if body.note is not None:
            item.note = body.note
        if body.room_session_id is not None:
            item.room_session_id = body.room_session_id
    else:
        db.add(
            SavedItem(
                user_id=user.id,
                merchant_product_id=body.product_id,
                note=body.note,
                room_session_id=body.room_session_id,
            )
        )
    await db.commit()
    return _summary(await _load_saved(db, user.id))


@router.patch("/{item_id}", response_model=SavedItemOut)
async def update_saved(
    item_id: uuid.UUID, body: SavedItemUpdate, user: CurrentUser, db: DBSession
) -> SavedItemOut:
    res = await db.execute(
        select(SavedItem)
        .options(selectinload(SavedItem.merchant_product))
        .where(SavedItem.id == item_id, SavedItem.user_id == user.id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="saved item not found"
        )
    if body.note is not None:
        item.note = body.note
    if body.room_session_id is not None:
        session = await db.get(DesignSession, body.room_session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        item.room_session_id = body.room_session_id
    await db.commit()
    await db.refresh(item)
    return _item_out(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved(item_id: uuid.UUID, user: CurrentUser, db: DBSession) -> None:
    res = await db.execute(
        select(SavedItem).where(SavedItem.id == item_id, SavedItem.user_id == user.id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="saved item not found"
        )
    await db.delete(item)
    await db.commit()


@router.delete("/by-product/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_by_product(
    product_id: uuid.UUID, user: CurrentUser, db: DBSession
) -> None:
    """Convenience for the client — unsave by merchant_product id.
    Useful for toggling the heart on a product card."""
    res = await db.execute(
        select(SavedItem).where(
            SavedItem.user_id == user.id,
            SavedItem.merchant_product_id == product_id,
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="saved item not found"
        )
    await db.delete(item)
    await db.commit()
