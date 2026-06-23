"""CSV export of admin listings (Requirement 21).

The Export_Service produces downloadable CSV files that mirror an admin
directory/list endpoint exactly: it reuses the shared listing engine's
:class:`~app.services.admin.listing.ListParams` and the same query builders
(:func:`~app.services.admin.listing.apply_filters` /
:func:`~app.services.admin.listing.apply_sort`) so the exported rows correspond
precisely to the records the listing returns under the same filters, search,
and sort (R21.1, design "CSV export", Property 39).

The export is streamed with :class:`fastapi.responses.StreamingResponse`:

* the first line is the declared column **header row** (R21.2);
* each subsequent line is one matching record, rendered via the per-column
  accessor callables;
* all matching rows are emitted — not just a single page — by running an
  *unpaginated* query that honours the same filters/search/sort.

After the row set is resolved, an audit entry is recorded naming the export
action, the acting :class:`AdminAccount`, and the exported dataset (R21.3) via
:meth:`~app.services.admin.audit_service.AuditService.record`.

Export permission equals the underlying listing's read permission; that check
is enforced by the router that wires this helper to a route (R21.4), so this
module performs no authorization of its own.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.admin import AdminAccount
from app.services.admin.audit_service import AuditService
from app.services.admin.listing import ListParams, apply_filters, apply_sort

#: A single export column: a human-readable header plus an accessor that derives
#: the cell value from a record. The accessor receives the record (an ORM
#: instance or row) and returns any value; it is rendered to text by
#: :func:`_render_cell`.
ExportColumn = tuple[str, Callable[[Any], Any]]

#: Conventional audit action recorded for a CSV export (R21.3).
EXPORT_ACTION = "export.csv"


def _render_cell(value: Any) -> str:
    """Render an accessor result to a CSV cell string.

    ``None`` becomes an empty string; everything else is stringified. Enum
    members render via their ``value`` so exports show the stored value rather
    than ``ClassName.MEMBER``.
    """
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, (str, int, float, bool)):
        return str(enum_value)
    return str(value)


async def export_csv(
    db: AsyncSession,
    base_stmt: Select,
    *,
    params: ListParams,
    columns: Sequence[ExportColumn],
    sortable: dict[str, ColumnElement],
    searchable: Sequence[ColumnElement],
    soft_delete_col: ColumnElement | None = None,
    actor: AdminAccount | uuid.UUID | None,
    dataset_name: str,
) -> StreamingResponse:
    """Stream the filtered listing as a CSV file and audit the export.

    The same ``base_stmt`` + ``params`` + builders used by
    :func:`~app.services.admin.listing.paginate` are applied here **without**
    pagination, so every record the listing would return across all pages is
    included in the export (R21.1). The first emitted line is the declared
    header row (R21.2); each following line is one record rendered through the
    column accessors. Once the rows are resolved, a single audit entry is
    recorded for the export (R21.3).

    Args:
        db: Async SQLAlchemy session.
        base_stmt: A ``select(...)`` returning the entity (or row) to export.
            Any joins/options the listing needs should already be applied —
            pass the *same* statement the listing endpoint uses.
        params: Normalised listing parameters (filters/search/sort/include_deleted).
            Pagination fields are intentionally ignored — the export is whole-set.
        columns: Ordered export columns as ``(header, accessor)`` pairs. The
            headers form the first CSV line; each accessor derives one cell per
            record.
        sortable: Sort/filter whitelist, shared with the listing endpoint.
        searchable: Columns OR-matched (ILIKE) against ``params.search``.
        soft_delete_col: Nullable deletion-timestamp column; soft-deleted rows
            are excluded unless ``params.include_deleted`` is True.
        actor: The acting admin (or its id, or ``None`` for system exports);
            recorded as the audit actor (R21.3).
        dataset_name: Name of the exported dataset (e.g. ``"merchants"``); used
            as the audit ``target_type`` and to build the download filename.

    Returns:
        A :class:`fastapi.responses.StreamingResponse` with ``text/csv`` media
        type and a ``Content-Disposition`` attachment filename.

    Raises:
        HTTPException: 422 when ``params.sort`` references an unsupported field
            (propagated from :func:`~app.services.admin.listing.apply_sort`).
    """
    # Build the unpaginated query using the SAME builders as the listing, so the
    # export and the listing resolve to identical row sets (R21.1, Property 39).
    stmt = apply_filters(
        base_stmt,
        params=params,
        sortable=sortable,
        searchable=searchable,
        soft_delete_col=soft_delete_col,
    )
    stmt = apply_sort(stmt, params=params, sortable=sortable)

    result = await db.execute(stmt)
    records = list(result.scalars().all())

    headers = [header for header, _ in columns]
    accessors = [accessor for _, accessor in columns]

    def _generate_rows() -> Any:
        """Yield the header row followed by one rendered row per record."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def _flush() -> str:
            text = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            return text

        writer.writerow(headers)
        yield _flush()

        for record in records:
            writer.writerow([_render_cell(accessor(record)) for accessor in accessors])
            yield _flush()

    # Record the export action, actor, and exported dataset (R21.3).
    audit = AuditService(db)
    await audit.record(
        actor=actor,
        action=EXPORT_ACTION,
        target_type=dataset_name,
        metadata={"row_count": len(records), "columns": headers},
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{dataset_name}-{timestamp}.csv"

    return StreamingResponse(
        _generate_rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
