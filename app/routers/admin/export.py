"""Admin CSV export router (Requirement 21).

Exposes a single generic endpoint — ``GET /admin/export/{resource}.csv`` — that
streams any supported admin listing as a CSV file. Each exportable resource is
declared once in :data:`EXPORT_RESOURCES`, keyed by its URL slug, mapping the
slug to everything :func:`app.services.admin.export_service.export_csv` needs:

``(base_stmt, columns, sortable, searchable, soft_delete_col, read_permission,
dataset_name)``.

The export reuses the *same* ``ListParams`` + query builders the underlying
listing uses, so the exported rows correspond exactly to what the listing would
return under the same filters/search/sort (R21.1). A header row naming each
column is always emitted (R21.2), the export action/actor/dataset is audited
(R21.3), and access is gated by the **underlying listing's read permission**
(R21.4) — resolved dynamically from the registry rather than via a static
``require_permission`` dependency, because the permission depends on which
resource was requested.

| Method | Path                          | Permission              | Req       |
|--------|-------------------------------|-------------------------|-----------|
| GET    | `/export/{resource}.csv`      | resource's read perm    | 21.1–21.4 |

An unknown resource slug yields HTTP 404; a missing read permission yields
HTTP 403.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.database import get_db
from app.models.invoice import Invoice
from app.models.merchant import Merchant
from app.models.merchant_product import MerchantProduct
from app.models.redeem_code import RedeemCode
from app.models.support import SupportTicket
from app.models.user import User
from app.services.admin.export_service import ExportColumn, export_csv
from app.services.admin.listing import ListParams
from app.services.admin.rbac_service import AdminRBACService, is_permission_satisfied
from app.utils.admin_dependencies import CurrentAdmin

router = APIRouter(prefix="/admin", tags=["admin-export"])

DBSession = Annotated[AsyncSession, Depends(get_db)]

#: Query-param names that control listing behaviour rather than acting as
#: equality filters. These are never treated as conjunctive filter keys.
_RESERVED_PARAMS = frozenset({"page", "page_size", "search", "sort", "include_deleted"})

#: String values that select the ``include_deleted`` behaviour.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class ExportResource:
    """Everything needed to export one admin listing as CSV.

    Attributes:
        base_stmt: Factory returning a fresh ``select(...)`` for the resource.
            A factory (rather than a shared statement) keeps each request's
            query independent.
        columns: Ordered ``(header, accessor)`` pairs defining the CSV columns.
        sortable: Sort/filter whitelist shared with the listing endpoint.
        searchable: Columns OR-matched (ILIKE) against the ``search`` term.
        soft_delete_col: Nullable deletion-timestamp column, or ``None`` when the
            resource does not support soft-delete.
        read_permission: The permission key gating the underlying listing — and
            therefore the export (R21.4).
        dataset_name: Audit ``target_type`` and CSV filename stem.
    """

    base_stmt: Callable[[], Select]
    columns: Sequence[ExportColumn]
    sortable: dict[str, ColumnElement]
    searchable: Sequence[ColumnElement]
    soft_delete_col: ColumnElement | None
    read_permission: str
    dataset_name: str


# ---------------------------------------------------------------------------
# Per-resource sort/search whitelists (mirroring each listing service exactly).
# ---------------------------------------------------------------------------

_USER_SORTABLE: dict[str, ColumnElement] = {
    "full_name": User.full_name,
    "email": User.email,
    "is_active": User.is_active,
    "credit_balance": User.credit_balance,
    "created_at": User.created_at,
}
_USER_SEARCHABLE = (User.full_name, User.email)

_MERCHANT_SORTABLE: dict[str, ColumnElement] = {
    "display_name": Merchant.display_name,
    "legal_name": Merchant.legal_name,
    "status": Merchant.status,
    "is_kyc_completed": Merchant.is_kyc_completed,
    "created_at": Merchant.created_at,
}
_MERCHANT_SEARCHABLE = (Merchant.display_name, Merchant.legal_name)

_PRODUCT_SORTABLE: dict[str, ColumnElement] = {
    "title": MerchantProduct.title,
    "price": MerchantProduct.in_app_price,
    "status": MerchantProduct.status,
    "created_at": MerchantProduct.created_at,
    "merchant_id": MerchantProduct.merchant_id,
}
_PRODUCT_SEARCHABLE = (MerchantProduct.title,)

_INVOICE_SORTABLE: dict[str, ColumnElement] = {
    "number": Invoice.number,
    "merchant_id": Invoice.merchant_id,
    "amount": Invoice.amount,
    "status": Invoice.status,
    "issue_date": Invoice.issue_date,
    "due_date": Invoice.due_date,
    "created_at": Invoice.created_at,
}
_INVOICE_SEARCHABLE = (Invoice.number,)

_REDEEM_SORTABLE: dict[str, ColumnElement] = {
    "value": RedeemCode.value,
    "status": RedeemCode.status,
    "expires_at": RedeemCode.expires_at,
    "redeemed_at": RedeemCode.redeemed_at,
    "created_at": RedeemCode.created_at,
}
_REDEEM_SEARCHABLE = (RedeemCode.code,)

_SUPPORT_SORTABLE: dict[str, ColumnElement] = {
    "subject": SupportTicket.subject,
    "status": SupportTicket.status,
    "priority": SupportTicket.priority,
    "requester_type": SupportTicket.requester_type,
    "sla_due_at": SupportTicket.sla_due_at,
    "created_at": SupportTicket.created_at,
}
_SUPPORT_SEARCHABLE = (SupportTicket.subject,)


#: Registry of exportable resources keyed by URL slug. Adding a new export is a
#: single entry here — the route, header row, filtering, auditing, and
#: permission gating are all driven from this table.
EXPORT_RESOURCES: dict[str, ExportResource] = {
    "users": ExportResource(
        base_stmt=lambda: select(User),
        columns=[
            ("id", lambda u: u.id),
            ("full_name", lambda u: u.full_name),
            ("email", lambda u: u.email),
            ("is_active", lambda u: u.is_active),
            ("credit_balance", lambda u: u.credit_balance),
            ("referred_by_code", lambda u: u.referred_by_code),
            ("created_at", lambda u: u.created_at),
        ],
        sortable=_USER_SORTABLE,
        searchable=_USER_SEARCHABLE,
        soft_delete_col=None,
        read_permission="users.read",
        dataset_name="users",
    ),
    "merchants": ExportResource(
        base_stmt=lambda: select(Merchant),
        columns=[
            ("id", lambda m: m.id),
            ("slug", lambda m: m.slug),
            ("display_name", lambda m: m.display_name),
            ("legal_name", lambda m: m.legal_name),
            ("status", lambda m: m.status),
            ("country", lambda m: m.country),
            ("is_kyc_completed", lambda m: m.is_kyc_completed),
            ("support_email", lambda m: m.support_email),
            ("created_at", lambda m: m.created_at),
        ],
        sortable=_MERCHANT_SORTABLE,
        searchable=_MERCHANT_SEARCHABLE,
        soft_delete_col=None,
        read_permission="merchants.read",
        dataset_name="merchants",
    ),
    "products": ExportResource(
        base_stmt=lambda: select(MerchantProduct),
        columns=[
            ("id", lambda p: p.id),
            ("merchant_id", lambda p: p.merchant_id),
            ("sku", lambda p: p.sku),
            ("title", lambda p: p.title),
            ("status", lambda p: p.status),
            ("price", lambda p: p.in_app_price),
            ("in_app_stock", lambda p: p.in_app_stock),
            ("created_at", lambda p: p.created_at),
        ],
        sortable=_PRODUCT_SORTABLE,
        searchable=_PRODUCT_SEARCHABLE,
        soft_delete_col=None,
        read_permission="products.read",
        dataset_name="products",
    ),
    "invoices": ExportResource(
        base_stmt=lambda: select(Invoice),
        columns=[
            ("id", lambda i: i.id),
            ("merchant_id", lambda i: i.merchant_id),
            ("number", lambda i: i.number),
            ("amount", lambda i: i.amount),
            ("currency", lambda i: i.currency),
            ("status", lambda i: i.status),
            ("issue_date", lambda i: i.issue_date),
            ("due_date", lambda i: i.due_date),
            ("paid_at", lambda i: i.paid_at),
            ("created_at", lambda i: i.created_at),
        ],
        sortable=_INVOICE_SORTABLE,
        searchable=_INVOICE_SEARCHABLE,
        soft_delete_col=Invoice.deleted_at,
        read_permission="invoices.read",
        dataset_name="invoices",
    ),
    "redeem-codes": ExportResource(
        base_stmt=lambda: select(RedeemCode),
        columns=[
            ("id", lambda c: c.id),
            ("code", lambda c: c.code),
            ("value", lambda c: c.value),
            ("currency", lambda c: c.currency),
            ("status", lambda c: c.status),
            ("expires_at", lambda c: c.expires_at),
            ("redeemed_by", lambda c: c.redeemed_by),
            ("redeemed_at", lambda c: c.redeemed_at),
            ("batch_id", lambda c: c.batch_id),
            ("created_at", lambda c: c.created_at),
        ],
        sortable=_REDEEM_SORTABLE,
        searchable=_REDEEM_SEARCHABLE,
        soft_delete_col=None,
        read_permission="redeem.read",
        dataset_name="redeem-codes",
    ),
    "support-tickets": ExportResource(
        base_stmt=lambda: select(SupportTicket),
        columns=[
            ("id", lambda t: t.id),
            ("subject", lambda t: t.subject),
            ("requester_type", lambda t: t.requester_type),
            ("requester_id", lambda t: t.requester_id),
            ("status", lambda t: t.status),
            ("priority", lambda t: t.priority),
            ("sla_due_at", lambda t: t.sla_due_at),
            ("created_at", lambda t: t.created_at),
        ],
        sortable=_SUPPORT_SORTABLE,
        searchable=_SUPPORT_SEARCHABLE,
        soft_delete_col=SupportTicket.deleted_at,
        read_permission="support.read",
        dataset_name="support-tickets",
    ),
}


def _resolve_resource(resource: str) -> ExportResource:
    """Look up an exportable resource by slug, or raise HTTP 404."""
    entry = EXPORT_RESOURCES.get(resource)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown export resource: {resource!r}",
        )
    return entry


def _build_params(request: Request, entry: ExportResource) -> ListParams:
    """Translate the request's query string into :class:`ListParams`.

    ``search``/``sort``/``include_deleted`` are honoured directly; every other
    query parameter whose name is a whitelisted (sortable) field becomes a
    conjunctive equality filter, mirroring the underlying listing's filter
    contract (R21.1). Unknown parameters are ignored.
    """
    qp = request.query_params
    filters: dict[str, object] = {}
    for key in entry.sortable:
        if key in _RESERVED_PARAMS:
            continue
        value = qp.get(key)
        if value is not None:
            filters[key] = value

    include_deleted = (qp.get("include_deleted") or "").lower() in _TRUTHY
    return ListParams(
        search=qp.get("search"),
        sort=qp.get("sort"),
        filters=filters,
        include_deleted=include_deleted,
    )


async def _require_read_permission(
    db: AsyncSession, admin, permission: str
) -> None:
    """Enforce the underlying listing's read permission (R21.4).

    Mirrors :func:`app.utils.admin_dependencies.require_permission` but resolves
    the required permission dynamically from the export registry. The wildcard
    ``*`` (Super Admin) satisfies any requirement. Raises HTTP 403 otherwise.
    """
    effective = await AdminRBACService(db).load_effective_permissions(admin.id)
    if not is_permission_satisfied(effective, (permission,)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions",
        )


@router.get("/export/{resource}.csv")
async def export_resource_csv(
    resource: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
) -> StreamingResponse:
    """Stream a supported admin listing as a CSV file (R21.1–R21.4).

    The same filters/search/sort the listing accepts are applied to the export
    (R21.1); a header row names each column (R21.2); the export is audited with
    the acting admin and dataset (R21.3); and access requires the underlying
    listing's read permission (R21.4). An unknown ``resource`` slug yields
    HTTP 404; a missing permission yields HTTP 403; an unsupported ``sort`` field
    yields HTTP 422 (from the export service).
    """
    entry = _resolve_resource(resource)
    await _require_read_permission(db, admin, entry.read_permission)

    params = _build_params(request, entry)
    response = await export_csv(
        db,
        entry.base_stmt(),
        params=params,
        columns=entry.columns,
        sortable=entry.sortable,
        searchable=entry.searchable,
        soft_delete_col=entry.soft_delete_col,
        actor=admin,
        dataset_name=entry.dataset_name,
    )
    # Persist the audit entry recorded by export_csv (the session does not
    # auto-commit). Row data is already materialised, so the stream is unaffected.
    await db.commit()
    return response
