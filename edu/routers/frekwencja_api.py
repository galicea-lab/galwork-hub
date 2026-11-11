# coding: utf-8
# frekwencja studenta oraz lista obecności grupy dla wykładowcy
# (education.portal.api.get_attendance / get_group_attendance /
# save_group_attendance). Zapis idempotentny po (student, grupa, data).
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.frekwencja import (
  EduFrekwencjaStudent,
  EduListaObecnosci,
  EduZapisFrekwencji,
  db_get_frekwencja_studenta,
  db_get_lista_obecnosci,
  db_save_lista_obecnosci,
)
from edu.dependencies import allowed_album, lecturer_email
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# Odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/frekwencja", tags=["frekwencja"],
                   responses={404: {"description": "Not found"}})


class FrekwencjaResponse(BaseModel):
  code: int = 0
  message: str = ''
  frekwencja: Optional[EduFrekwencjaStudent] = None


class ListaObecnosciResponse(BaseModel):
  code: int = 0
  message: str = ''
  lista: Optional[EduListaObecnosci] = None


class ZapisFrekwencjiResponse(BaseModel):
  code: int = 0
  message: str = ''
  wynik: Optional[EduZapisFrekwencji] = None


class WpisFrekwencji(BaseModel):
  student: str
  status: str


class ZapisFrekwencjiBody(BaseModel):
  data: str
  wpisy: List[WpisFrekwencji] = []
  grupa: Optional[str] = None
  wykladowca: Optional[str] = None


@router.get('', response_model=FrekwencjaResponse)
def get_frekwencja(od: Optional[str] = None, do: Optional[str] = None,
                   nr_albumu: Optional[str] = None,
                   current_user: CurrentUser = Depends(get_current_wf_user),
                   ) -> FrekwencjaResponse:
  """
  Frekwencja studenta: podsumowanie per grupa i lista nieobecności.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return FrekwencjaResponse(code=0, message='OK',
                              frekwencja=db_get_frekwencja_studenta(album, od, do))
  except HTTPException:
    raise
  except Exception as e:
    log.info('frekwencja: %s', e)
    return FrekwencjaResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), frekwencja=None)


@router.get('/grupa', response_model=ListaObecnosciResponse)
def get_lista_obecnosci(grupa: str, data: Optional[str] = None, email: Optional[str] = None,
                        current_user: CurrentUser = Depends(get_current_wf_user),
                        ) -> ListaObecnosciResponse:
  """
  Lista obecności grupy na dany dzień - tylko dla prowadzącego (albo dziekanatu).
  """
  try:
    who = lecturer_email(current_user, email)
    return ListaObecnosciResponse(code=0, message='OK',
                                  lista=db_get_lista_obecnosci(grupa, who, data))
  except HTTPException:
    raise
  except Exception as e:
    log.info('frekwencja/grupa: %s', e)
    return ListaObecnosciResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), lista=None)


@router.post('/grupa', response_model=ZapisFrekwencjiResponse)
def post_lista_obecnosci(dane: ZapisFrekwencjiBody, grupa: Optional[str] = None,
                         current_user: CurrentUser = Depends(get_current_wf_user),
                         ) -> ZapisFrekwencjiResponse:
  """
  Zapis listy obecności grupy (``wpisy`` = ``[{student, status}]``).

  `status`: ``Present`` / ``Absent`` / ``Leave``. Powtórny zapis tej samej daty
  nadpisuje wcześniejsze oznaczenia (idempotentnie per student).
  """
  try:
    who = lecturer_email(current_user, dane.wykladowca)
    target = grupa or dane.grupa
    if not target:
      raise HTTPException(status_code=400, detail='Podaj grupę')
    wpisy = [{'student': w.student, 'status': w.status} for w in dane.wpisy]
    return ZapisFrekwencjiResponse(
      code=0, message='OK',
      wynik=db_save_lista_obecnosci(target, who, dane.data, wpisy))
  except HTTPException:
    raise
  except Exception as e:
    log.info('frekwencja/grupa (POST): %s', e)
    return ZapisFrekwencjiResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), wynik=None)
