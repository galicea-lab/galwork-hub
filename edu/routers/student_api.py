# coding: utf-8
# źródłem danych jest Frappe Education (connector.frappe_education.*),
# a autoryzacja korzysta z tokenu Keycloak (edu.dependencies).
# Student widzi tylko własne dane; pracownik dziekanatu - dane wszystkich studentów.

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from connector.frappe_education.student import (
  EduStudent1,
  EduStudentStudia,
  db_email2nr_albumu,
  db_get_student_form,
  db_get_student_studia,
  db_get_studenci_aktywni,
)
from connector.frappe_education.wydruki import (
  EduWydruk,
  db_get_plik_wydruku,
  db_get_rej_wydruku,
)
from edu.dependencies import is_own_email, is_staff, require_edu_staff, user_email
from edu.util import content_disposition
from wf.dependencies import get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

router = APIRouter(
  prefix="",
  tags=['studenci'],
  responses={404: {"description": "Not found"}},
)

log = logging.getLogger(__name__)

class StudentForm(BaseModel):
  code: int = Field(None, description='kod wykonania')
  message: str = Field(None, description='komunikat')
  student : Optional[Dict[str, Any]] = None

class StudentStudia(BaseModel):
  code: int = Field(None, description='kod wykonania')
  message: str = Field(None, description='komunikat')
  student : Optional[EduStudentStudia] = None


class Studenci(BaseModel):
  code: int = Field(None, description='kod wykonania')
  message: str = Field(None, description='komunikat')
  studenci: Optional[List[EduStudent1]]=None


# 2026-11-05: numer albumu zalogowanego studenta albo podany numer, jeśli
# pytający jest pracownikiem dziekanatu.
def _allowed_album(current_user: CurrentUser, nr_albumu: Optional[str]) -> str:
  if is_staff(current_user):
    if not nr_albumu:
      raise HTTPException(status_code=400, detail='Podaj nr_albumu')
    return nr_albumu
  own = db_email2nr_albumu(user_email(current_user))
  if not own or (nr_albumu and nr_albumu != own):
    raise HTTPException(status_code=403, detail='Zabroniony dostęp')
  return own


@router.get('/student/form', response_model=StudentForm)
def get_student_form(nr_albumu : str, kierunek:str,
               current_user: CurrentUser = Depends(get_current_wf_user),
               )->StudentForm:
  """
  Dane studenta - do formularzy
  """
  try:
    # Uprawnienia przed pobraniem danych studenta (student -
    # tylko własny numer albumu, dziekanat - dowolny); odpowiedź jak dotąd.
    try:
      album = _allowed_album(current_user, nr_albumu)
    except HTTPException:
      log.info('student/form: zabroniony dostęp')
      return StudentForm(code=-2, message='Zabroniony dostęp', student=None)
    student = db_get_student_form(album, kierunek)
    return StudentForm(code=0, message='OK', student=student)
  except Exception as e:
    log.info('student/form: %s', e)
    return StudentForm(code=-1, message='Niepowodzenie: %s' % error_message(e), student=None)

@router.get('/student/studia', response_model=StudentStudia)
def get_student_studies(email: Optional[str] = None, nr_albumu: Optional[str] = None,
                current_user: CurrentUser = Depends(get_current_wf_user),
                )->StudentStudia:
  """
  Dane studenta na podstawie emaila albo numeru albumu.
  Student podaje własny e-mail (albo nic); numer albumu - tylko dziekanat.
  """
  try:
    staff = is_staff(current_user)
    if not staff:
      if nr_albumu or (email and not is_own_email(current_user, email)):
        return StudentStudia(code=-2, message='Zabroniony dostęp', student=None)
      email = user_email(current_user)
    if (email and nr_albumu) or ((not email) and (not nr_albumu)):
      return StudentStudia(code=-3, message='Musisz podać albo email albo numer albumu', student=None)
    student = db_get_student_studia(email, nr_albumu)
    return StudentStudia(code=0, message='OK', student= student)
  except Exception as e:
    return StudentStudia(code=-1, message='Niepowodzenie: %s' % error_message(e), student=None)


@router.get('/studenci', response_model=Studenci)
def list_students(rok: Optional[str] = None, letni: Optional[int] = None, kierunek: Optional[str] = None,
                  nr_semestru:Optional[int]=None,
                  current_user: CurrentUser = Depends(require_edu_staff),
                 ) -> Studenci:
  """
  Lista aktywnych studentów - tylko dla pracowników dziekanatu.
  """
  try:
      students = db_get_studenci_aktywni(rok, letni, kierunek, nr_semestru)
      return Studenci(code=0, message='', studenci=students)
  except Exception as e:
    return Studenci(code=-1, message='Niepowodzenie: %s' % error_message(e), studenci=[])


# 2026-11-05: przy błędzie handler zwracał None zamiast listy.
@router.get('/wydruki', response_model=List[EduWydruk])
def list_printouts(nr_albumu : Optional[str] = None,
                   current_user: CurrentUser = Depends(get_current_wf_user),
                   )->List[EduWydruk]:
    """
    Rejestr wydruków (zaświadczeń, decyzji) studenta.
    """
    album = _allowed_album(current_user, nr_albumu)
    try:
      return db_get_rej_wydruku(album)
    except Exception as e:
      log.info('wydruki: %s', e)
      return []


@router.get('/plik_wydruku')
def get_printout_file(id : str, fn : str, nr_albumu : Optional[str] = None,
                     current_user: CurrentUser = Depends(get_current_wf_user),
                     ):
  """
  Plik wydruku. Student może pobrać tylko wydruk ze swojego rejestru.
  """
  album = _allowed_album(current_user, nr_albumu) if not is_staff(current_user) else None
  if album is not None:
    if id not in {w.id for w in db_get_rej_wydruku(album)}:
      raise HTTPException(status_code=403, detail='Zabroniony dostęp')
  try:
    file_content = db_get_plik_wydruku(id, fn)
  except Exception as e:
    log.info('plik_wydruku: %s', e)
    raise HTTPException(status_code=404, detail='Nie znaleziono pliku') from e
  headers = {'Content-Disposition': content_disposition(fn)}
  return Response(file_content, headers=headers, media_type='application/octet-stream')
