# wf/routers/user_directory_api.py
# coding: utf-8
"""
Katalog użytkowników/grup dla pól `AsyncSelectWidget` typu "Osoba lub grupa".

Scala dwie tabele huba:
  * `User` (models/user.py) - pojedyncze konta,
  * `OrgUnit` (wf/models/wf.py) - jednostki/grupy organizacyjne
    (`keycloak_group`) - zgodnie z `lanes.md` to jest rejestr rzeczywistych
    ról organizacyjnych, do którego powinny się odwoływać lane'y BPMN, a nie
    nazwy zadań.

`value` rozróżnia rodzaj wpisu prefiksem (`user:<id>` / `group:<uuid>`), żeby
wywołujący (np. przydział kandydata do zadania) wiedział, z którą tabelą ma
do czynienia.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from models.user import User
from wf.dependencies import get_current_wf_user
from wf.models import OrgUnit
from wf.schemas.wf_schemas import CurrentUser, OptionRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[OptionRead])
def list_user_options(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_wf_user),
):
    """`GET /v1/wf/users?q=...` - opcje dla pól "Osoba lub grupa" (łączy
    konta użytkowników i jednostki/grupy organizacyjne; `limit` jest
    stosowany do każdej z tabel osobno, a potem do wyniku łącznego)."""
    options: list[OptionRead] = []

    user_stmt = select(User).where(User.is_active.is_(True))
    if q:
        user_stmt = user_stmt.where(
            or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    user_stmt = user_stmt.order_by(User.name).limit(limit)
    for user in db.execute(user_stmt).scalars().all():
        options.append(OptionRead(value=f"user:{user.id}", label=user.name or user.email))

    org_stmt = select(OrgUnit)
    if q:
        org_stmt = org_stmt.where(OrgUnit.name.ilike(f"%{q}%"))
    org_stmt = org_stmt.order_by(OrgUnit.name).limit(limit)
    for org_unit in db.execute(org_stmt).scalars().all():
        options.append(OptionRead(value=f"group:{org_unit.id}", label=f"Grupa: {org_unit.name}"))

    return options[:limit]
