"""Shared listing envelope for admin directory/list endpoints (R20).

Every admin list endpoint returns the same envelope shape so the Admin Panel
consumes them uniformly:

```json
{
  "items": [ ... ],
  "page": 1,
  "page_size": 25,
  "total": 137,
  "total_pages": 6,
  "has_next": true,
  "next_page": 2
}
```

:class:`ListingEnvelope` is generic over the item type, so each router can
declare its concrete response model (for example ``ListingEnvelope[UserOut]``)
while the pagination metadata fields stay identical everywhere.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class ListingEnvelope(BaseModel, Generic[ItemT]):
    """Uniform pagination envelope wrapping a page of ``ItemT`` records.

    Attributes:
        items: The records on the current page.
        page: 1-based index of the current page.
        page_size: Number of records per page (after clamping).
        total: Total number of records matching the query (across all pages).
        total_pages: Number of pages for ``total`` at ``page_size``.
        has_next: Whether a subsequent page exists.
        next_page: The next page index, or ``None`` when on the last page.
    """

    items: list[ItemT]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    next_page: int | None = None
