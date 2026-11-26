# pm/access.py
# 2026-11-24: kontrola dostępu do API projektów PM (pm/routers/projects.py,
# forms.py, dashboard.py, calc.py) - dotąd te endpointy nie wymagały tokenu,
# także zapis i usuwanie projektów.
#
#   * odczyt i kalkulatory - każdy zalogowany (token Keycloak),
#   * zapis (projekty, formularze) - administrator PM: grupa z
#     `[keycloak] admin_groups` albo `[taiga] admin_groups` - ta sama reguła
#     co w integracji Taiga (pm/routers/taiga.py::is_admin).
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from config.config import section
from wf.dependencies import get_current_wf_user, is_process_admin
from wf.schemas.wf_schemas import CurrentUser


def _leaf(value: str) -> str:
    return value.strip().lstrip('/').split('/')[-1].lower()


def is_pm_editor(user: CurrentUser) -> bool:
    if is_process_admin(user):
        return True
    extra = {_leaf(g) for g in (section('taiga').get('admin_groups') or '').split(',') if g.strip()}
    return bool(extra & {_leaf(v) for v in (user.groups | user.roles) if v})


def require_pm_user(user: CurrentUser = Depends(get_current_wf_user)) -> CurrentUser:
    """Zależność: zalogowany użytkownik."""
    return user


def require_pm_editor(user: CurrentUser = Depends(get_current_wf_user)) -> CurrentUser:
    """Zależność: administrator PM (zapis projektów i formularzy)."""
    if not is_pm_editor(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail='Wymagane uprawnienia administratora PM')
    return user
