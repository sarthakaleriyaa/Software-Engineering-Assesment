import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    verify_password_against_dummy,
)
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    email = str(data.email).strip().lower()
    lookup_started = time.perf_counter()

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )
    lookup_ms = (time.perf_counter() - lookup_started) * 1000

    if existing_user:
        logger.info(
            "auth_signup_conflict lookup_ms=%.1f",
            lookup_ms,
        )
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        )

    hash_started = time.perf_counter()
    password_hash = hash_password(data.password)
    hash_ms = (time.perf_counter() - hash_started) * 1000

    user = User(
        email=email,
        hashed_password=password_hash,
    )
    db.add(user)

    commit_started = time.perf_counter()

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        logger.info(
            "auth_signup_conflict lookup_ms=%.1f hash_ms=%.1f",
            lookup_ms,
            hash_ms,
        )
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        ) from None

    commit_ms = (time.perf_counter() - commit_started) * 1000

    logger.info(
        "auth_signup_success user_id=%s lookup_ms=%.1f hash_ms=%.1f "
        "commit_ms=%.1f",
        user.id,
        lookup_ms,
        hash_ms,
        commit_ms,
    )

    return user


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    email = str(data.email).strip().lower()
    lookup_started = time.perf_counter()
    user = db.scalar(select(User).where(User.email == email))
    lookup_ms = (time.perf_counter() - lookup_started) * 1000

    if not user:
        verify_password_against_dummy(data.password)
        logger.info(
            "auth_login_failed reason=invalid_credentials lookup_ms=%.1f",
            lookup_ms,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verify_started = time.perf_counter()
    password_is_valid = verify_password(
        data.password, user.hashed_password
    )
    verify_ms = (time.perf_counter() - verify_started) * 1000

    if not password_is_valid:
        logger.info(
            "auth_login_failed reason=invalid_credentials "
            "user_id=%s lookup_ms=%.1f verify_ms=%.1f",
            user.id,
            lookup_ms,
            verify_ms,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_started = time.perf_counter()
    access_token = create_access_token(user.id)
    token_ms = (time.perf_counter() - token_started) * 1000

    logger.info(
        "auth_login_success user_id=%s lookup_ms=%.1f verify_ms=%.1f "
        "token_ms=%.1f",
        user.id,
        lookup_ms,
        verify_ms,
        token_ms,
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user