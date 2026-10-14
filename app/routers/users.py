from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..timeutil import valid_timezone

router = APIRouter(prefix="/me", tags=["profile"])


def _me(user: models.User) -> schemas.MeOut:
    return schemas.MeOut(id=user.id, email=user.email, profile=schemas.ProfileOut.model_validate(user.profile))


@router.get("", response_model=schemas.MeOut)
def get_me(user: models.User = Depends(current_user)):
    return _me(user)


@router.patch("", response_model=schemas.MeOut)
def update_me(
    body: schemas.ProfileUpdate,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if body.timezone is not None and not valid_timezone(body.timezone):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown time zone")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "target_weight_kg":  # the goal can be cleared with an explicit null
            setattr(user.profile, field, value)
    db.commit()
    db.refresh(user)
    return _me(user)
