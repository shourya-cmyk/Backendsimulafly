"""Request/response schemas for the Alert Center (Requirement 6).

The Alert Center surfaces five operational alert categories the Admin Panel's
zustand store currently fakes, each now backed by a real, queryable source
(see :mod:`app.services.admin.alert_service` for the count definitions):

* ``fraud`` — open :class:`~app.models.admin.FraudAlert` rows.
* ``overdue_invoices`` — unpaid, past-due :class:`~app.models.invoice.Invoice`.
* ``sla_breaches`` — unresolved, past-SLA :class:`~app.models.support.SupportTicket`.
* ``failed_generations`` — failed, unacknowledged ``ai_image_generation``
  :class:`~app.models.event.BuyerEvent` rows (approximated; see service).
* ``low_balance_wallets`` — :class:`~app.models.wallet.Wallet` rows whose balance
  is strictly below ``ADMIN_WALLET_RISK_THRESHOLD`` (R6.10).

:class:`AlertCounters` mirrors the UI store field names (``fraudAlerts`` …
``walletsBelow100``) via aliases so the panel can consume it directly. Item
listings use the uniform :class:`~app.schemas.admin.listing.ListingEnvelope`
parameterised with :class:`AlertItem`.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertCategory(str, enum.Enum):
    """The five recognised alert categories (R6.3, R6.4).

    Used as the ``{category}`` path parameter; FastAPI rejects any value outside
    this whitelist with HTTP 422 (R6.4).
    """

    FRAUD = "fraud"
    OVERDUE_INVOICES = "overdue_invoices"
    SLA_BREACHES = "sla_breaches"
    FAILED_GENERATIONS = "failed_generations"
    LOW_BALANCE_WALLETS = "low_balance_wallets"


class AlertCounters(BaseModel):
    """Non-negative integer count for each of the five alert categories (R6.1).

    Field names mirror the Admin Panel store (``store.ts``) via camelCase
    aliases so the response can populate the UI counters directly; the response
    is serialised using those aliases.
    """

    model_config = ConfigDict(populate_by_name=True)

    fraud_alerts: int = Field(alias="fraudAlerts", ge=0)
    overdue_invoices: int = Field(alias="overdueInvoices", ge=0)
    sla_breaches: int = Field(alias="slaBreaches", ge=0)
    failed_generations: int = Field(alias="failedGenerations", ge=0)
    wallets_below_threshold: int = Field(alias="walletsBelow100", ge=0)


class AlertItem(BaseModel):
    """One underlying record currently contributing to an alert category (R6.3).

    A uniform projection over the heterogeneous source rows: ``id`` is the
    underlying record's identifier (the value accepted by the resolve endpoint),
    ``category`` echoes the queried category, ``title`` is a short human-readable
    summary, ``created_at`` is the record's creation timestamp when available,
    and ``detail`` carries the salient source fields for that category.
    """

    id: str
    category: AlertCategory
    title: str
    created_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class AlertResolution(BaseModel):
    """Confirmation returned when an alert item is resolved (R6.5).

    ``resolved`` is always ``True`` on success; ``id`` and ``category`` identify
    the affected record so the panel can decrement the relevant counter.
    """

    id: str
    category: AlertCategory
    resolved: bool = True
