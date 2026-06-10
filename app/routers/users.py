from fastapi import APIRouter

from app.models.user import User
from app.schemas.user import UserOut, UserUpdate
from app.utils.dependencies import CurrentUser, DBSession

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
async def patch_me(body: UserUpdate, user: CurrentUser, db: DBSession) -> User:
    updated = body.model_dump(exclude_unset=True)
    for key, value in updated.items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/me", status_code=204)
async def delete_me(user: CurrentUser, db: DBSession) -> None:
    await db.delete(user)
    await db.commit()

