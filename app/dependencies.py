from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_site_db
from .models import AdminUser


def get_current_admin(request: Request, db: Session = Depends(get_site_db)) -> AdminUser:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    admin_user = db.get(AdminUser, user_id)
    if admin_user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    return admin_user
