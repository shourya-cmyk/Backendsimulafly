"""Visualization router.

Multi-product rendering rules
-------------------------------
* All selected products in the SAME category  →  one render per product
  (separate VisualizeJob objects so the user can compare variants).
* Products spanning MULTIPLE categories         →  single COMPOSITE render
  (one job; prompt describes every product so the AI places them together).

Single-product path
-------------------
Accepts `product_id` (legacy) or `product_ids` with one element.
"""


import asyncio
import uuid
from collections import defaultdict
from typing import Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.models.message import Message
from app.models.product import Product
from app.models.session import DesignSession
from app.schemas.upload import (
    VisualizeJobOut,
    VisualizeJobRef,
    VisualizeMultiResponse,
    VisualizeRequest,
    VisualizeResponse,
)
from app.services.azure_ai_client import get_image_client
from app.services.image_service import get_owned, persist_image
from app.services.visualize_jobs import (
    VisualizeJob,
    create_job,
    get_job,
    mark_done,
    mark_failed,
)
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/visualize", tags=["visualize"])
settings = get_settings()
log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PLACEMENT_LINE = (
    "Place it at: {placement}."
    if "{placement}" in "{placement}"  # always True — just a template marker
    else ""
)


def _edit_prompt(product_title: str, placement: str | None) -> str:
    """Single-product edit prompt for the Azure /images/edits endpoint."""
    direction = f" Place it at: {placement}." if placement else ""
    return (
        "You are a photorealistic interior-design compositor. "
        "Using the product shown in the reference image, insert it into the provided room scene. "
        "Rules you MUST follow:\n"
        "  1. Preserve the room's original lighting, shadows, perspective, camera angle, and background exactly.\n"
        "  2. Do NOT remove, move, or recolour any existing furniture or decorations.\n"
        "  3. The product must look physically present: correct scale, contact shadow, and reflections.\n"
        f"  4. Product to place: {product_title}.{direction}\n"
        "Output: one photorealistic staging photograph, no text overlays, no watermarks."
    )


def _composite_prompt(
    products: list[Product],
    room_summary: str | None,
    placement: str | None,
) -> str:
    """Multi-product composite prompt (different categories → single scene)."""
    items = "; ".join(
        f"{p.title} ({p.category or 'furniture'})" for p in products
    )
    direction = f" Arrange them as follows: {placement}." if placement else ""
    scene = room_summary or "a warmly lit, tastefully furnished residential interior"
    return (
        "Photorealistic interior-design scene. "
        f"Room: {scene}. "
        f"Place all of the following items together in the scene: {items}.{direction} "
        "Rules:\n"
        "  1. Preserve the room's original lighting, shadows, perspective, and camera angle.\n"
        "  2. Do NOT remove, move, or recolour any existing furniture or decorations.\n"
        "  3. Each product must look physically present: correct scale, contact shadows, reflections.\n"
        "  4. Maintain natural spacing between items; avoid overlapping.\n"
        "Output: one photorealistic staging photograph, no text overlays, no watermarks."
    )[:3800]


def _fallback_prompt_single(product: Product, room_summary: str | None, placement: str | None) -> str:
    meta = product.product_metadata or {}
    details = [f"{k}: {v}" for k in ("color", "material", "dimensions", "brand") if (v := meta.get(k))]
    detail_line = "; ".join(details) if details else ""
    scene = room_summary or "a warmly lit, tastefully furnished residential interior"
    direction = f" Placement: {placement}." if placement else ""
    return (
        "Photorealistic interior-design scene. "
        f"Room: {scene}. "
        f"Place a {product.category or 'furniture piece'} — specifically: {product.title}. "
        f"{detail_line}.{direction} "
        "Natural daylight, realistic shadows, 3/4 camera angle, professional staging photograph."
    )[:3500]


def _fallback_prompt_composite(
    products: list[Product],
    room_summary: str | None,
    placement: str | None,
) -> str:
    items = "; ".join(
        f"{p.title} ({p.category or 'furniture'})" for p in products
    )
    scene = room_summary or "a warmly lit, tastefully furnished residential interior"
    direction = f" Arrangement: {placement}." if placement else ""
    return (
        "Photorealistic interior-design staging photograph. "
        f"Room: {scene}. "
        f"All items present: {items}.{direction} "
        "Natural daylight, realistic shadows, 3/4 camera angle, professional result."
    )[:3500]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_out(job: VisualizeJob) -> VisualizeJobOut:
    return VisualizeJobOut(
        task_id=job.id,
        status=job.status,
        image_id=job.image_id,
        message_id=job.message_id,
        preview_url=(f"/api/v1/upload/room-image/{job.image_id}" if job.image_id else None),
        error=job.error,
    )


