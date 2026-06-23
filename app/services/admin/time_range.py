"""Time_Range parsing and window bucketing for the Admin dashboard.

The Admin Panel UI offers a fixed set of aggregation windows
(``TIME_RANGES`` in ``Admin-Panel/lib/mock-data.ts``): ``1 Min``, ``1 Hour``,
``24 Hours``, ``7 Days``, ``1 Month``, ``1 Year``, ``2 Years``. The dashboard,
finance, and analytics services aggregate real rows over the window a Time_Range
resolves to and return ordered time-series whose point counts/labels match the
UI's ``generateChartData`` exactly.

This module maps a Time_Range string to a :class:`ResolvedRange`
``(window_start, window_end, bucket_count, bucket_labels)`` where
``window_end`` is the request time ("now") and ``window_start`` is
``now - range duration`` (Requirements 5.6, 12.4, 18.5). An unknown or missing
value raises :class:`InvalidTimeRangeError` (a ``ValueError`` subclass) which
the routers convert to HTTP 422.

The module is pure and DB-free so it is trivially testable: pass ``now`` to make
resolution deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Allowed Time_Range values, in UI display order
#: (mirrors ``TIME_RANGES`` in ``Admin-Panel/lib/mock-data.ts``). Treated as a
#: fixed contract: the backend never adds/removes values here.
TIME_RANGES: tuple[str, ...] = (
    "1 Min",
    "1 Hour",
    "24 Hours",
    "7 Days",
    "1 Month",
    "1 Year",
    "2 Years",
)

#: Fast membership set for validation (the allowed-set constant).
ALLOWED_TIME_RANGES: frozenset[str] = frozenset(TIME_RANGES)


class InvalidTimeRangeError(ValueError):
    """Raised when a Time_Range value is missing or not in the allowed set.

    Subclasses :class:`ValueError` so callers/routers can catch it and convert
    it to an HTTP 422 response (Requirements 5.6, 12.4, 18.5).
    """

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"Invalid Time_Range {value!r}; allowed values are: "
            + ", ".join(repr(v) for v in TIME_RANGES)
        )


@dataclass(frozen=True)
class ResolvedRange:
    """A Time_Range resolved against a concrete "now".

    Attributes
    ----------
    value:
        The originating Time_Range string (one of :data:`TIME_RANGES`).
    window_start:
        Inclusive start of the aggregation window (``window_end - duration``).
    window_end:
        End of the window — the request time ("now").
    bucket_count:
        Number of time-series buckets, matching the UI's ``generateChartData``.
    bucket_labels:
        Ordered bucket labels, matching the UI's ``generateChartData`` exactly.
        Always ``len(bucket_labels) == bucket_count``.
    duration:
        The window length (``window_end - window_start``).
    """

    value: str
    window_start: datetime
    window_end: datetime
    bucket_count: int
    bucket_labels: tuple[str, ...]
    duration: timedelta


def _day_of_week_labels() -> tuple[str, ...]:
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _month_labels() -> tuple[str, ...]:
    return (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )


def _two_year_labels() -> tuple[str, ...]:
    # Mirrors the UI exactly:
    #   `'${String(25 + Math.floor(i/12)).padStart(2,'0')} M${(i%12)+1}`
    return tuple(
        f"'{str(25 + i // 12).rjust(2, '0')} M{(i % 12) + 1}" for i in range(24)
    )


# Each Time_Range maps to (window duration, bucket_count, bucket_labels).
# Durations follow "window_start = now - range duration"; bucket counts/labels
# match the UI's generateChartData. Monthly ranges use 30-day months / 365-day
# years to keep resolution pure and deterministic.
_RANGE_SPECS: dict[str, tuple[timedelta, int, tuple[str, ...]]] = {
    "1 Min": (timedelta(minutes=1), 60, tuple(f"{i}s" for i in range(60))),
    "1 Hour": (timedelta(hours=1), 60, tuple(f"{i}m" for i in range(60))),
    "24 Hours": (timedelta(hours=24), 24, tuple(f"{i}h" for i in range(24))),
    "7 Days": (timedelta(days=7), 7, _day_of_week_labels()),
    "1 Month": (timedelta(days=30), 30, tuple(str(i + 1) for i in range(30))),
    "1 Year": (timedelta(days=365), 12, _month_labels()),
    "2 Years": (timedelta(days=730), 24, _two_year_labels()),
}


def resolve_time_range(
    range_str: str | None,
    *,
    now: datetime | None = None,
) -> ResolvedRange:
    """Resolve a Time_Range string to a concrete window and bucket layout.

    Parameters
    ----------
    range_str:
        One of :data:`TIME_RANGES`. Any other value (including ``None`` or an
        empty string) raises :class:`InvalidTimeRangeError`.
    now:
        The reference "now" (``window_end``). Defaults to the current UTC time.
        A naive ``datetime`` is assumed to be UTC.

    Returns
    -------
    ResolvedRange
        ``window_end`` is ``now``; ``window_start`` is ``now - duration``;
        ``bucket_count``/``bucket_labels`` match the UI's ``generateChartData``.

    Raises
    ------
    InvalidTimeRangeError
        If ``range_str`` is not a recognised Time_Range value. Routers convert
        this to HTTP 422 (Requirements 5.6, 12.4, 18.5).
    """
    if range_str not in ALLOWED_TIME_RANGES:
        raise InvalidTimeRangeError(range_str)

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    duration, bucket_count, bucket_labels = _RANGE_SPECS[range_str]
    window_end = now
    window_start = window_end - duration

    return ResolvedRange(
        value=range_str,
        window_start=window_start,
        window_end=window_end,
        bucket_count=bucket_count,
        bucket_labels=bucket_labels,
        duration=duration,
    )
