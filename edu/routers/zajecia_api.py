# coding: utf-8
# źródłem danych jest Frappe Education (connector.frappe_education.zajecia).

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from connector.frappe_education.zajecia import (
    EduGroupStaffMember,
    EduGroupSubject,
    EduRoom,
    EduSemester,
    EduStaffMember,
    db_edu_group_staff,
    db_edu_group_subjects,
    db_edu_rooms,
    db_edu_semesters,
    db_edu_staff,
)
from wf.dependencies import get_current_wf_user
# Odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

class _PolishAlias(BaseModel):
    """Model z polskim aliasem serializowanym do JSON (kompatybilność klientów)."""

    model_config = ConfigDict(populate_by_name=True)


class RoomsResponse(_PolishAlias):
    code: int
    message: str
    rooms: List[EduRoom] = Field(alias='sale')

class StaffResponse(_PolishAlias):
    code: int
    message: str
    staff: List[EduStaffMember] = Field(alias='kadra')

class SemestersResponse(_PolishAlias):
    code: int
    message: str
    semesters: List[EduSemester] = Field(alias='semestry')

class GroupStaffResponse(_PolishAlias):
    code: int
    message: str
    group_staff: List[EduGroupStaffMember] = Field(alias='gpracownicy')

class GroupSubjectsResponse(_PolishAlias):
    code: int
    message: str
    group_subjects: List[EduGroupSubject] = Field(alias='gprzedmioty')

# Dane zajęć wymagają zalogowania (dowolny użytkownik).
router = APIRouter(
    prefix="",
    tags=['zajecia'],
    dependencies=[Depends(get_current_wf_user)],
)


@router.get('/sale', response_model=RoomsResponse)
def get_rooms()->RoomsResponse:
    try:
        rooms = db_edu_rooms()
        return RoomsResponse(
            code=0,
            message='OK',
            rooms=rooms
        )
    except Exception as e:
        return RoomsResponse(
            code=-7,
            message='Niepowodzenie: %s' % error_message(e),
            rooms=[]
        )


@router.get('/semestry/{rok:int}/{lato:int}', response_model=SemestersResponse)
def get_semesters(rok: int, lato: int)->SemestersResponse:
    try:
        semesters = db_edu_semesters(rok, lato)
        return SemestersResponse(
            code=0,
            message='OK',
            semesters=semesters
        )
    except Exception as e:
        return SemestersResponse(
            code=-7,
            message='Niepowodzenie: %s' % error_message(e),
            semesters=[]
        )

# 2026-11-05: poprawiony kod odpowiedzi (było code='0' także dla błędu).
@router.get('/kadra', response_model=StaffResponse)
def get_staff()->StaffResponse:
    try:
        staff = db_edu_staff()
        return StaffResponse(
            code=0,
            message='OK',
            staff=staff
        )
    except Exception as e:
        return StaffResponse(
            code=-7,
            message= 'Niepowodzenie: %s' % error_message(e),
            staff=[]
        )


@router.get('/gpracownicy/{rok:int}/{lato:int}',response_model=GroupStaffResponse)
def get_group_staff(rok: int, lato: int)->GroupStaffResponse:
    try:
        group_staff = db_edu_group_staff(rok,lato)
        return GroupStaffResponse(
            code=0,
            message='OK',
            group_staff=group_staff
        )
    except Exception as e:
        return GroupStaffResponse(
            code=-7,
            message='Niepowodzenie: %s' % error_message(e),
            group_staff=[]
        )


@router.get('/gprzedmioty/{rok:int}/{lato:int}',response_model=GroupSubjectsResponse)
def get_group_subjects(rok: int, lato: int)->GroupSubjectsResponse:
    try:
        group_subjects = db_edu_group_subjects(rok,lato)
        return GroupSubjectsResponse(
            code=0,
            message='OK',
            group_subjects=group_subjects
        )
    except Exception as e:
        return GroupSubjectsResponse(
            code=-7,
            message='Niepowodzenie: %s' % error_message(e),
            group_subjects=[]
        )
