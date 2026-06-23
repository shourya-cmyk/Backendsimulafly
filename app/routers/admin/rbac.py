"""Admin RBAC router — roles (R2).

Endpoints (all prefixed ``/admin``; mounted under ``/api/v1`` by the wiring
step):

| Method | Path          | Permission     | Notes                              |
|--------|---------------|----------------|------------------------------------|
| GET    | `/roles`      | `roles.read`   | predefined + custom w/ permissions |
| POST   | `/roles`      | `roles.manage` | create a custom role               |
| PATCH  | `/roles/{id}` | `roles.manage` | modify role permissions/metadata   |

Reads are gated by ``require_permission(...)`` from
:mod:`app.utils.admin_dependencies`; the mutating routes are additionally
wrapped by ``audited(...)`` from :mod:`app.services.admin.audit_service` so each
create/modify writes one immutable audit entry (Requirement 19.1).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin import Role
from app.schemas.admin.rbac import (
    RoleCreateRequest,
    RoleResponse,
    RolesListResponse,
    RoleUpdateRequest,
)
from app.services.admin.audit_service import AuditContext, audited
from app.services.admin.roles_service import AdminRolesService
from app.utils.admin_dependencies import require_permission

router = APIRouter(prefix="/admin", tags=["admin:rbac"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _to_role_response(role: Role) -> RoleResponse:
    """Project a loaded ``Role`` (with permissions) to its response schema."""
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        is_predefined=role.is_predefined,
        permissions=sorted(p.key for p in role.permissions),
    )


@router.get(
    "/roles",
    response_model=RolesListResponse,
    dependencies=[Depends(require_permission("roles.read"))],
)
async def list_roles(db: DBSession) -> RolesListResponse:
    """List every role (predefined + custom) with its permission keys (R2.1, R2.2)."""
    roles = await AdminRolesService(db).list_roles()
    return RolesListResponse(roles=[_to_role_response(r) for r in roles])


@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("roles.manage"))],
)
async def create_role(
    payload: RoleCreateRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("roles.create", "role"))],
) -> RoleResponse:
    """Create a custom role (R2.8)."""
    role = await AdminRolesService(db).create_role(
        payload.name,
        payload.permissions,
        description=payload.description,
    )
    audit.set_target(role.id)
    audit.add_metadata(name=role.name, permissions=sorted(p.key for p in role.permissions))
    return _to_role_response(role)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleResponse,
    dependencies=[Depends(require_permission("roles.manage"))],
)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    db: DBSession,
    audit: Annotated[AuditContext, Depends(audited("roles.update", "role"))],
) -> RoleResponse:
    """Modify a role's permissions and/or metadata (R2.6)."""
    role = await AdminRolesService(db).update_role(
        role_id,
        permission_keys=payload.permissions,
        name=payload.name,
        description=payload.description,
    )
    audit.set_target(role.id)
    audit.add_metadata(permissions=sorted(p.key for p in role.permissions))
    return _to_role_response(role)
