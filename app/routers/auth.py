from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    GoogleLoginRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.user import UserOut
from app.services.google_auth import GoogleAuthError, verify_id_token
from app.utils.dependencies import DBSession

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DBSession) -> User:
    existing = await db.execute(select(User).where(User.email == body.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, db: DBSession) -> TokenPair:
    res = await db.execute(select(User).where(User.email == body.email.lower()))
    user = res.scalar_one_or_none()
    if (
        not user
        or not user.hashed_password
        or not verify_password(body.password, user.hashed_password)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    sub = str(user.id)
    return TokenPair(access_token=create_access_token(sub), refresh_token=create_refresh_token(sub))


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest) -> TokenPair:
    try:
        sub = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return TokenPair(access_token=create_access_token(sub), refresh_token=create_refresh_token(sub))


@router.post("/google", response_model=TokenPair)
async def google_login(body: GoogleLoginRequest, db: DBSession) -> TokenPair:
    try:
        identity = verify_id_token(body.id_token)
    except GoogleAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    # Resolve user: by google_sub, else by email (link), else create.
    res = await db.execute(select(User).where(User.google_sub == identity.sub))
    user = res.scalar_one_or_none()

    if user is None:
        res = await db.execute(select(User).where(User.email == identity.email))
        user = res.scalar_one_or_none()
        if user is not None:
            user.google_sub = identity.sub
            if not user.full_name and identity.full_name:
                user.full_name = identity.full_name
            if not user.avatar_url and identity.picture:
                user.avatar_url = identity.picture
        else:
            user = User(
                email=identity.email,
                hashed_password=None,
                google_sub=identity.sub,
                full_name=identity.full_name,
                avatar_url=identity.picture,
                is_active=True,
            )
            db.add(user)

    if user.is_active is False:
        user.is_active = True

    await db.commit()
    await db.refresh(user)
    sub = str(user.id)
    return TokenPair(access_token=create_access_token(sub), refresh_token=create_refresh_token(sub))