def _resolve_product_ids(body: VisualizeRequest) -> list[uuid.UUID]:
    """Return the final list of product IDs from the request (deduped)."""
    ids: list[uuid.UUID] = list(body.product_ids) if body.product_ids else []
    if body.product_id and body.product_id not in ids:
        ids.append(body.product_id)
    return ids


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=dict,   # union response; shape depends on single vs multi-product
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(f"{settings.IMAGE_GEN_RATE_LIMIT_PER_HOUR}/hour")
async def visualize(
    request: Request,
    response: Response,
    body: VisualizeRequest,
    user: CurrentUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
) -> dict:
    """Kick off image generation in the background.

    Returns immediately with a task_id (single product) or a jobs list (multi-product).
    Poll GET /visualize/{task_id} until status == "done" to retrieve results.
    """
    # --- validate session ---
    session_res = await db.execute(
        select(DesignSession).where(
            DesignSession.id == body.session_id, DesignSession.user_id == user.id
        )
    )
    session = session_res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    # --- validate room image ---
    room_image = await get_owned(db, image_id=body.room_image_id, owner_id=user.id)
    if not room_image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room image not found")

    # --- resolve product list ---
    product_ids = _resolve_product_ids(body)
    if not product_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide at least one of product_id or product_ids",
        )

    # Fetch all requested products
    products_res = await db.execute(select(Product).where(Product.id.in_(product_ids)))
    products_by_id: dict[uuid.UUID, Product] = {p.id: p for p in products_res.scalars().all()}

    # Fallback to MerchantProduct for any missing IDs
    missing_ids = [pid for pid in product_ids if pid not in products_by_id]
    if missing_ids:
        from app.models.merchant_product import MerchantProduct as _MerchantProduct
        mp_res = await db.execute(select(_MerchantProduct).where(_MerchantProduct.id.in_(missing_ids)))
        for mp in mp_res.scalars().all():
            class MockProduct:
                def __init__(self, mp_obj):
                    self.id = mp_obj.id
                    self.title = mp_obj.title
                    self.category = mp_obj.category
                    self.image_url = mp_obj.primary_image_url
                    self.product_metadata = mp_obj.custom_metadata or {}
            products_by_id[mp.id] = MockProduct(mp)

    missing = [str(pid) for pid in product_ids if pid not in products_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"products not found: {', '.join(missing)}",
        )

    products = [products_by_id[pid] for pid in product_ids]

    # Phase 4: emit ai_image_generation for each included product that has a
    # MerchantProduct record. Products here come from the legacy `products` table
    # (visualization router not yet migrated to merchant_products — Phase 4b).
    # We look up MerchantProduct by ID to see if a corresponding record exists.
    from app.models.merchant_product import MerchantProduct as _MerchantProduct
    from app.services.billing import BillingService as _BillingService
    _affected_merchants: set[uuid.UUID] = set()
    _billing_vis = _BillingService(db)
    for _pid in product_ids:
        _mp = await db.get(_MerchantProduct, _pid)
        if not _mp:
            continue
        try:
            await _billing_vis.record_event(
                event_type="ai_image_generation",
                user_id=user.id,
                merchant_id=_mp.merchant_id,
                product_id=_mp.id,
                session_id=str(body.session_id),
                context={},
            )
            _affected_merchants.add(_mp.merchant_id)
        except Exception:
            pass  # billing must not break visualization
    for _mid in _affected_merchants:
        background_tasks.add_task(_billing_vis.pause_if_depleted_for, _mid)

    # --- SINGLE product shortcut ---
    if len(products) == 1:
        p = products[0]
        job = create_job(
            user_id=user.id,
            session_id=session.id,
            product_id=p.id,
            room_image_id=room_image.id,
        )
        asyncio.create_task(
            _run_single(
                job_id=job.id,
                user_id=user.id,
                session_id=session.id,
                product=_product_snapshot(p),
                room_bytes=room_image.data,
                room_summary=session.context_summary,
                placement=body.placement,
            )
        )
        return VisualizeResponse(task_id=job.id, status="pending").model_dump(mode="json")

    # --- MULTI-product: group by category ---
    by_category: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        cat = (p.category or "uncategorised").lower()
        by_category[cat].append(p)

    unique_categories = list(by_category.keys())

    if len(unique_categories) == 1:
        # All same category → separate individual renders
        scene_type: Literal["individual", "composite"] = "individual"
        jobs_created: list[VisualizeJob] = []
        for p in products:
            job = create_job(
                user_id=user.id,
                session_id=session.id,
                product_id=p.id,
                room_image_id=room_image.id,
            )
            jobs_created.append(job)
            asyncio.create_task(
                _run_single(
                    job_id=job.id,
                    user_id=user.id,
                    session_id=session.id,
                    product=_product_snapshot(p),
                    room_bytes=room_image.data,
                    room_summary=session.context_summary,
                    placement=body.placement,
                )
            )
        return VisualizeMultiResponse(
            scene_type=scene_type,
            jobs=[
                VisualizeJobRef(
                    task_id=j.id,
                    product_id=j.product_id,
                    scene_type=scene_type,
                )
                for j in jobs_created
            ],
        ).model_dump(mode="json")
    else:
        # Mixed categories → single composite scene
        scene_type = "composite"
        job = create_job(
            user_id=user.id,
            session_id=session.id,
            product_id=None,           # no single product; composite
            room_image_id=room_image.id,
        )
        asyncio.create_task(
            _run_composite(
                job_id=job.id,
                user_id=user.id,
                session_id=session.id,
                products=[_product_snapshot(p) for p in products],
                room_bytes=room_image.data,
                room_summary=session.context_summary,
                placement=body.placement,
            )
        )
        return VisualizeMultiResponse(
            scene_type=scene_type,
            jobs=[
                VisualizeJobRef(
                    task_id=job.id,
                    product_id=None,
                    scene_type=scene_type,
                )
            ],
        ).model_dump(mode="json")


