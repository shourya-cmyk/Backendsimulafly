"""Request/response schemas for the admin merchant-wallet endpoints (Requirement 14).

These back the wallet router (``app/routers/admin/wallets.py``):

* :class:`WalletListItem` — one row in the paginated wallet listing (R14.1),
  wrapped by the shared :class:`app.schemas.admin.listing.ListingEnvelope`.
* :class:`WalletTransactionItem` — one row in a wallet's transaction history
  (R14.7).
* :class:`WalletAdjustmentRequest` — the ``{direction, amount}`` body of a
  credit/debit adjustment (R14.3, R14.4).
* :class:`WalletAdjustmentResponse` — the result of an applied adjustment
  (R14.3–R14.6), echoing the affected wallet's new balance and the identifiers
  of the recorded ``Transaction`` / ``LedgerEntry``.

All monetary values are denominated in INR, consistent with the existing
``Wallet``/``Transaction``/``LedgerEntry`` models.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AdjustmentDirection(str, enum.Enum):
    """Direction of an admin wallet adjustment (R14.3, R14.4)."""

    CREDIT = "credit"
    DEBIT = "debit"


class WalletListItem(BaseModel):
    """A single row in the paginated wallet directory (R14.1).

    Includes the wallet identifier, the owning merchant (id + display name),
    balance, currency, status, and the wallet's low-balance threshold.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    merchant_name: str | None = None
    balance: Decimal
    currency: str
    status: str
    low_balance_threshold: Decimal
    last_recharged_at: datetime | None = None
    created_at: datetime


class WalletTransactionItem(BaseModel):
    """A single transaction in a wallet's history (R14.7)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    amount: Decimal
    currency: str
    payment_method: str | None = None
    gateway: str
    gateway_ref: str | None = None
    status: str
    created_at: datetime


class WalletAdjustmentRequest(BaseModel):
    """Body of a wallet credit/debit adjustment (R14.3, R14.4).

    ``amount`` must be strictly positive; a non-positive amount is rejected with
    HTTP 422 by request validation. ``direction`` selects credit (increase) or
    debit (decrease).
    """

    direction: AdjustmentDirection
    amount: Decimal = Field(gt=0, description="Positive INR amount to credit or debit")


class WalletAdjustmentResponse(BaseModel):
    """Result of an applied wallet adjustment (R14.3–R14.6)."""

    model_config = ConfigDict(from_attributes=True)

    wallet_id: uuid.UUID
    merchant_id: uuid.UUID
    direction: AdjustmentDirection
    amount: Decimal
    balance: Decimal
    currency: str
    status: str
    transaction_id: uuid.UUID
    ledger_entry_id: uuid.UUID
