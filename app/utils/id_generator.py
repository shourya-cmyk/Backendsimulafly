import re
from typing import Any
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# Regex patterns for MPUID and MPSUID validation
MPUID_REGEX = re.compile(r"^SIM-M-([A-Z]{2})-(\d{6})-([A-Z0-9])$")
MPSUID_REGEX = re.compile(r"^SIM-S-(\d{6})-(\d{2})-([A-Z0-9])$")


def validate_mpuid(mpuid: str) -> bool:
    """Validate if a string matches the MPUID format: SIM-M-{STATE}-{SEQUENCE}-{CITY_CODE}."""
    if not mpuid:
        return False
    return bool(MPUID_REGEX.match(mpuid.strip().upper()))


def validate_mpsuid(mpsuid: str) -> bool:
    """Validate if a string matches the MPSUID format: SIM-S-{MERCHANT_SEQUENCE}-{SHOP_SEQUENCE}-{CITY_CODE}."""
    if not mpsuid:
        return False
    return bool(MPSUID_REGEX.match(mpsuid.strip().upper()))


def parse_mpuid(mpuid: str) -> dict[str, Any] | None:
    """Parse MPUID into its constituent parts."""
    if not mpuid:
        return None
    match = MPUID_REGEX.match(mpuid.strip().upper())
    if not match:
        return None
    state, sequence, city_code = match.groups()
    return {
        "brand": "SIM",
        "entity": "M",
        "state": state,
        "sequence": sequence,
        "sequence_int": int(sequence),
        "city_code": city_code,
    }


def parse_mpsuid(mpsuid: str) -> dict[str, Any] | None:
    """Parse MPSUID into its constituent parts."""
    if not mpsuid:
        return None
    match = MPSUID_REGEX.match(mpsuid.strip().upper())
    if not match:
        return None
    merchant_seq, shop_seq, city_code = match.groups()
    return {
        "brand": "SIM-S",
        "merchant_sequence": merchant_seq,
        "merchant_sequence_int": int(merchant_seq),
        "shop_sequence": shop_seq,
        "shop_sequence_int": int(shop_seq),
        "city_code": city_code,
    }


def normalize_state_code(state_code: str | None) -> str:
    """Normalize state code to 2 uppercase letters. Defaults to 'DL'."""
    if not state_code:
        return "DL"
    clean = re.sub(r"[^A-Za-z]", "", state_code).upper()
    if len(clean) >= 2:
        return clean[:2]
    return (clean + "XX")[:2]


def normalize_city_code(city_code: str | None) -> str:
    """Normalize city code to 1 uppercase alphanumeric character. Defaults to 'N'."""
    if not city_code:
        return "N"
    clean = re.sub(r"[^A-Za-z0-9]", "", city_code).upper()
    if len(clean) >= 1:
        return clean[0]
    return "N"


async def generate_mpuid(
    db: AsyncSession, state_code: str | None = "DL", city_code: str | None = "N"
) -> str:
    """Generate a unique MPUID: SIM-M-{STATE}-{SEQUENCE}-{CITY_CODE}."""
    from app.models.merchant import Merchant

    st = normalize_state_code(state_code)
    city = normalize_city_code(city_code)

    # Get the count of distinct partner IDs to derive next sequence
    res = await db.execute(
        select(func.count(func.distinct(Merchant.partner_id))).where(
            Merchant.partner_id.isnot(None)
        )
    )
    count = res.scalar() or 0
    next_seq = count + 1

    for offset in range(100):
        candidate_seq = f"{next_seq + offset:06d}"
        candidate = f"SIM-M-{st}-{candidate_seq}-{city}"
        chk = await db.execute(
            select(Merchant.id).where(Merchant.partner_id == candidate)
        )
        if chk.scalar_one_or_none() is None:
            return candidate

    raise RuntimeError("Could not generate unique MPUID partner_id")


async def generate_mpsuid(
    db: AsyncSession, partner_id: str | None, city_code: str | None = "N"
) -> str:
    """Generate a unique MPSUID: SIM-S-{MERCHANT_SEQUENCE}-{SHOP_SEQUENCE}-{CITY_CODE}."""
    from app.models.merchant import Merchant

    # Extract 6-digit merchant sequence from parent MPUID
    parsed_parent = parse_mpuid(partner_id) if partner_id else None
    if parsed_parent:
        merchant_seq = parsed_parent["sequence"]
        city = parsed_parent["city_code"]
    elif partner_id:
        # Fallback for legacy or custom partner_id formats
        digits = re.sub(r"\D", "", partner_id)
        merchant_seq = f"{int(digits):06d}"[:6] if digits else "000001"
        city = normalize_city_code(city_code)
    else:
        merchant_seq = "000001"
        city = normalize_city_code(city_code)

    # Count existing shops under this partner_id
    if partner_id:
        res = await db.execute(
            select(func.count(Merchant.id)).where(Merchant.partner_id == partner_id)
        )
        existing_shops_count = res.scalar() or 0
    else:
        existing_shops_count = 0

    next_shop_num = existing_shops_count + 1

    for offset in range(50):
        shop_seq = f"{next_shop_num + offset:02d}"
        candidate = f"SIM-S-{merchant_seq}-{shop_seq}-{city}"
        chk = await db.execute(
            select(Merchant.id).where(Merchant.shop_id == candidate)
        )
        if chk.scalar_one_or_none() is None:
            return candidate

    raise RuntimeError("Could not generate unique MPSUID shop_id")
