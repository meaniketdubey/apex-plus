from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if creds is None:
        raise unauthorized
    user_id = decode_token(creds.credentials, "access")
    if user_id is None:
        raise unauthorized
    user = db.get(models.User, user_id)
    if user is None:
        raise unauthorized
    return user
