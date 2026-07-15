import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventTypeLiteral = Literal[
    "impression",
    "ai_rag_mention",
    "click",
    "ai_image_generation",
]

LedgerEntryTypeLiteral = Literal["deduction", "credit", "refund", "adjustment"]


# ── Incoming event payloads (from Flutter) ─────────────────────────────────

class ClickEventIn(BaseModel):
    product_id: uuid.UUID
    session_id: str = Field(min_length=1, max_length=120)
    context: dict = Field(default_factory=dict)


class ImpressionBatchIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    product_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)




class BuyerEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    merchant_id: uuid.UUID
    merchant_product_id: uuid.UUID | None
    event_type: EventTypeLiteral
    context: dict
    billed: bool
    created_at: datetime


# ── Ledger entries (merchant-facing read) ──────────────────────────────────

class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    wallet_id: uuid.UUID
    related_event_id: uuid.UUID | None
    related_txn_id: uuid.UUID | None
    entry_type: LedgerEntryTypeLiteral
    amount: float
    reason: str
    balance_after: float
    notes: str | None
    created_at: datetime


class PaginatedLedger(BaseModel):
    items: list[LedgerEntryOut]
    total: int
    limit: int
    offset: int
