from __future__ import annotations

import base64
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.room_image import RoomImage
from app.schemas.upload import ALLOWED_MEDIA_TYPES

settings = get_settings()


class ImageValidationError(Exception):
    pass


def decode_and_validate(image_base64: str, media_type: str) -> bytes:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ImageValidationError(f"unsupported media type: {media_type}")
    cleaned = image_base64.split(",", 1)[-1]  # tolerate data-URL prefix
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception as e:
        raise ImageValidationError("invalid base64") from e
    if len(raw) > settings.MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"image too large: {len(raw)} bytes (max {settings.MAX_IMAGE_BYTES})"
        )
    if len(raw) < 100:
        raise ImageValidationError("image too small")
    return raw


async def persist_image(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    data: bytes,
    media_type: str,
    source: str = "upload",
) -> RoomImage:
    image = RoomImage(
        owner_id=owner_id,
        data=data,
        media_type=media_type,
        byte_size=len(data),
        source=source,
    )
    db.add(image)
    await db.flush()
    return image


async def persist_base64(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    image_base64: str,
    media_type: str,
    source: str = "upload",
) -> RoomImage:
    try:
        raw = decode_and_validate(image_base64, media_type)
    except ImageValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return await persist_image(db, owner_id=owner_id, data=raw, media_type=media_type, source=source)


async def get_owned(db: AsyncSession, *, image_id: uuid.UUID, owner_id: uuid.UUID) -> RoomImage | None:
    res = await db.execute(
        select(RoomImage).where(RoomImage.id == image_id, RoomImage.owner_id == owner_id)
    )
    return res.scalar_one_or_none()


async def get_image(db: AsyncSession, *, image_id: uuid.UUID) -> RoomImage | None:
    res = await db.execute(
        select(RoomImage).where(RoomImage.id == image_id)
    )
    return res.scalar_one_or_none()
