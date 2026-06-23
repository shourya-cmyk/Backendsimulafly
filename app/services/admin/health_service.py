"""System_Health_Service — webhook monitoring + service health (Requirement 23).

This service powers the operational endpoints in
:mod:`app.routers.admin.system`:

* **Webhook delivery log** (:meth:`list_webhooks`, R23.1) — a paginated listing
  over :class:`~app.models.webhook_delivery.WebhookDelivery` using the shared
  :func:`app.services.admin.listing.paginate` engine, newest first.
* **Redelivery** (:meth:`redeliver`, R23.2) — re-enqueues a *failed* delivery by
  resetting its status to ``pending`` and incrementing ``attempt_count``. A
  missing id is 404; a delivery that is not in the ``failed`` state is 409
  (only failed deliveries can be redelivered). The acting admin is recorded by
  the router's ``audited(...)`` dependency.
* **System health** (:meth:`health`, R23.3, R23.5) — reports the database
  connection (via :func:`app.core.database.ping_db`) and the APScheduler
  background scheduler (read from ``request.app.state.scheduler``). An
  unavailable dependency is reported as ``degraded`` rather than raising, which
  mirrors the existing ``/readyz`` semantics.
* **Operational counters** (:meth:`counters`, R23.4) — counts of pending
  background work (scheduled APScheduler jobs + pending webhook deliveries) and
  recent processing failures (failed webhook deliveries).

The scheduler is accessed defensively: the service accepts the resolved
scheduler object (which the router reads from ``request.app.state``) and treats
its absence — or a stopped scheduler — as ``degraded`` so the endpoint never
depends on a fully wired runtime.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import ping_db
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.services.admin.listing import ListParams, Page, paginate

#: Rolled-up / per-dependency health status strings.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"


class SystemHealthService:
    """Webhook monitoring, redelivery, and service-health reporting (R23)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- Webhook delivery log (R23.1) -------------------------------------

    async def list_webhooks(self, params: ListParams) -> Page:
        """Return a paginated page of webhook deliveries, newest first (R23.1).

        Sortable by ``created_at`` / ``last_attempt_at`` / ``attempt_count`` and
        filterable by ``status``/``event_type`` via the shared listing engine.
        Defaults to ``-created_at`` when no explicit sort is supplied.
        """
        base = select(WebhookDelivery)
        sortable = {
            "created_at": WebhookDelivery.created_at,
            "last_attempt_at": WebhookDelivery.last_attempt_at,
            "attempt_count": WebhookDelivery.attempt_count,
            "status": WebhookDelivery.status,
            "event_type": WebhookDelivery.event_type,
        }
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
            searchable=(WebhookDelivery.event_type,),
        )

    # -- Redelivery (R23.2) -----------------------------------------------

    async def redeliver(self, delivery_id: uuid.UUID) -> WebhookDelivery:
        """Re-enqueue a failed webhook delivery (R23.2).

        Resets the delivery's status to ``pending`` and increments its
        ``attempt_count`` so the background pipeline will pick it up again.
        Raises HTTP 404 when the id does not exist and HTTP 409 when the
        delivery is not in the ``failed`` state (only failed deliveries are
        redeliverable). The acting admin is captured by the caller's
        ``audited(...)`` dependency.
        """
        record = await self.db.get(WebhookDelivery, delivery_id)
        if record is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="webhook delivery not found",
            )
        if record.status != WebhookDeliveryStatus.FAILED.value:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="only failed webhook deliveries can be redelivered",
            )
        record.status = WebhookDeliveryStatus.PENDING.value
        record.attempt_count = (record.attempt_count or 0) + 1
        await self.db.commit()
        await self.db.refresh(record)
        return record

    # -- System health (R23.3, R23.5) -------------------------------------

    async def health(self, scheduler: Any | None) -> tuple[str, dict[str, str | None]]:
        """Report database + scheduler health (R23.3); degraded on failure (R23.5).

        Returns ``(database_status, scheduler_status_and_detail)``-style data as
        a tuple of the database status string and a scheduler ``{status, detail}``
        mapping. The router assembles the rolled-up :class:`SystemHealth`. An
        unavailable dependency yields ``degraded`` rather than an exception.
        """
        db_ok = await ping_db()
        db_status = STATUS_OK if db_ok else STATUS_DEGRADED

        sched_status, sched_detail = self._scheduler_status(scheduler)
        return db_status, {"status": sched_status, "detail": sched_detail}

    @staticmethod
    def _scheduler_status(scheduler: Any | None) -> tuple[str, str | None]:
        """Classify the APScheduler state defensively (R23.5).

        Treats a missing scheduler (not wired into ``app.state``) or a
        non-running scheduler as ``degraded`` so the endpoint never assumes a
        fully wired runtime.
        """
        if scheduler is None:
            return STATUS_DEGRADED, "scheduler not available"
        running = getattr(scheduler, "running", None)
        if running is False:
            return STATUS_DEGRADED, "scheduler not running"
        return STATUS_OK, None

    # -- Operational counters (R23.4) -------------------------------------

    async def counters(self, scheduler: Any | None) -> tuple[int, int]:
        """Return ``(pending_jobs, recent_failures)`` (R23.4).

        ``pending_jobs`` is the number of scheduled APScheduler jobs plus the
        number of webhook deliveries still awaiting delivery (``pending``).
        ``recent_failures`` is the number of failed webhook deliveries.
        """
        scheduled_jobs = self._scheduled_job_count(scheduler)

        pending_webhooks = await self._count_status(WebhookDeliveryStatus.PENDING)
        recent_failures = await self._count_status(WebhookDeliveryStatus.FAILED)

        return scheduled_jobs + pending_webhooks, recent_failures

    @staticmethod
    def _scheduled_job_count(scheduler: Any | None) -> int:
        """Number of jobs the scheduler currently has scheduled (0 if absent)."""
        if scheduler is None:
            return 0
        get_jobs = getattr(scheduler, "get_jobs", None)
        if get_jobs is None:
            return 0
        try:
            return len(get_jobs())
        except Exception:  # noqa: BLE001 - scheduler not started yet, etc.
            return 0

    async def _count_status(self, status_value: WebhookDeliveryStatus) -> int:
        """Count webhook deliveries currently in ``status_value``."""
        stmt = (
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.status == status_value.value)
        )
        total = (await self.db.execute(stmt)).scalar_one()
        return int(total)
