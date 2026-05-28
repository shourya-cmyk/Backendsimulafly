"""Merchant Contacts (My Customers CRM) endpoints."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select

from app.models.buyer_intelligence import MerchantContact
from app.utils.dependencies import DBSession
from app.utils.merchant_context import CurrentMerchantContext

router = APIRouter(prefix="/merchant/contacts", tags=["contacts"])

InviteStatusLiteral = Literal["not_invited", "invited", "joined"]
SourceLiteral = Literal["csv", "whatsapp", "manual"]


class ContactCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    source: SourceLiteral = "manual"
    last_purchase_note: str | None = None
    invite_status: InviteStatusLiteral = "not_invited"
    notes: str | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    last_purchase_note: str | None = None
    invite_status: InviteStatusLiteral | None = None
    notes: str | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    source: str
    last_purchase_note: str | None
    invite_status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PaginatedContacts(BaseModel):
    items: list[ContactOut]
    total: int
    limit: int
    offset: int


@router.get("/", response_model=PaginatedContacts)
async def list_contacts(
    db: DBSession,
    ctx: CurrentMerchantContext,
    search: str | None = Query(default=None),
    invite_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    base = select(MerchantContact).where(MerchantContact.merchant_id == ctx.merchant.id)
    count_base = select(func.count()).select_from(MerchantContact).where(
        MerchantContact.merchant_id == ctx.merchant.id
    )
    if search:
        pat = f"%{search}%"
        base = base.where(or_(MerchantContact.name.ilike(pat), MerchantContact.phone.ilike(pat)))
        count_base = count_base.where(
            or_(MerchantContact.name.ilike(pat), MerchantContact.phone.ilike(pat))
        )
    if invite_status:
        base = base.where(MerchantContact.invite_status == invite_status)
        count_base = count_base.where(MerchantContact.invite_status == invite_status)

    total = (await db.execute(count_base)).scalar_one()
    rows = (
        await db.execute(base.order_by(MerchantContact.created_at.desc()).offset(offset).limit(limit))
    ).scalars().all()
    return {"items": list(rows), "total": total, "limit": limit, "offset": offset}


@router.post("/", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactCreate,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> MerchantContact:
    contact = MerchantContact(merchant_id=ctx.merchant.id, **body.model_dump())
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdate,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> MerchantContact:
    contact = await db.get(MerchantContact, contact_id)
    if not contact or contact.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="contact not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(contact, k, v)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_contact(
    contact_id: uuid.UUID,
    db: DBSession,
    ctx: CurrentMerchantContext,
):
    contact = await db.get(MerchantContact, contact_id)
    if not contact or contact.merchant_id != ctx.merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="contact not found")
    await db.delete(contact)
    await db.commit()


@router.post("/csv-import", response_model=list[ContactOut], status_code=status.HTTP_201_CREATED)
async def import_csv(
    file: UploadFile,
    db: DBSession,
    ctx: CurrentMerchantContext,
) -> list[MerchantContact]:
    """Accept a CSV with columns: Name, Phone, Last Purchase (order flexible).

    Returns created contacts.  Skips rows missing a Name.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="file must be a .csv"
        )
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    created: list[MerchantContact] = []
    for row in reader:
        # Normalise header names (case-insensitive)
        normalised = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        name = normalised.get("name") or normalised.get("full name") or ""
        if not name:
            continue
        phone = normalised.get("phone") or normalised.get("mobile") or None
        email = normalised.get("email") or None
        last_purchase = normalised.get("last purchase") or normalised.get("last_purchase") or None
        contact = MerchantContact(
            merchant_id=ctx.merchant.id,
            name=name,
            phone=phone,
            email=email,
            source="csv",
            last_purchase_note=last_purchase,
            invite_status="not_invited",
        )
        db.add(contact)
        created.append(contact)

    if not created:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no valid rows found; expected columns: Name, Phone, Last Purchase",
        )

    await db.commit()
    for c in created:
        await db.refresh(c)
    return created
