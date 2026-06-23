"""Alert Center service — five operational counters + resolution (Requirement 6).

``AlertService`` replaces the five faked counters in the Admin Panel's zustand
store (``store.ts``) with real, queryable aggregates and the actions that clear
them. Each category maps to an underlying source model and a single SQL
predicate that is shared between counting (:meth:`counters`), listing
(:meth:`list_items`), and resolution (:meth:`resolve`) so the three always agree
(a resolved record stops satisfying the predicate and therefore stops counting).

Count definitions (design "Alert Center (R6)"):

| Category               | Source           | Predicate                                              |
|------------------------|------------------|--------------------------------------------------------|
| ``fraud``              | ``FraudAlert``   | ``status == 'open'``                                   |
| ``overdue_invoices``   | ``Invoice``      | ``status == 'unpaid' AND due_date < now`` (not deleted)|
| ``sla_breaches``       | ``SupportTicket``| ``status != 'resolved' AND sla_due_at < now`` (not del)|
| ``failed_generations`` | ``BuyerEvent``   | failed ``ai_image_generation`` in window, unacknowledged|
| ``low_balance_wallets``| ``Wallet``       | ``balance < threshold AND status != 'frozen'`` (R6.10) |

Approximations (documented because the source models lack first-class fields):

* **failed_generations** — :class:`~app.models.event.BuyerEvent` has no status or
  acknowledgement column, so a failed generation is approximated as an
  ``ai_image_generation`` event whose JSONB ``context`` carries a failure marker
  (``context.status == 'failed'`` or ``context.failed == true``). "Recent" is
  bounded to :data:`FAILED_GENERATION_WINDOW`. Acknowledgement is recorded back
  into ``context`` (``context.acknowledged == 'true'``); resolving sets it, which
  removes the row from the count without deleting telemetry.
* **low_balance_wallets** — :class:`~app.models.wallet.Wallet` has no
  acknowledgement column, so resolution is *acknowledge-style*: the wallet is
  moved to ``status == 'frozen'`` (the risk has been actioned), which removes it
  from the count while leaving the balance untouched. The count itself still
  honours R6.10 exactly (strictly ``< threshold``); the ``status != 'frozen'``
  guard only hides wallets an admin has already acknowledged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status as http_status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.admin import FraudAlert, FraudAlertStatus
from app.models.event import BuyerEvent, EventType
from app.models.invoice import Invoice, InvoiceStatus
from app.models.support import SupportTicket, SupportTicketStatus
from app.models.wallet import Wallet, WalletStatus
from app.schemas.admin.alerts import AlertCategory, AlertCounters, AlertItem
from app.services.admin.listing import ListParams, Page, paginate

#: Lookback window for the failed-AI-generation counter. ``BuyerEvent`` rows
#: older than this are treated as historical and excluded from the live alert.
FAILED_GENERATION_WINDOW = timedelta(days=7)

#: Source ORM model backing each alert category (used for listing/sorting).
CATEGORY_MODEL: dict[AlertCategory, Any] = {
    AlertCategory.FRAUD: FraudAlert,
    AlertCategory.OVERDUE_INVOICES: Invoice,
    AlertCategory.SLA_BREACHES: SupportTicket,
    AlertCategory.FAILED_GENERATIONS: BuyerEvent,
    AlertCategory.LOW_BALANCE_WALLETS: Wallet,
}


def _now() -> datetime:
    """Current UTC time (timezone-aware to match the TIMESTAMP(tz) columns)."""
    return datetime.now(timezone.utc)


def _failed_generation_marker():
    """SQL predicate: the JSONB ``context`` flags this generation as failed.

    Matches either ``context.status == 'failed'`` or a truthy ``context.failed``
    flag, tolerating absent keys (treated as not-failed).
    """
    return or_(
        func.coalesce(BuyerEvent.context["status"].astext, "") == "failed",
        func.coalesce(BuyerEvent.context["failed"].astext, "") == "true",
    )


def _not_acknowledged():
    """SQL predicate: the generation has not been acknowledged via ``context``."""
    return func.coalesce(BuyerEvent.context["acknowledged"].astext, "false") != "true"


class AlertService:
    """Compute alert counters, list contributing records, and resolve them (R6)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- Predicate / query construction -----------------------------------

    def _threshold(self):
        """The wallet-risk threshold as an INR amount (R6.10, default ₹100)."""
        return get_settings().ADMIN_WALLET_RISK_THRESHOLD

    def _base_stmt(self, category: AlertCategory, now: datetime) -> Select:
        """Build the ``select`` of source rows currently in ``category``.

        The same predicate underpins counting, listing, and the
        no-longer-contributes guarantee of resolution.
        """
        if category is AlertCategory.FRAUD:
            return select(FraudAlert).where(
                FraudAlert.status == FraudAlertStatus.OPEN.value
            )
        if category is AlertCategory.OVERDUE_INVOICES:
            return select(Invoice).where(
                Invoice.deleted_at.is_(None),
                Invoice.status == InvoiceStatus.UNPAID.value,
                Invoice.due_date < now,
            )
        if category is AlertCategory.SLA_BREACHES:
            return select(SupportTicket).where(
                SupportTicket.deleted_at.is_(None),
                SupportTicket.status != SupportTicketStatus.RESOLVED.value,
                SupportTicket.sla_due_at.is_not(None),
                SupportTicket.sla_due_at < now,
            )
        if category is AlertCategory.FAILED_GENERATIONS:
            return select(BuyerEvent).where(
                BuyerEvent.event_type == EventType.AI_IMAGE_GENERATION.value,
                _failed_generation_marker(),
                _not_acknowledged(),
                BuyerEvent.created_at >= now - FAILED_GENERATION_WINDOW,
            )
        if category is AlertCategory.LOW_BALANCE_WALLETS:
            return select(Wallet).where(
                Wallet.balance < self._threshold(),
                Wallet.status != WalletStatus.FROZEN.value,
            )
        # Unreachable: AlertCategory is exhaustively handled above.
        raise ValueError(f"unhandled alert category: {category!r}")

    async def _count(self, category: AlertCategory, now: datetime) -> int:
        """Return the non-negative count of rows contributing to ``category``."""
        stmt = self._base_stmt(category, now).order_by(None).subquery()
        total = (await self.db.execute(select(func.count()).select_from(stmt))).scalar_one()
        return int(total)

    # -- Counters (R6.1, R6.2, R6.10) -------------------------------------

    async def counters(self) -> AlertCounters:
        """Return the five non-negative category counts from live sources (R6.1, R6.2)."""
        now = _now()
        return AlertCounters(
            fraud_alerts=await self._count(AlertCategory.FRAUD, now),
            overdue_invoices=await self._count(AlertCategory.OVERDUE_INVOICES, now),
            sla_breaches=await self._count(AlertCategory.SLA_BREACHES, now),
            failed_generations=await self._count(AlertCategory.FAILED_GENERATIONS, now),
            wallets_below_threshold=await self._count(
                AlertCategory.LOW_BALANCE_WALLETS, now
            ),
        )

    # -- Item listing (R6.3) ----------------------------------------------

    async def list_items(self, category: AlertCategory, params: ListParams) -> Page:
        """Return a paginated page of the underlying records for ``category`` (R6.3).

        Ordered by recency (``created_at`` descending) by default; the listing
        engine still honours an explicit whitelisted sort and pagination clamps.
        """
        now = _now()
        base = self._base_stmt(category, now)
        model = CATEGORY_MODEL[category]
        sortable = {"created_at": model.created_at}
        if params.sort is None:
            params = ListParams(
                page=params.page,
                page_size=params.page_size,
                search=params.search,
                sort="-created_at",
                filters=params.filters,
                include_deleted=params.include_deleted,
            )
        return await paginate(
            self.db,
            base,
            params=params,
            sortable=sortable,
            searchable=(),
        )

    def to_item(self, category: AlertCategory, record: Any) -> AlertItem:
        """Project a source row into the uniform :class:`AlertItem` shape (R6.3)."""
        if category is AlertCategory.FRAUD:
            return AlertItem(
                id=str(record.id),
                category=category,
                title=f"Fraud alert: {record.reason}",
                created_at=record.created_at,
                detail={
                    "subject_type": record.subject_type,
                    "subject_id": record.subject_id,
                    "reason": record.reason,
                    "status": record.status,
                },
            )
        if category is AlertCategory.OVERDUE_INVOICES:
            return AlertItem(
                id=str(record.id),
                category=category,
                title=f"Overdue invoice {record.number}",
                created_at=record.created_at,
                detail={
                    "number": record.number,
                    "merchant_id": str(record.merchant_id),
                    "amount": str(record.amount),
                    "currency": record.currency,
                    "status": record.status,
                    "due_date": record.due_date.isoformat() if record.due_date else None,
                },
            )
        if category is AlertCategory.SLA_BREACHES:
            return AlertItem(
                id=str(record.id),
                category=category,
                title=f"SLA breach: {record.subject}",
                created_at=record.created_at,
                detail={
                    "subject": record.subject,
                    "status": record.status,
                    "priority": record.priority,
                    "sla_due_at": record.sla_due_at.isoformat() if record.sla_due_at else None,
                },
            )
        if category is AlertCategory.FAILED_GENERATIONS:
            return AlertItem(
                id=str(record.id),
                category=category,
                title="Failed AI image generation",
                created_at=record.created_at,
                detail={
                    "merchant_id": str(record.merchant_id),
                    "user_id": str(record.user_id),
                    "event_type": record.event_type,
                    "context": record.context,
                },
            )
        if category is AlertCategory.LOW_BALANCE_WALLETS:
            return AlertItem(
                id=str(record.id),
                category=category,
                title="Wallet below risk threshold",
                created_at=record.created_at,
                detail={
                    "merchant_id": str(record.merchant_id),
                    "balance": str(record.balance),
                    "currency": record.currency,
                    "status": record.status,
                },
            )
        raise ValueError(f"unhandled alert category: {category!r}")

    # -- Resolution (R6.5–R6.9) -------------------------------------------

    async def resolve(
        self,
        category: AlertCategory,
        item_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
    ) -> None:
        """Resolve one alert item so it no longer contributes to its count (R6.5).

        Dispatches to the per-category resolution. Raises HTTP 404 when the
        identifier does not exist (or does not name an item of this category;
        R6.6) and HTTP 409 when the item is already resolved/acknowledged
        (R6.7); in both cases no count changes. The acting admin is recorded by
        the caller's ``audited(...)`` dependency, and (for fraud) on the row.
        """
        if category is AlertCategory.FRAUD:
            await self._resolve_fraud(item_id, actor_id=actor_id)
        elif category is AlertCategory.OVERDUE_INVOICES:
            await self._resolve_invoice(item_id)
        elif category is AlertCategory.SLA_BREACHES:
            await self._resolve_ticket(item_id)
        elif category is AlertCategory.FAILED_GENERATIONS:
            await self._resolve_generation(item_id)
        elif category is AlertCategory.LOW_BALANCE_WALLETS:
            await self._resolve_wallet(item_id)
        else:  # pragma: no cover - exhaustive above
            raise ValueError(f"unhandled alert category: {category!r}")
        await self.db.commit()

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="alert item not found"
        )

    @staticmethod
    def _already_resolved() -> HTTPException:
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail="alert item already resolved"
        )

    async def _resolve_fraud(self, item_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        record = await self.db.get(FraudAlert, item_id)
        if record is None:
            raise self._not_found()
        if record.status == FraudAlertStatus.RESOLVED.value:
            raise self._already_resolved()
        record.status = FraudAlertStatus.RESOLVED.value
        record.resolved_by = actor_id
        record.resolved_at = _now()

    async def _resolve_invoice(self, item_id: uuid.UUID) -> None:
        record = await self.db.get(Invoice, item_id)
        if record is None or record.deleted_at is not None:
            raise self._not_found()
        if record.status != InvoiceStatus.UNPAID.value:
            # Already paid or voided → no longer an overdue alert item.
            raise self._already_resolved()
        record.status = InvoiceStatus.PAID.value
        record.paid_at = _now()

    async def _resolve_ticket(self, item_id: uuid.UUID) -> None:
        record = await self.db.get(SupportTicket, item_id)
        if record is None or record.deleted_at is not None:
            raise self._not_found()
        if record.status == SupportTicketStatus.RESOLVED.value:
            raise self._already_resolved()
        record.status = SupportTicketStatus.RESOLVED.value

    async def _resolve_generation(self, item_id: uuid.UUID) -> None:
        record = await self.db.get(BuyerEvent, item_id)
        if record is None or record.event_type != EventType.AI_IMAGE_GENERATION.value:
            raise self._not_found()
        context = dict(record.context or {})
        failed = context.get("status") == "failed" or context.get("failed") is True
        if not failed:
            # Not a failed generation → not an item of this category.
            raise self._not_found()
        if context.get("acknowledged") == "true":
            raise self._already_resolved()
        context["acknowledged"] = "true"
        # Reassign so SQLAlchemy detects the JSONB mutation.
        record.context = context

    async def _resolve_wallet(self, item_id: uuid.UUID) -> None:
        record = await self.db.get(Wallet, item_id)
        if record is None:
            raise self._not_found()
        if record.status == WalletStatus.FROZEN.value:
            # Already acknowledged (frozen) → no longer contributing.
            raise self._already_resolved()
        if record.balance >= self._threshold():
            # Balance is healthy → not a low-balance alert item.
            raise self._not_found()
        record.status = WalletStatus.FROZEN.value
