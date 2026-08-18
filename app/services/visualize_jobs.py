"""Database-backed tracker for asynchronous visualize jobs.

Each /visualize/ POST returns immediately with a task_id while the actual
gpt-image generation (~2-4 min) runs as a background asyncio task. Clients
poll GET /visualize/{task_id} until status == "done".
"""

from __future__ import annotations

import uuid
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visualize_job import VisualizeJob

JobStatus = Literal["pending", "done", "failed"]


async def create_job(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    product_id: uuid.UUID | None,   # None for composite renders
    room_image_id: uuid.UUID | None,
) -> VisualizeJob:
    job = VisualizeJob(
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        product_id=product_id,
        room_image_id=room_image_id,
        status="pending",
    )
    db.add(job)
    await db.flush()
    return job


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> VisualizeJob | None:
    res = await db.execute(
        select(VisualizeJob).where(VisualizeJob.id == job_id)
    )
    return res.scalar_one_or_none()


async def mark_done(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    image_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    job = await db.get(VisualizeJob, job_id)
    if not job:
        return
    job.status = "done"
    job.image_id = image_id
    job.message_id = message_id


async def mark_failed(
    job_id: uuid.UUID,
    error: str,
) -> None:
    from app.core.database import SessionLocal
    async with SessionLocal() as db:
        job = await db.get(VisualizeJob, job_id)
        if not job:
            return
        job.status = "failed"
        job.error = error[:500]
        await db.commit()
