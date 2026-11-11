# coding: utf-8
# "Moje studia" - dane studenta, zapisy na kierunek i statusy
# semestrów (education.portal.api.get_studies). Student widzi tylko siebie,
# dziekanat może wskazać numer albumu.
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.studia import MojeStudia, db_get_moje_studia
from edu.dependencies import allowed_album
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# Odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/studia", tags=["moje studia"],
                   responses={404: {"description": "Not found"}})


class StudiaResponse(BaseModel):
  code: int = 0
  message: str = ''
  studia: Optional[MojeStudia] = None


@router.get('', response_model=StudiaResponse)
def get_moje_studia(nr_albumu: Optional[str] = None,
                    current_user: CurrentUser = Depends(get_current_wf_user),
                    ) -> StudiaResponse:
  """
  Profil studenta, zapisy na kierunek i rozliczenie semestrów.

  Student nie podaje numeru albumu; dziekanat podaje go jawnie.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return StudiaResponse(code=0, message='OK', studia=db_get_moje_studia(album))
  except HTTPException:
    raise
  except Exception as e:
    log.info('studia: %s', e)
    return StudiaResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), studia=None)
