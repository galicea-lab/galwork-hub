# wf/services/system_actor.py
# coding: utf-8
"""
2026-11-22: użytkownik systemowy dla działań bez zalogowanej osoby (webhooki,
crony): `[wf] system_user`, a bez niej `[edr] system_user` - nazwa albo
e-mail z tabeli `users` (ta sama reguła co scripts/wf_wake.py).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from connector.base import BaseConnector
from models.user import User
from wf.schemas.wf_schemas import CurrentUser


class SystemActorError(RuntimeError):
    """Brak `[wf] system_user` albo użytkownika w tabeli users."""


def get_system_actor(db: Session, login: str | None = None, *, display_name: str = 'System') -> CurrentUser:
    if not login:
        for section in ('wf', 'edr'):
            login = (BaseConnector.load_ini_section(section).get('system_user') or '').strip()
            if login:
                break
    user = db.execute(select(User).where((User.name == login) | (User.email == login))
                      ).scalars().first() if login else None
    if user is None:
        raise SystemActorError('ustaw [wf] system_user (nazwa lub e-mail użytkownika z tabeli users)')
    return CurrentUser(id=user.id, sub=user.keycloak_sub or 'sub-system', username=user.name,
                       email=user.email, display_name=display_name, groups=frozenset())
