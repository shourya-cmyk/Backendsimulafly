"""Request/response schemas for webhook/system monitoring (Requirement 23).

These schemas back the operational endpoints served by
:class:`~app.services.admin.health_service.SystemHealthService`:

* :class:`WebhookDeliveryOut` — one row of the webhook delivery log
  (``GET /system/webhooks``; R23.1). Listings reuse the uniform
  :class:`~app.schemas.admin.listing.ListingEnvelope` parameterised with this
  item type.
* :class:`SystemHealth` / :class:`DependencyHealth` — the database + background
  scheduler health report (``GET /system/health``; R23.3, R23.5). An
  unavailable dependency is reported as ``degraded`` rather than failing the
  request (mirrors the existing ``/readyz`` semantics).
* :class:`OperationalCounters` — pending background jobs and recent processing
  failures (``GET /system/counters``; R23.4).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WebhookDeliveryOut(BaseModel):
    """One webhook delivery attempt in the delivery log (R23.1).

    Carries the event type, lifecycle status (``pending|delivered|failed``),
    the number of attempts so far, and the relevant timestamps so the Admin
    Panel can surface the delivery history and offer redelivery of failures.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status: str
    attempt_count: int
    last_attempt_at: datetime | None = None
    created_at: datetime


class DependencyHealth(BaseModel):
    """Health of a single dependency reported by the System_Health_Service.

    ``status`` is ``"ok"`` when the dependency is available and ``"degraded"``
    when it is not (R23.5). ``detail`` carries an optional human-readable note.
    """

    status: str
    detail: str | None = None


class SystemHealth(BaseModel):
    """Overall service health: database + background scheduler (R23.3, R23.5).

    ``status`` is the rolled-up health: ``"ok"`` when every dependency is
    available, otherwise ``"degraded"``. The per-dependency entries report the
    database connection and the APScheduler background scheduler individually.
    """

    status: str
    database: DependencyHealth
    scheduler: DependencyHealth


class OperationalCounters(BaseModel):
    """Operational counters: pending jobs + recent processing failures (R23.4).

    ``pending_jobs`` counts background jobs currently scheduled to run plus any
    webhook deliveries still awaiting delivery. ``recent_failures`` counts
    recent processing failures (failed webhook deliveries).
    """

    pending_jobs: int
    recent_failures: int
