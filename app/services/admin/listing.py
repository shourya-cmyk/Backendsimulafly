"""Shared listing engine for admin directory/list endpoints (R20, R21, R22).

This module provides one reusable async helper, :func:`paginate`, that every
admin directory/list endpoint calls. It centralises:

* **Pagination** — 1-based ``page`` with ``page_size`` clamped to
  ``ADMIN_MAX_PAGE_SIZE`` (R20.1, R20.2) and a consistent, bounded envelope
  (R20.3).
* **Search** — a case-insensitive ``ILIKE`` OR across a whitelist of
  searchable columns (R20.5 / R7.2, R8.2, R9.2, R10.2).
* **Filters** — conjunctive (AND) equality filters (R20.6).
* **Sort** — ordering by a whitelisted field, ascending (``"field"``) or
  descending (``"-field"``); an unsupported field is rejected with HTTP 422
  (R20.4).
* **Soft-delete exclusion** — rows whose ``deleted_at`` is set are hidden from
  default listings unless ``include_deleted`` is requested (R22.2).

The same :class:`ListParams` + query builders are reused by the CSV export
service (R21), keeping the listing and its export perfectly aligned.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings


def _default_page_size() -> int:
    """Resolve the configured default page size at instance-construction time."""
    return get_settings().ADMIN_DEFAULT_PAGE_SIZE


@dataclass
class ListParams:
    """Normalised listing query parameters shared by all admin list endpoints.

    Attributes:
        page: 1-based page index (clamped to >= 1 by :func:`paginate`).
        page_size: Requested records per page (clamped to
            ``[1, ADMIN_MAX_PAGE_SIZE]`` by :func:`paginate`). Defaults to
            ``ADMIN_DEFAULT_PAGE_SIZE``.
        search: Optional free-text term matched (ILIKE) across the endpoint's
            searchable columns.
        sort: Optional sort spec — ``"field"`` (ascending) or ``"-field"``
            (descending). The field must appear in the endpoint's sortable
            whitelist, otherwise HTTP 422 is raised.
        filters: Conjunctive filters. Keys may be a column expression or a
            string resolved against the sortable whitelist; values may be a
            scalar (equality) or a list/tuple/set (membership). ``None`` values
            are ignored.
        include_deleted: When False (default), soft-deleted rows are excluded.
    """

    page: int = 1
    page_size: int = field(default_factory=_default_page_size)
    search: str | None = None
    sort: str | None = None
    filters: dict[Any, Any] = field(default_factory=dict)
    include_deleted: bool = False


@dataclass
class Page:
    """Result of :func:`paginate` — a page of rows plus pagination metadata.

    Routers map this to a :class:`app.schemas.admin.ListingEnvelope` parameterised
    with their concrete item schema.
    """

    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    next_page: int | None


def apply_filters(
    base_stmt: Select,
    *,
    params: ListParams,
    sortable: dict[str, ColumnElement],
    searchable: Sequence[ColumnElement],
    soft_delete_col: ColumnElement | None = None,
) -> Select:
    """Apply soft-delete exclusion, search, and conjunctive filters (no sort/page).

    This is the shared filter core reused by both :func:`paginate` and the CSV
    export service so a listing and its export resolve to exactly the same row
    set under the same parameters (R21.1, Property 39).

    Args:
        base_stmt: A ``select(...)`` returning the entity (or row) to list.
        params: Normalised listing parameters.
        sortable: Whitelist used to resolve string filter keys to columns.
        searchable: Columns OR-matched (ILIKE) against ``params.search``.
        soft_delete_col: Nullable deletion-timestamp column; when provided and
            ``params.include_deleted`` is False, rows with a non-null value are
            excluded.

    Returns:
        The statement with soft-delete/search/filter predicates applied.
    """
    stmt = base_stmt

    # --- Soft-delete exclusion (R22.2) ---
    if soft_delete_col is not None and not params.include_deleted:
        stmt = stmt.where(soft_delete_col.is_(None))

    # --- Search: ILIKE OR across whitelisted columns (R20.5) ---
    if params.search and searchable:
        term = f"%{params.search}%"
        stmt = stmt.where(or_(*[col.ilike(term) for col in searchable]))

    # --- Conjunctive filters (R20.6) ---
    for key, value in params.filters.items():
        if value is None:
            continue
        column = sortable.get(key) if isinstance(key, str) else key
        if column is None:
            # Unknown string filter key — ignore rather than leak an error.
            continue
        if isinstance(value, (list, tuple, set)):
            members = list(value)
            if not members:
                continue
            stmt = stmt.where(column.in_(members))
        else:
            stmt = stmt.where(column == value)

    return stmt


def apply_sort(
    stmt: Select,
    *,
    params: ListParams,
    sortable: dict[str, ColumnElement],
) -> Select:
    """Apply a whitelisted sort spec to ``stmt`` (R20.4).

    Args:
        stmt: The statement to order.
        params: Normalised listing parameters (uses ``params.sort``).
        sortable: Whitelist mapping sortable field names to column expressions.

    Returns:
        The ordered statement (unchanged when ``params.sort`` is falsy).

    Raises:
        HTTPException: 422 when ``params.sort`` references a field that is not
            in ``sortable`` (R20.4).
    """
    if not params.sort:
        return stmt
    descending = params.sort.startswith("-")
    field_name = params.sort[1:] if descending else params.sort
    column = sortable.get(field_name)
    if column is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported sort field: {field_name!r}",
        )
    return stmt.order_by(column.desc() if descending else column.asc())


async def paginate(
    db: AsyncSession,
    base_stmt: Select,
    *,
    params: ListParams,
    sortable: dict[str, ColumnElement],
    searchable: Sequence[ColumnElement],
    soft_delete_col: ColumnElement | None = None,
) -> Page:
    """Apply search/filter/sort/soft-delete/pagination and return a :class:`Page`.

    Args:
        db: Async SQLAlchemy session.
        base_stmt: A ``select(...)`` returning the entity (or row) to list. Any
            joins/options the endpoint needs should already be applied.
        params: Normalised listing parameters.
        sortable: Whitelist mapping sortable field names to column expressions.
            Also used to resolve string filter keys.
        searchable: Columns OR-matched (ILIKE) against ``params.search``.
        soft_delete_col: Nullable deletion-timestamp column; when provided and
            ``params.include_deleted`` is False, rows with a non-null value are
            excluded.

    Returns:
        A :class:`Page` with the current-page ``items`` and pagination metadata.

    Raises:
        HTTPException: 422 when ``params.sort`` references a field that is not
            in ``sortable`` (R20.4).
    """
    settings = get_settings()

    # --- Clamp pagination inputs (R20.1, R20.2) ---
    page = params.page if params.page and params.page >= 1 else 1
    page_size = params.page_size if params.page_size and params.page_size >= 1 else 1
    if page_size > settings.ADMIN_MAX_PAGE_SIZE:
        page_size = settings.ADMIN_MAX_PAGE_SIZE

    # --- Soft-delete / search / filters (shared with CSV export) ---
    stmt = apply_filters(
        base_stmt,
        params=params,
        sortable=sortable,
        searchable=searchable,
        soft_delete_col=soft_delete_col,
    )

    # --- Total count over the filtered set (before ordering/pagination) ---
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # --- Sort: whitelisted field only (R20.4) ---
    stmt = apply_sort(stmt, params=params, sortable=sortable)

    # --- Pagination window (R20.3) ---
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    items = list(result.scalars().all())

    total_pages = math.ceil(total / page_size) if total else 0
    has_next = page < total_pages
    next_page = page + 1 if has_next else None

    return Page(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        next_page=next_page,
    )
