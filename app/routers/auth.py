from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..timeutil import valid_timezone
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user_id: int) -> schemas.TokenOut:
    return schemas.TokenOut(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )


@router.post("/register", response_model=schemas.TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(models.User).where(models.User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = models.User(email=email, password_hash=hash_password(body.password))
    tz = body.timezone if body.timezone and valid_timezone(body.timezone) else "UTC"
    user.profile = models.Profile(display_name=body.display_name, timezone=tz)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _tokens(user.id)


@router.post("/login", response_model=schemas.TokenOut)
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(models.User).where(models.User.email == body.email.lower()))
    # Same error for unknown email and wrong password so accounts can't be enumerated.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return _tokens(user.id)


@router.post("/refresh", response_model=schemas.TokenOut)
def refresh(body: schemas.RefreshIn, db: Session = Depends(get_db)):
    user_id = decode_token(body.refresh_token, "refresh")
    if user_id is None or db.get(models.User, user_id) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    return _tokens(user_id)
