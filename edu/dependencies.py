# Autoryzacja modułu edu.
# Tożsamość pochodzi z tokenu Keycloak (wf.dependencies.get_current_wf_user).
# Student widzi tylko własne dane, rozpoznawane po e-mailu z tokenu.
# Pracownik dziekanatu (grupa lub rola z [edu] staff_groups) widzi dane
# wszystkich studentów.

from __future__ import annotations

from fastapi import Depends, HTTPException

from core.config import settings
from wf.dependencies import ADMIN_GROUPS, get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser

STAFF_GROUPS = frozenset(
  group.strip().lstrip('/').split('/')[-1].lower()
  for group in settings.edu.staff_groups
)


# Nazwy grup i ról z tokenu bez prefiksów ścieżek (np.
# `/dziekanat/studenci` -> `studenci`), małymi literami.
def principal_names(current_user: CurrentUser) -> set[str]:
  return {value.lstrip('/').split('/')[-1].lower()
          for value in current_user.groups | current_user.roles}


def is_staff(current_user: CurrentUser) -> bool:
  """Czy użytkownik należy do grupy pracowników dziekanatu."""
  return bool(STAFF_GROUPS & principal_names(current_user))


def is_admin(current_user: CurrentUser) -> bool:
  """Czy użytkownik należy do grupy administracyjnej systemu.

  Ta sama lista co w `wf/dependencies.py` (`[keycloak] admin_groups`), żeby
  administrator procesów był też administratorem serwisu studenckiego.
  """
  return bool(ADMIN_GROUPS & principal_names(current_user))


def user_email(current_user: CurrentUser) -> str:
  return str(current_user.email or '').strip().lower()


def is_own_email(current_user: CurrentUser, email: str | None) -> bool:
  return bool(email) and email.strip().lower() == user_email(current_user)


def require_edu_staff(
  current_user: CurrentUser = Depends(get_current_wf_user),
) -> CurrentUser:
  """Zależność FastAPI: zalogowany pracownik dziekanatu."""
  if not is_staff(current_user):
    raise HTTPException(
      status_code=403,
      detail='Brak uprawnień pracownika dziekanatu '
             f'(wymagana grupa: {", ".join(sorted(STAFF_GROUPS)) or "brak - ustaw [edu] staff_groups"})')
  return current_user


# 2026-11-08: rozpoznanie tożsamości dla nowych routerów modułu edu
# (plan, oceny, frekwencja, opłaty, legitymacja).
from connector.frappe_education.student import db_email2nr_albumu


def allowed_album(current_user: CurrentUser, nr_albumu: str | None = None) -> str:
  """Numer albumu zalogowanego studenta albo wskazany przez dziekanat.

  2026-11-10: administrator systemu ([keycloak] admin_groups) też wskazuje
  studenta - w interfejsie ma tę samą rolę co dziekanat.
  """
  if is_staff(current_user) or is_admin(current_user):
    if not nr_albumu:
      raise HTTPException(status_code=400, detail='Podaj nr_albumu')
    return nr_albumu

  own = db_email2nr_albumu(user_email(current_user))
  if not own or (nr_albumu and nr_albumu != own):
    raise HTTPException(status_code=403, detail='Zabroniony dostęp')
  return own


def lecturer_email(current_user: CurrentUser, email: str | None = None) -> str:
  """E-mail wykładowcy: z tokenu Keycloak (student nie ma tu dostępu)."""
  if is_staff(current_user):
    if not email:
      raise HTTPException(status_code=400, detail='Podaj email wykładowcy')
    return email

  own = user_email(current_user)
  if not own or (email and email.strip().lower() != own):
    raise HTTPException(status_code=403, detail='Zabroniony dostęp')
  return own


# 2026-11-08 //
# Wywołania Frappe w imieniu użytkownika portalu (X-User-Token, X-Forwarded-For;
# zob. connector/frappe_education/user_context.py). Zależność całej aplikacji
# /v1/edu; włączana przełącznikiem [frappe_education] forward_user_token, bo
# Frappe przyjmuje token tylko osoby, która ma tam konto (kandydat zakładany
# automatycznie, studenci i pracownicy - z provisioningu).
from fastapi import Request

from connector.frappe_education.connector import get_connector
from connector.frappe_education.user_context import bearer_token, set_current, UserContext

TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on', 'tak'})


def forward_user_token_enabled() -> bool:
  value = get_connector().config.get('forward_user_token', '') or ''
  return value.strip().lower() in TRUE_VALUES


def _user_context(request: Request) -> UserContext | None:
  token = bearer_token(request.headers.get('authorization'))
  if not token:
    return None
  return UserContext(token, request.client.host if request.client else None)


async def frappe_as_user(request: Request):
  """Zależność: wywołania Frappe w tym żądaniu w imieniu zalogowanego
  użytkownika, jeśli włączono `forward_user_token`.

  Zależność asynchroniczna celowo - kontekst ustawiony w pętli zdarzeń
  przechodzi do synchronicznych endpointów wykonywanych w puli wątków
  (kontekst ustawiony w zależności synchronicznej by nie przeszedł).
  """
  set_current(_user_context(request) if forward_user_token_enabled() else None)
  try:
    yield
  finally:
    set_current(None)


async def frappe_as_user_always(request: Request):
  """Jak `frappe_as_user`, ale bez przełącznika - dla tras, które z założenia
  działają tylko w imieniu użytkownika (np. przyszłe trasy rekrutacji)."""
  set_current(_user_context(request))
  try:
    yield
  finally:
    set_current(None)
# // --
