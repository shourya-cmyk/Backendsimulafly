"""Response schemas for the executive dashboard overview + activity feed.

These back two read-only endpoints that power the Admin Panel's Executive
Dashboard *SnapshotWidgets*, the *funnel*, and the *Realtime Feed* with real
aggregate data (replacing the UI's previously faked figures):

* ``GET /admin/dashboard/executive/overview`` → :class:`DashboardOverview` — a
  bundle of small count/sum groups computed over existing domain tables
  (merchants, wallets, stores, support tickets, invoices, fraud alerts, users,
  buyer events, carts, orders, redeem codes).
* ``GET /admin/dashboard/executive/activity`` → ``list[ActivityEntry]`` — the
  most recent admin audit-log entries, newest first.

Every numeric field is a non-negative integer count (or summed total). The
shapes intentionally mirror the UI's widget/funnel field groups so the frontend
can be wired to match directly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MerchantHealth(BaseModel):
    """Merchant + wallet health snapshot."""

    total: int
    active: int
    suspended: int
    low_balance_wallets: int
    frozen_wallets: int


class StoreHealth(BaseModel):
    """Store status snapshot over ``Store.status``."""

    total: int
    active: int
    inactive: int
    suspended: int


class SupportSnapshot(BaseModel):
    """Support-ticket snapshot over ``SupportTicket.status`` (+ SLA breaches)."""

    open: int
    pending: int
    resolved: int
    sla_breaches: int


class InvoiceSnapshot(BaseModel):
    """Invoice snapshot over ``Invoice`` (soft-deleted rows excluded)."""

    total: int
    unpaid: int
    paid: int
    overdue: int


class TrustSafety(BaseModel):
    """Trust & safety snapshot over ``FraudAlert.status``."""

    open_fraud_alerts: int
    resolved_fraud_alerts: int


class ReferralSnapshot(BaseModel):
    """Referral snapshot over ``User`` (referred vs. total users)."""

    referred_users: int
    total_users: int


class Funnel(BaseModel):
    """Acquisition funnel: impressions → clicks → add-to-cart → purchases."""

    impressions: int
    clicks: int
    add_to_cart: int
    purchases: int


class RedeemSnapshot(BaseModel):
    """Redeem-code snapshot over ``RedeemCode.status``."""

    active_codes: int
    redeemed_codes: int


class DashboardOverview(BaseModel):
    """Aggregate snapshot powering the Executive Dashboard widgets + funnel.

    Each group is a small bundle of non-negative integer counts computed with
    simple ``COUNT``/``SUM`` queries over existing tables.
    """

    merchant_health: MerchantHealth
    store_health: StoreHealth
    support: SupportSnapshot
    invoices: InvoiceSnapshot
    trust_safety: TrustSafety
    referral: ReferralSnapshot
    funnel: Funnel
    redeem: RedeemSnapshot


class ActivityEntry(BaseModel):
    """One lightweight audit-log entry for the Realtime Feed.

    Projects the :class:`~app.models.admin.AuditLog` row to the fields the feed
    renders; ``target_id`` is the stringified target identifier (nullable).
    """

    id: str
    action: str
    target_type: str
    target_id: str | None = None
    outcome: str
    created_at: datetime
