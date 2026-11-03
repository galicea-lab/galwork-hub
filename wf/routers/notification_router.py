# coding: utf-8

"""Router API dla powiadomień użytkownika. Realizuje TODO.md pkt 2.

2026-10-05: powiadomienia są filtrowane po `users.id`
zalogowanego użytkownika (token Keycloak), a nie po parametrze zapytania -
wcześniej klient mógł podać dowolny UUID i odczytać cudze powiadomienia."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import Notification
from wf.schemas.wf_schemas import (
    CurrentUser,
    NotificationMarkRead,
    NotificationRead,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
def list_notifications(unread_only: bool = False,
                        limit: int = 50,
                        db: Session = Depends(get_db),
                        current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.post("/mark-read")
def mark_notifications_read(payload: NotificationMarkRead,
                             db: Session = Depends(get_db),
                             current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(Notification).where(
        Notification.id.in_(payload.ids),
        Notification.user_id == current_user.id,
    )
    notifications = db.execute(stmt).scalars().all()
    now = datetime.now(UTC)
    for notification in notifications:
        notification.read_at = now
    db.commit()
    return {"updated": len(notifications)}
