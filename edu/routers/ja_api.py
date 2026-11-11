# coding: utf-8

# 2026-11-10: Tożsamość i role zalogowanego użytkownika
# (GET /v1/edu/ja) - serwis studencki pokazuje na tej podstawie, w jakiej roli
# występuje użytkownik i jakie menu mu się należy.
# Role z Keycloak: administrator, dziekanat. Role z Education (Frappe):
# student, wykladowca. Kandydat: osoba bez rekordu w Education.
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from connector.frappe_education.connector import error_message
from connector.frappe_education.tozsamosc import (
  EduTozsamosc,
  StudentTozsamosc,
  WykladowcaTozsamosc,
  db_get_tozsamosc,
)
from edu.dependencies import is_admin, is_staff, user_email
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ja", tags=["tożsamość"],
                   responses={404: {"description": "Not found"}})

# 2026-11-10: kolejność = ważność; pierwsza rola z listy jest rolą domyślną
# w interfejsie.
ROLA_ADMINISTRATOR = 'administrator'
ROLA_DZIEKANAT = 'dziekanat'
ROLA_WYKLADOWCA = 'wykladowca'
ROLA_STUDENT = 'student'
ROLA_KANDYDAT = 'kandydat'


class Uzytkownik(BaseModel):
  """Kim jest zalogowany użytkownik i w jakich rolach może wystąpić."""

  email: str = ''
  nazwa: str = ''
  username: str = ''
  role: List[str] = []  # role dostępne do przełączenia, w kolejności ważności
  student: Optional[StudentTozsamosc] = None
  wykladowca: Optional[WykladowcaTozsamosc] = None
  dziekanat: bool = False
  administrator: bool = False


class JaResponse(BaseModel):
  code: int = 0
  message: str = ''
  # 2026-11-10: niepusty, gdy nie udało się ustalić roli z Education (Frappe nie odpowiada) -
  # interfejs pokazuje wtedy role z tokenu Keycloak i ostrzeżenie.
  ostrzezenie: str = ''
  uzytkownik: Optional[Uzytkownik] = None


def role_uzytkownika(current_user: CurrentUser, tozsamosc: Optional[EduTozsamosc]) -> List[str]:
  """Lista dostępnych ról - Keycloak (administrator, dziekanat) i Education."""
  role: List[str] = []
  if is_admin(current_user):
    role.append(ROLA_ADMINISTRATOR)
  if is_staff(current_user):
    role.append(ROLA_DZIEKANAT)
  if tozsamosc and tozsamosc.wykladowca:
    role.append(ROLA_WYKLADOWCA)
  if tozsamosc and tozsamosc.student:
    role.append(ROLA_STUDENT)
  return role


@router.get('', response_model=JaResponse)
def get_ja(current_user: CurrentUser = Depends(get_current_wf_user)) -> JaResponse:
  """
  Role i dane identyfikacyjne zalogowanego użytkownika.

  Student i wykładowca są rozpoznawani po e-mailu z tokenu Keycloak
  (`get_identity`), dziekanat i administrator - po grupach z tokenu. Osoba bez
  rekordu w Education dostaje rolę `kandydat`. Gdy Education nie odpowiada,
  zwracamy role z Keycloak i ostrzeżenie - menu musi się wyrenderować mimo
  awarii jednego z systemów.
  """
  tozsamosc: Optional[EduTozsamosc] = None
  ostrzezenie = ''
  email = user_email(current_user)

  if email:
    try:
      tozsamosc = db_get_tozsamosc(email)
    except Exception as e:
      log.info('ja: %s', e)
      ostrzezenie = error_message(e)
  else:
    ostrzezenie = 'W tokenie brak adresu e-mail - nie można ustalić ról w Education'

  role = role_uzytkownika(current_user, tozsamosc)
  if not role and not ostrzezenie:
    role = [ROLA_KANDYDAT]

  return JaResponse(
    code=0,
    message='OK',
    ostrzezenie=ostrzezenie,
    uzytkownik=Uzytkownik(
      email=email,
      nazwa=current_user.display_name,
      username=current_user.username,
      role=role,
      student=tozsamosc.student if tozsamosc else None,
      wykladowca=tozsamosc.wykladowca if tozsamosc else None,
      dziekanat=ROLA_DZIEKANAT in role,
      administrator=ROLA_ADMINISTRATOR in role,
    ),
  )
