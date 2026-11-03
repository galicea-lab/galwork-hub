# coding: utf-8

from fastapi import Request, APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Annotated, Dict
import logging

from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# (2026-10-05): endpoint był dostępny bez tokenu (komentarz z wycofanym
# `lib.auth.CheckToken`) - teraz wymaga zalogowanego użytkownika workflow.

log = logging.getLogger(__name__)


router_calendar = APIRouter(
  prefix="",
  tags=['events'],
  responses={404: {"description": "Not found"}},
)

class Event(BaseModel):
  id: str = Field(None, description='ID')
  url: str = Field(None, description='URL')
  title: str = Field(None, description='Tytuł')
  allDay: bool = Field(None, description='Cały dzień?')
  url: str = Field(None, description='URL')
  start: str = Field(None, description='Data rozpoczęcia')
  end: str = Field(None, description='Data zakończenia')
  organizer: str = Field(None, description='Organizator')
  extendedProps: Dict #  { 'calendar': 'Microsoft365'  }

class Events(BaseModel):
  code: int = Field(None, description='kod wykonania')
  message: str = Field(None, description='komunikat')
  events: Optional[List[Event]]=None

@router_calendar.get('/', response_model=Events)
async def events_get(request: Request,
               start : str, end: str,
               user: Annotated[str, Query(description="Adres email osoby dla której szukamy danych",
                                min_length=3)]= None,
 #              user_or_client: TokenOwner = Depends(CheckToken()), \
               current_user : CurrentUser = Depends(get_current_wf_user),
               )->Events:
  """
    Pobieranie listy zdarzeń dladanej osoby, we wskazanymzakresie dat
    (range_min, range_max)
  """
  try:
    result = {
      'code':0,
      'message':'OK',
      'events':[
        {
          'id': '1',
          'url': '',
          'title': 'Początek reoku akademickiego',
          'allDay': True,
          'start': '2023-10-01',
          'end': '2023-10-01',
          'organizer': 'JW',
          'extendedProps': { 'calendar': 'AcademicYear'  }
        },
      ]}
    return result
  except Exception as e:
    log.info('Exception: %s' % e)
    return Events(code=-1, message='Niepowodzenie: %s' % e, events=None)
