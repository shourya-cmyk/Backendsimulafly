import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.product import MerchantProductSimpleOut


class SavedItemAdd(BaseModel):
    product_id: uuid.UUID  # merchant_product id
    note: str | None = Field(default=None, max_length=280)
    room_session_id: uuid.UUID | None = None


class SavedItemUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=280)
    room_session_id: uuid.UUID | None = None


class SavedItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID  # alias for merchant_product_id — kept for API compat
    note: str | None
    room_session_id: uuid.UUID | None
    added_at: datetime
    product: MerchantProductSimpleOut


class SavedList(BaseModel):
    items: list[SavedItemOut]
    item_count: int
