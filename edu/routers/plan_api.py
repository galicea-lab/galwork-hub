# coding: utf-8
# plan zajęć w serwisie studenckim - student widzi własny plan,
# wykładowca swój; dziekanat może wskazać numer albumu. Dane: konektor
# connector/frappe_education/plan.py (API portalowe education).
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.plan import (
  EduPlan,
  db_get_kontekst_wykladowcy,
  db_get_plan_studenta,
  db_get_plan_wykladowcy,
)
from edu.dependencies import allowed_album, lecturer_email
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/plan", tags=["plan zajęć"],
                   responses={404: {"description": "Not found"}})


class PlanResponse(BaseModel):
  code: int = 0
  message: str = ''
  plan: Optional[EduPlan] = None


class KontekstWykladowcyResponse(BaseModel):
  code: int = 0
  message: str = ''
  kontekst: Optional[dict] = None


@router.get('', response_model=PlanResponse)
def get_plan(od: Optional[str] = None, do: Optional[str] = None,
             nr_albumu: Optional[str] = None, kierunek: Optional[str] = None,
             current_user: CurrentUser = Depends(get_current_wf_user),
             ) -> PlanResponse:
  """
  Plan zajęć studenta.

  Student nie podaje numeru albumu (liczy się tożsamość z tokenu); dziekanat
  podaje `nr_albumu`. Zakres dat jest opcjonalny (domyślnie tydzień w Frappe).
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return PlanResponse(code=0, message='OK',
                        plan=db_get_plan_studenta(album, od, do, kierunek))
  except HTTPException:
    raise
  except Exception as e:
    log.info('plan: %s', e)
    return PlanResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), plan=None)


@router.get('/wykladowca', response_model=PlanResponse)
def get_plan_wykladowcy(od: Optional[str] = None, do: Optional[str] = None,
                        email: Optional[str] = None,
                        current_user: CurrentUser = Depends(get_current_wf_user),
                        ) -> PlanResponse:
  """
  Plan zajęć wykładowcy - grupy, które prowadzi.

  Wykładowca jest rozpoznawany po e-mailu z tokenu Keycloak; dziekanat może
  wskazać dowolny e-mail wykładowcy parametrem `email`.
  """
  try:
    who = lecturer_email(current_user, email)
    return PlanResponse(code=0, message='OK', plan=db_get_plan_wykladowcy(who, od, do))
  except HTTPException:
    raise
  except Exception as e:
    log.info('plan/wykladowca: %s', e)
    return PlanResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), plan=None)


@router.get('/wykladowca/kontekst', response_model=KontekstWykladowcyResponse)
def get_kontekst_wykladowcy(semestr: Optional[str] = None, email: Optional[str] = None,
                            current_user: CurrentUser = Depends(get_current_wf_user),
                            ) -> KontekstWykladowcyResponse:
  """
  Grupy i przedmioty wykładowcy (dane do nagłówka ekranu wykładowcy).
  """
  try:
    who = lecturer_email(current_user, email)
    return KontekstWykladowcyResponse(code=0, message='OK',
                                      kontekst=db_get_kontekst_wykladowcy(who, semestr))
  except HTTPException:
    raise
  except Exception as e:
    log.info('plan/wykladowca/kontekst: %s', e)
    return KontekstWykladowcyResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), kontekst=None)
