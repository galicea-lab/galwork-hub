# coding: utf-8
# oceny studenta i oceny wykładowcy w serwisie.
# Student: education.portal.api.get_grades (zaliczenie i średnia liczone przez
# Frappe). Wykładowca: siatka ocen grupy (get_group_assessments +
# get_group_roster) i zapis wyników (save_group_results).
# Zmiany:
# 2026-11-08: Karta Okresowych Osiągnięć (GET /oceny/karta) -
# education.portal.api.get_semester_card (connector/frappe_education/evaluation.py).

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from connector.frappe_education.evaluation import EduKartaOkresowa, db_get_karta_okresowa
from connector.frappe_education.oceny import (
  EduOceny,
  EduPlanOceniania,
  EduStudentGrupy,
  EduZapisOcen,
  db_get_oceny_grupy,
  db_get_oceny_studenta,
  db_save_oceny_grupy,
)
from edu.dependencies import allowed_album, lecturer_email
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/oceny", tags=["oceny"],
                   responses={404: {"description": "Not found"}})


class OcenyResponse(BaseModel):
  code: int = 0
  message: str = ''
  oceny: Optional[EduOceny] = None


class KartaOkresowaResponse(BaseModel):
  code: int = 0
  message: str = ''
  karta: Optional[EduKartaOkresowa] = None


class OcenyGrupyResponse(BaseModel):
  code: int = 0
  message: str = ''
  grupa: Optional[str] = None
  wykladowca: Optional[str] = None
  plany: List[EduPlanOceniania] = []
  studenci: List[EduStudentGrupy] = []


class WpisOceny(BaseModel):
  student: str
  punkty: Dict[str, float] = {}
  suma: Optional[float] = None
  komentarz: Optional[str] = None


class OcenyGrupyBody(BaseModel):
  assessment_plan: str
  wpisy: List[WpisOceny] = []
  zatwierdz: bool = False
  grupa: Optional[str] = None
  wykladowca: Optional[str] = None


class ZapisOcenResponse(BaseModel):
  code: int = 0
  message: str = ''
  wynik: Optional[EduZapisOcen] = None


@router.get('', response_model=OcenyResponse)
def get_oceny(rok: Optional[str] = None, semestr: Optional[str] = None,
              nr_albumu: Optional[str] = None,
              current_user: CurrentUser = Depends(get_current_wf_user),
              ) -> OcenyResponse:
  """
  Karta ocen studenta: przedmioty, podejścia, średnia i rozliczenie semestrów.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return OcenyResponse(code=0, message='OK',
                         oceny=db_get_oceny_studenta(album, rok, semestr))
  except HTTPException:
    raise
  except Exception as e:
    log.info('oceny: %s', e)
    return OcenyResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), oceny=None)


@router.get('/karta', response_model=KartaOkresowaResponse)
def get_karta_okresowa(semestr: Optional[str] = None, kierunek: Optional[str] = None,
                       nr_albumu: Optional[str] = None,
                       current_user: CurrentUser = Depends(get_current_wf_user),
                       ) -> KartaOkresowaResponse:
  """
  Karta Okresowych Osiągnięć Studenta (KOOS) jednego semestru: przedmioty,
  forma zajęć i zaliczenia, podejścia z terminami, oceny, wykładowcy, ECTS.

  ``semestr`` = nazwa Academic Term (domyślnie ostatni rozliczony semestr),
  ``kierunek`` = nazwa Program (domyślnie ostatni zapis). Student widzi własną
  kartę, dziekanat podaje ``nr_albumu``.
  """
  try:
    album = allowed_album(current_user, nr_albumu)
    return KartaOkresowaResponse(code=0, message='OK',
                                 karta=db_get_karta_okresowa(album, semestr, kierunek))
  except HTTPException:
    raise
  except Exception as e:
    log.info('oceny/karta: %s', e)
    return KartaOkresowaResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), karta=None)


@router.get('/grupa', response_model=OcenyGrupyResponse)
def get_oceny_grupy(grupa: str, email: Optional[str] = None,
                    current_user: CurrentUser = Depends(get_current_wf_user),
                    ) -> OcenyGrupyResponse:
  """
  Siatka ocen grupy: plany oceniania z kryteriami oraz studenci grupy.

  Tylko prowadzący grupę (albo dziekanat, który poda e-mail wykładowcy);
  zakres grup pilnuje Frappe (``_assert_group`` w education.portal.lecturer).
  """
  try:
    who = lecturer_email(current_user, email)
    plany, studenci = db_get_oceny_grupy(grupa, who)
    return OcenyGrupyResponse(code=0, message='OK', grupa=grupa, wykladowca=who,
                              plany=plany, studenci=studenci)
  except HTTPException:
    raise
  except Exception as e:
    log.info('oceny/grupa: %s', e)
    return OcenyGrupyResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), grupa=grupa)


@router.post('/grupa', response_model=ZapisOcenResponse)
def post_oceny_grupy(dane: OcenyGrupyBody, grupa: Optional[str] = None,
                     current_user: CurrentUser = Depends(get_current_wf_user),
                     ) -> ZapisOcenResponse:
  """
  Zapis ocen grupy (``wpisy`` = ``[{student, punkty{kryterium: punkty},
  suma, komentarz}]``).

  ``zatwierdz=True`` od razu zatwierdza wyniki; w przeciwnym razie zostają
  wersją roboczą. Ocena końcowa i walidacja sumy liczone są po stronie Frappe.
  """
  try:
    who = lecturer_email(current_user, dane.wykladowca)
    target = grupa or dane.grupa
    if not target:
      raise HTTPException(status_code=400, detail='Podaj grupę')
    wpisy = [
      {
        'student': w.student,
        'details': w.punkty,
        'total_score': w.suma,
        'comment': w.komentarz,
      }
      for w in dane.wpisy
    ]
    return ZapisOcenResponse(
      code=0, message='OK',
      wynik=db_save_oceny_grupy(target, who, dane.assessment_plan, wpisy, dane.zatwierdz))
  except HTTPException:
    raise
  except Exception as e:
    log.info('oceny/grupa (POST): %s', e)
    return ZapisOcenResponse(code=-1, message='Niepowodzenie: %s' % error_message(e), wynik=None)
