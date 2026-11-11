# coding: utf-8
# źródłem danych jest Frappe Education (connector.frappe_education.slowniki)
# Frappe Education nie ma słownika TERYT.

from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from connector.frappe_education.slowniki import (
  EduAcademicYear,
  EduDeanOffice,
  EduFieldOfStudy,
  EduInstitute,
  db_get_academic_years,
  db_get_dean_offices,
  db_get_fields_of_study,
  db_get_institutes,
)
from wf.dependencies import get_current_wf_user
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

# Słowniki wymagają zalogowania (dowolny użytkownik).
router = APIRouter(
    prefix="",
    tags=['slowniki'],
    dependencies=[Depends(get_current_wf_user)],
)

class _PolishAlias(BaseModel):
    """Model z polskim aliasem serializowanym do JSON (kompatybilność klientów)."""

    model_config = ConfigDict(populate_by_name=True)


class FieldsOfStudyResponse(_PolishAlias):
    code: int
    message: str
    fields_of_study: List[EduFieldOfStudy] = Field(alias='kierunki')

class AcademicYearsResponse(_PolishAlias):
    code: int
    message: str
    academic_years: List[EduAcademicYear] = Field(alias='lata')

class DeanOfficesResponse(_PolishAlias):
    code: int
    message: str
    dean_offices: List[EduDeanOffice] = Field(alias='dziekanaty')

class InstitutesResponse(_PolishAlias):
    code: int
    message: str
    institutes: List[EduInstitute] = Field(alias='instytuty')


@router.get('/kierunki', response_model=FieldsOfStudyResponse)
def get_fields_of_study()->Dict:
    """
    Lista kierunków studiów
    """
    try:
        fields_of_study = db_get_fields_of_study()
        return {
          'code': 0,
          'message':'OK',
          'kierunki':fields_of_study
        }
    except Exception as e:
        return {
            'code': -7,
            'message': 'Niepowodzenie: %s' % error_message(e),
            'kierunki': []
        }


@router.get('/lata', response_model=AcademicYearsResponse)
def get_academic_years()->Dict:
    """
    Lista lat akademickich
    """
    try:
        academic_years = db_get_academic_years()
        return {
            'code': 0,
            'message': '',
            'lata': academic_years
        }
    except Exception as e:
        return {
            'code': -7,
            'message': 'Niepowodzenie: %s' % error_message(e),
            'lata': []
        }


@router.get('/dziekanaty', response_model=DeanOfficesResponse)
def get_dean_offices()->Dict:
    """
    Lista dziekanatów
    """
    try:
        dean_offices = db_get_dean_offices()
        return {
            'code': 0,
            'message': 'OK',
            'dziekanaty': dean_offices
        }
    except Exception as e:
        return {
            'code': -1,
            'message': 'Niepowodzenie: %s' % error_message(e),
            'dziekanaty': []
        }

@router.get('/instytuty', response_model=InstitutesResponse)
def get_institutes()->Dict:
    """
    Lista instytutów
    """
    try:
        institutes = db_get_institutes()
        return {
            'code': 0,
            'message': 'OK',
            'instytuty': institutes
        }
    except Exception as e:
        return {
            'code': -1,
            'message': 'Niepowodzenie: %s' % error_message(e),
            'instytuty': []
        }
