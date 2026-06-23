"""First-run bootstrap of a Super Admin account.

Admin login authenticates against :class:`AdminAccount` rows, but on a fresh
deploy there is no way to create the very first account — invitations require
an existing Super Admin to send them. This module closes that bootstrap gap.

  * ``bootstrap_super_admin(db)`` — called from the FastAPI lifespan (after
    ``seed_rbac_if_empty``). Idempotent and safe to run on every startup:

      - No-ops if ``ADMIN_BOOTSTRAP_EMAIL`` or ``ADMIN_BOOTSTRAP_PASSWORD`` is
        empty (the feature is opt-in via environment variables).
      - If an :class:`AdminAccount` with the configured email already exists it
        is left untouched — the password is **never** reset and roles are not
        re-assigned. This makes restarts and re-deploys safe.
      - Otherwise creates an active account with a bcrypt-hashed password and
        assigns it the predefined ``Super Admin`` role.

Module-as-script: ``python -m app.services.admin.admin_bootstrap`` runs the
seed + bootstrap against the configured DATABASE_URL.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.admin import AdminAccount, Role
from app.services.admin.rbac_seed import seed_rbac_if_empty

log = get_logger("app.services.admin.bootstrap")

SUPER_ADMIN_ROLE_NAME = "Super Admin"


async def bootstrap_super_admin(db: AsyncSession) -> bool:
    """Create the first Super Admin from env vars if none exists yet.

    Returns ``True`` if an account was created, ``False`` otherwise (feature
    disabled or the account already existed). Idempotent.
    """
    settings = get_settings()
    email = (settings.ADMIN_BOOTSTRAP_EMAIL or "").strip().lower()
    password = settings.ADMIN_BOOTSTRAP_PASSWORD or ""

    if not email or not password:
        log.info("admin_bootstrap_skipped", reason="email or password not configured")
        return False

    # An existing account (by email) is left exactly as-is — never reset the
    # password or re-assign roles.
    existing = (
        await db.execute(select(AdminAccount).where(AdminAccount.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        log.info("admin_bootstrap_skipped", reason="account already exists", email=email)
        return False

    # Locate the Super Admin role. The caller (lifespan) seeds roles first, but
    # guard defensively in case this is invoked standalone.
    role = (
        await db.execute(
            select(Role)
            .where(Role.name == SUPER_ADMIN_ROLE_NAME)
            .options(selectinload(Role.permissions))
        )
    ).scalar_one_or_none()
    if role is None:
        await seed_rbac_if_empty(db)
        role = (
            await db.execute(
                select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME)
            )
        ).scalar_one_or_none()
    if role is None:
        log.warning(
            "admin_bootstrap_failed",
            reason="Super Admin role missing after seed",
            email=email,
        )
        return False

    account = AdminAccount(
        email=email,
        hashed_password=hash_password(password),
        full_name=settings.ADMIN_BOOTSTRAP_FULL_NAME or SUPER_ADMIN_ROLE_NAME,
        is_active=True,
    )
    account.roles.append(role)
    db.add(account)
    await db.commit()
    log.info("admin_bootstrap_created", email=email, role=SUPER_ADMIN_ROLE_NAME)
    return True


async def _main() -> int:
    async with SessionLocal() as db:
        await seed_rbac_if_empty(db)
        created = await bootstrap_super_admin(db)
    print("Super Admin created." if created else "No Super Admin created (disabled or exists).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
