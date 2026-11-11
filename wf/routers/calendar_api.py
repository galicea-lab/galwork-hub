# coding: utf-8
# 2026-11-10: dawna wersja kalendarza zastąpiona przekierowaniem
# do modułu kalendarz/ (GET /v1/kalendarz/).
# Zostaje tylko dla zgodności - z GET /v1/wf/ korzystają jeszcze strony
# zdarzeń galwork-dashboard i galwork-arena. Nowy kod: /v1/kalendarz/.

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from kalendarz.router import get_kalendarz
from kalendarz.schemas import KalendarzResponse
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser

log = logging.getLogger(__name__)


router_calendar = APIRouter(
  prefix="",
  tags=['events'],
  responses={404: {"description": "Not found"}},
)


@router_calendar.get('/', response_model=KalendarzResponse, deprecated=True)
def events_get(start: str, end: str,
               user: Annotated[Optional[str], Query(
                 description="Nieużywany (zachowany dla zgodności)", min_length=3)] = None,
               db: Session = Depends(get_db),
               current_user: CurrentUser = Depends(get_current_wf_user),
               ) -> KalendarzResponse:
  """
  Zdarzenia zalogowanego użytkownika w zakresie dat `start`-`end` - to samo co
  `GET /v1/kalendarz/?od=&do=` (rola ustalana z Education). Przestarzałe.
  """
  return get_kalendarz(od=start, do=end, zrodla=None, rola=None, nr_albumu=None,
                       email=None, db=db, current_user=current_user)
