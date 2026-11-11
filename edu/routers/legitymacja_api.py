# coding: utf-8
# mLegitymacja w serwisie studenckim (education.portal.api.get_card
# / order_card / reveal_card_codes / request_card_revocation). Kody QR i
# aktywacyjny zwracane są jednorazowo i nie są przez hub zapisywane.
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.student_card import (
  EduKodyLegitymacji,
  EduLegitymacja,
  EduZamowienieLegitymacji,
  db_get_legitymacja,
  db_odswiez_legitymacje,
  db_pobierz_kody_legitymacji,
  db_zamow_legitymacje,
  db_zamow_uniewaznienie_legitymacji,
)
from edu.dependencies import allowed_album, user_email
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/legitymacja", tags=["legitymacja mLegitymacja"],
                   responses={404: {"description": "Not found"}})


class LegitymacjaResponse(BaseModel):
  code: int = 0
  message: str = ''
  legitymacja: Optional[EduLegitymacja] = None


class KodyLegitymacjiResponse(BaseModel):
  code: int = 0
  message: str = ''
  kody: Optional[EduKodyLegitymacji] = None


class ZamowienieResponse(BaseModel):
  code: int = 0
  message: str = ''
  zamowienie: Optional[EduZamowienieLegitymacji] = None


class ZamowienieBody(BaseModel):
  rodzaj: str = 'Issue'
  wazna_od: Optional[str] = None
  wazna_do: Optional[str] = None


class UniewaznienieBody(BaseModel):
  powod: Optional[str] = None


@router.get('', response_model=LegitymacjaResponse)
def get_legitymacja(nr_albumu: Optional[str] = None,
                    current_user: CurrentUser = Depends(get_current_wf_user),
                    ) -> LegitymacjaResponse:
  """
  Stan legitymacji: brak / zamówiona / wydana, terminy, dostępność kodów.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return LegitymacjaResponse(code=0, message='OK', legitymacja=db_get_legitymacja(album))
  except HTTPException:
    raise
  except Exception as e:
    log.info('legitymacja: %s', e)
    return LegitymacjaResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), legitymacja=None)


@router.post('/odswiez', response_model=LegitymacjaResponse)
def post_odswiez_legitymacje(nr_albumu: Optional[str] = None,
                            current_user: CurrentUser = Depends(get_current_wf_user),
                            ) -> LegitymacjaResponse:
  """
  Ponowne odpytanie MLS o stan legitymacji (normalnie robi to scheduler Frappe).

  Użyteczne, gdy student czeka na kod QR i nie chce czekać do kolejnego cyklu.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return LegitymacjaResponse(code=0, message='OK',
                               legitymacja=db_odswiez_legitymacje(album))
  except HTTPException:
    raise
  except Exception as e:
    log.info('legitymacja/odswiez: %s', e)
    return LegitymacjaResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), legitymacja=None)


@router.post('/zamow', response_model=ZamowienieResponse)
def post_zamow_legitymacje(dane: Optional[ZamowienieBody] = None,
                          nr_albumu: Optional[str] = None,
                          current_user: CurrentUser = Depends(get_current_wf_user),
                          ) -> ZamowienieResponse:
  """
  Zamówienie legitymacji (``Issue``) albo przedłużenie (``Renewal``).

  Student zamawia dla siebie; dziekanat może wskazać numer albumu.
  """
  dane = dane or ZamowienieBody()
  try:
    album = allowed_album(current_user, nr_albumu)
    return ZamowienieResponse(
      code=0, message='OK',
      zamowienie=db_zamow_legitymacje(album, dane.rodzaj, dane.wazna_od, dane.wazna_do))
  except HTTPException:
    raise
  except Exception as e:
    log.info('legitymacja/zamow: %s', e)
    return ZamowienieResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), zamowienie=None)


@router.post('/kody', response_model=KodyLegitymacjiResponse)
def post_kody_legitymacji(nr_albumu: Optional[str] = None,
                         current_user: CurrentUser = Depends(get_current_wf_user),
                         ) -> KodyLegitymacjiResponse:
  """
  Jednorazowy odczyt kodu QR i kodu aktywacyjnego gotowej legitymacji.

  Wymaga gotowego zamówienia; Frappe zostawia ślad audytowy (komentarz na
  zamówieniu i wpis w logu). Dane nie są przechowywane przez hub.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    # 2026-11-08: ślad w logu huba - kto i kiedy odczytał kody (Frappe
    # zostawia własny komentarz na zamówieniu, to jest druga strona audytu).
    log.info('legitymacja/kody: ujawnienie kodów, album=%s user=%s',
             album, user_email(current_user))
    return KodyLegitymacjiResponse(code=0, message='OK',
                                   kody=db_pobierz_kody_legitymacji(album))
  except HTTPException:
    raise
  except Exception as e:
    log.info('legitymacja/kody: %s', e)
    return KodyLegitymacjiResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), kody=None)


@router.post('/uniewaznij', response_model=ZamowienieResponse)
def post_uniewaznij_legitymacje(dane: Optional[UniewaznienieBody] = None,
                               nr_albumu: Optional[str] = None,
                               current_user: CurrentUser = Depends(get_current_wf_user),
                               ) -> ZamowienieResponse:
  """
  Wniosek o unieważnienie legitymacji (np. zgubienie) - tworzy zamówienie
  unieważnienia po stronie Frappe.
  """
  dane = dane or UniewaznienieBody()
  try:
    album = allowed_album(current_user, nr_albumu)
    return ZamowienieResponse(
      code=0, message='OK',
      zamowienie=db_zamow_uniewaznienie_legitymacji(album, dane.powod))
  except HTTPException:
    raise
  except Exception as e:
    log.info('legitymacja/uniewaznij: %s', e)
    return ZamowienieResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), zamowienie=None)
