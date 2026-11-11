# coding: utf-8
# należności studenta w serwisie (education.portal.api.get_fees).
# Płatność online to osobny krok (bramka + handle_payment_success we Frappe).
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.oplaty import EduOplaty, db_get_oplaty_studenta
from edu.dependencies import allowed_album
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/oplaty", tags=["opłaty"],
                   responses={404: {"description": "Not found"}})


class OplatyResponse(BaseModel):
  code: int = 0
  message: str = ''
  oplaty: Optional[EduOplaty] = None


@router.get('', response_model=OplatyResponse)
def get_oplaty(nr_albumu: Optional[str] = None,
               current_user: CurrentUser = Depends(get_current_wf_user),
               ) -> OplatyResponse:
  """
  Należności studenta: kwoty, terminy, stan (paid/due/overdue) i sumy.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return OplatyResponse(code=0, message='OK', oplaty=db_get_oplaty_studenta(album))
  except HTTPException:
    raise
  except Exception as e:
    log.info('oplaty: %s', e)
    return OplatyResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), oplaty=None)
