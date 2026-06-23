"""Executive dashboard overview + activity feed (real aggregate data).

``DashboardOverviewService`` backs two read-only endpoints on the executive
dashboard router. It replaces the Admin Panel's previously faked
*SnapshotWidgets*, *funnel*, and *Realtime Feed* figures with real aggregates
computed via simple ``COUNT``/``SUM`` queries over existing domain tables.

* :meth:`overview` returns a :class:`~app.schemas.admin.overview.DashboardOverview`
  bundle of small count groups: merchant + wallet health, store health, support
  tickets, invoices, trust & safety (fraud), referral, the acquisition funnel,
  and redeem codes.
* :meth:`activity` returns the most recent ``limit`` :class:`AuditLog` entries
  as a lightweight list, newest first.

Time comparisons (overdue invoices, SLA breaches) compare against ``now``. Some
of the columns involved are declared ``TIMESTAMP WITHOUT TIME ZONE`` (naive),
and asyncpg cannot bind a tz-aware datetime to a naive timestamp parameter, so
the ``now`` bound is coerced to naive UTC before binding — mirroring the
``_to_naive_utc`` pattern in :mod:`app.services.admin.analytics_service`.

All queries use portable SQL (``select(func.count())`` over a filtered table),
so the service runs identically on Postgres and the test SQLite engine. Every
returned value is a non-negative integer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.admin import AuditLog, FraudAlert, FraudAlertStatus
from app.models.cart import CartItem
from app.models.event import BuyerEvent, EventType
from app.models.invoice import Invoice, InvoiceStatus
from app.models.lead import Order, OrderStatus
from app.models.merchant import Merchant, MerchantStatus
from app.models.redeem_code import RedeemCode, RedeemCodeStatus
from app.models.store import Store, StoreStatus
from app.models.support import SupportTicket, SupportTicketStatus
from app.models.user import User
from app.models.wallet import Wallet, WalletStatus
from app.schemas.admin.overview import (
    ActivityEntry,
    DashboardOverview,
    Funnel,
    InvoiceSnapshot,
    MerchantHealth,
    RedeemSnapshot,
    ReferralSnapshot,
    StoreHealth,
    SupportSnapshot,
    TrustSafety,
)

#: Default number of audit-log entries returned by the activity feed.
DEFAULT_ACTIVITY_LIMIT = 12


def _to_naive_utc(dt: datetime) -> datetime:
    """Coerce a tz-aware datetime to naive UTC for binding against naive columns.

    Several time columns compared here (``invoices.due_date`` is tz-aware, but
    ``support_tickets.sla_due_at`` semantics and other naive ``created_at``
    columns) require a naive-UTC bound so asyncpg can bind it to ``TIMESTAMP
    WITHOUT TIME ZONE`` parameters. Returns ``dt`` unchanged when already naive.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class DashboardOverviewService:
    """Compute the executive dashboard overview + activity feed (read-only)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _count(self, model, *filters) -> int:
        """Return the non-negative count of ``model`` rows matching ``filters``."""
        stmt = select(func.count()).select_from(model)
        if filters:
            stmt = stmt.where(*filters)
        total = (await self.db.execute(stmt)).scalar_one()
        return int(total or 0)

    async def overview(self, *, now: datetime | None = None) -> DashboardOverview:
        """Aggregate every snapshot group into a :class:`DashboardOverview`."""
        now = now or datetime.now(timezone.utc)
        now_naive = _to_naive_utc(now)
        threshold = get_settings().ADMIN_WALLET_RISK_THRESHOLD

        merchant_health = MerchantHealth(
            total=await self._count(Merchant),
            active=await self._count(
                Merchant, Merchant.status == MerchantStatus.ACTIVE.value
            ),
            suspended=await self._count(
                Merchant, Merchant.status == MerchantStatus.SUSPENDED.value
            ),
            low_balance_wallets=await self._count(Wallet, Wallet.balance < threshold),
            frozen_wallets=await self._count(
                Wallet, Wallet.status == WalletStatus.FROZEN.value
            ),
        )

        store_health = StoreHealth(
            total=await self._count(Store),
            active=await self._count(Store, Store.status == StoreStatus.ACTIVE.value),
            inactive=await self._count(
                Store, Store.status == StoreStatus.INACTIVE.value
            ),
            suspended=await self._count(
                Store, Store.status == StoreStatus.SUSPENDED.value
            ),
        )

        support = SupportSnapshot(
            open=await self._count(
                SupportTicket, SupportTicket.status == SupportTicketStatus.OPEN.value
            ),
            pending=await self._count(
                SupportTicket,
                SupportTicket.status == SupportTicketStatus.PENDING.value,
            ),
            resolved=await self._count(
                SupportTicket,
                SupportTicket.status == SupportTicketStatus.RESOLVED.value,
            ),
            sla_breaches=await self._count(
                SupportTicket,
                SupportTicket.status != SupportTicketStatus.RESOLVED.value,
                SupportTicket.sla_due_at.is_not(None),
                SupportTicket.sla_due_at < now_naive,
            ),
        )

        invoices = InvoiceSnapshot(
            total=await self._count(Invoice, Invoice.deleted_at.is_(None)),
            unpaid=await self._count(
                Invoice,
                Invoice.deleted_at.is_(None),
                Invoice.status == InvoiceStatus.UNPAID.value,
            ),
            paid=await self._count(
                Invoice,
                Invoice.deleted_at.is_(None),
                Invoice.status == InvoiceStatus.PAID.value,
            ),
            overdue=await self._count(
                Invoice,
                Invoice.deleted_at.is_(None),
                Invoice.status == InvoiceStatus.UNPAID.value,
                Invoice.due_date < now_naive,
            ),
        )

        trust_safety = TrustSafety(
            open_fraud_alerts=await self._count(
                FraudAlert, FraudAlert.status == FraudAlertStatus.OPEN.value
            ),
            resolved_fraud_alerts=await self._count(
                FraudAlert, FraudAlert.status == FraudAlertStatus.RESOLVED.value
            ),
        )

        referral = ReferralSnapshot(
            referred_users=await self._count(
                User, User.referred_by_code.is_not(None)
            ),
            total_users=await self._count(User),
        )

        funnel = Funnel(
            impressions=await self._count(
                BuyerEvent, BuyerEvent.event_type == EventType.IMPRESSION.value
            ),
            clicks=await self._count(
                BuyerEvent, BuyerEvent.event_type == EventType.CLICK.value
            ),
            add_to_cart=await self._count(CartItem),
            purchases=await self._count(
                Order, Order.status == OrderStatus.COMPLETED.value
            ),
        )

        redeem = RedeemSnapshot(
            active_codes=await self._count(
                RedeemCode, RedeemCode.status == RedeemCodeStatus.ACTIVE.value
            ),
            redeemed_codes=await self._count(
                RedeemCode, RedeemCode.status == RedeemCodeStatus.REDEEMED.value
            ),
        )

        return DashboardOverview(
            merchant_health=merchant_health,
            store_health=store_health,
            support=support,
            invoices=invoices,
            trust_safety=trust_safety,
            referral=referral,
            funnel=funnel,
            redeem=redeem,
        )

    async def activity(
        self, *, limit: int = DEFAULT_ACTIVITY_LIMIT
    ) -> list[ActivityEntry]:
        """Return the most recent ``limit`` audit-log entries, newest first."""
        limit = max(1, int(limit))
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [
            ActivityEntry(
                id=str(row.id),
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                outcome=row.outcome,
                created_at=row.created_at,
            )
            for row in rows
        ]