@router.get("/{task_id}", response_model=VisualizeJobOut)
async def get_visualize_status(
    task_id: uuid.UUID, user: CurrentUser
) -> VisualizeJobOut:
    job = get_job(task_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    if job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return _to_out(job)


# ---------------------------------------------------------------------------
# Product snapshot (so background tasks don't hold DB object references)
# ---------------------------------------------------------------------------


class _ProductSnapshot:
    """Lightweight copy of a Product for use in background tasks."""
    __slots__ = ("id", "title", "category", "image_url", "product_metadata")

    def __init__(self, p: Product) -> None:
        self.id = p.id
        self.title = p.title
        self.category = p.category
        self.image_url = p.image_url
        self.product_metadata = dict(p.product_metadata or {})


def _product_snapshot(p: Product) -> _ProductSnapshot:
    return _ProductSnapshot(p)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


async def _fetch_product_image(image_url: str | None) -> bytes | None:
    if not image_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(image_url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content
    except Exception as e:
        log.warning("product_image_fetch_failed", url=image_url, error=str(e))
    return None


async def _run_single(
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    product: _ProductSnapshot,
    room_bytes: bytes,
    room_summary: str | None,
    placement: str | None,
) -> None:
    """Single-product render: use /images/edits with the product reference image."""
    try:
        product_bytes = await _fetch_product_image(product.image_url)

        ai = get_image_client()
        edit_prompt = _edit_prompt(product.title, placement)
        fallback_prompt = _fallback_prompt_single(product, room_summary, placement)  # type: ignore[arg-type]
        log.info("visualize.single.start", job_id=str(job_id), product_id=str(product.id))
        png_bytes = await ai.image_edit(
            room_bytes, product_bytes, edit_prompt, fallback_prompt=fallback_prompt
        )
        await _persist_and_mark_done(
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            png_bytes=png_bytes,
            caption=f"Here's how the {product.title} looks in your space!",
            product_id=product.id,
        )
    except Exception as e:
        log.exception("visualize.single.failed", job_id=str(job_id), error=str(e))
        mark_failed(job_id, str(e))


async def _run_composite(
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    products: list[_ProductSnapshot],
    room_bytes: bytes,
    room_summary: str | None,
    placement: str | None,
) -> None:
    """Composite render: text-to-image prompt that describes ALL products together."""
    try:
        ai = get_image_client()
        # Composite always uses text-only generation (no single product reference image)
        prompt = _composite_prompt(products, room_summary, placement)  # type: ignore[arg-type]
        fallback = _fallback_prompt_composite(products, room_summary, placement)  # type: ignore[arg-type]
        log.info(
            "visualize.composite.start",
            job_id=str(job_id),
            product_count=len(products),
        )
        # Use image_edit with room image + text prompt; no product reference image for composite
        png_bytes = await ai.image_edit(
            room_bytes, None, prompt, fallback_prompt=fallback
        )
        names = " + ".join(p.title for p in products[:3])
        if len(products) > 3:
            names += f" + {len(products) - 3} more"
        await _persist_and_mark_done(
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            png_bytes=png_bytes,
            caption=f"Here's your room with {names} all together!",
            product_id=None,
        )
    except Exception as e:
        log.exception("visualize.composite.failed", job_id=str(job_id), error=str(e))
        mark_failed(job_id, str(e))


async def _persist_and_mark_done(
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    png_bytes: bytes,
    caption: str,
    product_id: uuid.UUID | None,
) -> None:
    async with SessionLocal() as db:
        generated = await persist_image(
            db,
            owner_id=user_id,
            data=png_bytes,
            media_type="image/png",
            source="generated_preview",
        )
        assistant_msg = Message(
            session_id=session_id,
            role="assistant",
            content=caption,
            ui_payload={
                "type": "room_preview",
                "image_id": str(generated.id),
                "product_id": str(product_id) if product_id else None,
            },
            image_id=generated.id,
        )
        db.add(assistant_msg)
        await db.commit()
        await db.refresh(assistant_msg)
        mark_done(job_id, image_id=generated.id, message_id=assistant_msg.id)
        log.info(
            "visualize.done",
            job_id=str(job_id),
            image_id=str(generated.id),
            bytes=len(png_bytes),
        )
