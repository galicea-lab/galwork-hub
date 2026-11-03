# wf/dependencies.py
# 2026-10-05: autoryzacja modulu wf opiera sie na tokenie Keycloak
# i jednej tabeli `users`.
#
# `get_current_wf_user` zwraca CurrentUser: lokalne `users.id` (FK w tabelach
# workflow), `sub` z tokenu (models/user.py: keycloak_sub) oraz role/grupy
# z claimow Keycloak - to one decyduja o dostepie do zadan (patrz
# wf/engine.py: TaskAssignment.allows i wf/services/process_service.authorize).

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.security import get_current_principal
from core.config import settings
from models.user import User
from wf.models import UserSubstitution
from wf.schemas.wf_schemas import CurrentUser


def _groups_from_claims(claims: Mapping[str, Any]) -> frozenset[str]:
  """Grupy Keycloak z claimów - używane przez candidateGroups w BPMN.

  2026-10-05: claim `groups` (pełne ścieżki `/example/reviewers`) oraz
  role z `realm_access`/`resource_access`. Normalizujemy do postaci bez
  prefiksu "/", żeby dało się dopasować nazwy użyte w BPMN.
  """
  groups: set[str] = set()
  raw_groups = claims.get('groups') or ()
  if isinstance(raw_groups, str):
    raw_groups = raw_groups.split()
  groups.update(str(group) for group in raw_groups)
  realm_roles = (claims.get('realm_access') or {}).get('roles') or ()
  groups.update(str(role) for role in realm_roles)
  return frozenset(group.lstrip('/').split('/')[-1] for group in groups if group)


def _active_substitutions(db: Session, user: User) -> frozenset[int]:
  """Uzytkownicy, za ktorych aktualnie dziala `user` (tabela user_substitution)."""
  now = datetime.now(UTC)
  rows = db.execute(
    select(UserSubstitution.user_id)
    .where(UserSubstitution.substitute_id == user.id,
           UserSubstitution.valid_from <= now,
           UserSubstitution.valid_to >= now)
  ).scalars().all()
  return frozenset(rows)


def get_current_wf_user(
  principal = Depends(get_current_principal),
  db: Session = Depends(get_db),
) -> CurrentUser:
  """Zależność FastAPI: kontekst zalogowanego użytkownika dla modułu wf.

  Uwaga: `get_current_principal` (api/security) zwalidował token i zwrocil
  konto z bazy, a `get_db` to ta sama sesja - dzieki temu `user` jest juz
  zaladowany (brak dodatkowego zapytania o `users`).
  """
  user: User = principal.user
  token = principal.token
  return CurrentUser(
    id=user.id,
    sub=user.keycloak_sub or token.sub or '',
    username=token.username or user.name or '',
    email=user.email,
    display_name=user.name or token.username or user.email,
    groups=_groups_from_claims(principal.claims),
    roles=frozenset(token.roles or ()),
    substituting_for=_active_substitutions(db, user),
  )


# 2026-10-05: definicje procesów (ProcessModel/ProcessModelVersion) są
# konfiguracją systemu, więc zapis na nie jest zamknięty dla grup
# `admin_groups` z sekcji [keycloak] w config/config.ini. Odczyt wymaga tylko
# zalogowania. Pusta lista = nikt nie może zapisywać definicji.

ADMIN_GROUPS = frozenset(
  group.strip().lstrip('/').split('/')[-1].lower()
  for group in settings.keycloak.admin_groups
)


def require_process_admin(
  current_user: CurrentUser = Depends(get_current_wf_user),
) -> CurrentUser:
  """Zależność FastAPI: zalogowany użytkownik z grupy administracyjnej."""
  if not ADMIN_GROUPS:
    raise HTTPException(
      status_code=403,
      detail=('Brak uprawnień do administracji procesami: ustaw '
              'keycloak.admin_groups w config/config.ini'))
  principals = {value.lstrip('/').split('/')[-1].lower()
                for value in current_user.groups | current_user.roles}
  if not ADMIN_GROUPS & principals:
    raise HTTPException(
      status_code=403,
      detail='Brak uprawnień do administracji procesami '
             f'(wymagana grupa: {", ".join(sorted(ADMIN_GROUPS))})')
  return current_user
