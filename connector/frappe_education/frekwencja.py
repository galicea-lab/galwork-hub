#!/usr/bin/env python
# -*- coding: utf-8 -*-
# frekwencja studenta i lista obecności grupy przez API education 
# (get_attendance, get_group_attendance, save_group_attendance). 
# Zapis jest idempotentny po (student, grupa, data).
"""Frekwencja - API portalowe Frappe Education.

Student: podsumowanie obecności per grupa oraz lista nieobecności.
Wykładowca: lista obecności grupy na dany dzień i jej zapis.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduFrekwencjaOgolem(BaseModel):
    """Łączna frekwencja studenta."""

    obecny: int = 0
    nieobecny: int = 0
    procent: Optional[float] = None


class EduFrekwencjaGrupa(BaseModel):
    """Frekwencja w jednej grupie."""

    grupa: str = ""
    nazwa: Optional[str] = None
    przedmiot: Optional[str] = None
    obecny: int = 0
    nieobecny: int = 0
    urlop: int = 0
    procent: Optional[float] = None


class EduNieobecnosc(BaseModel):
    """Pojedyncza nieobecność."""

    data: str = ""
    grupa: Optional[str] = None
    zajecia: Optional[str] = None
    frekwencja: Optional[str] = None
    usprawiedliwiona: bool = False


class EduFrekwencjaStudent(BaseModel):
    """Frekwencja studenta."""

    student: str = ""
    ogolem: EduFrekwencjaOgolem = EduFrekwencjaOgolem()
    grupy: List[EduFrekwencjaGrupa] = []
    nieobecnosci: List[EduNieobecnosc] = []


class EduWpisFrekwencji(BaseModel):
    """Student w liście obecności grupy."""

    student: str = ""
    nazwisko: Optional[str] = None
    nr_albumu: Optional[str] = None
    zdjecie: Optional[str] = None
    nr_w_grupie: Optional[int] = None
    status: Optional[str] = None  # Present/Absent/Leave albo None (nieoznaczony)
    frekwencja: Optional[str] = None


class EduListaObecnosci(BaseModel):
    """Lista obecności grupy na dany dzień."""

    grupa: str = ""
    data: str = ""
    wpisy: List[EduWpisFrekwencji] = []
    data_zamrozenia: Optional[str] = None


class EduZapisFrekwencji(BaseModel):
    """Wynik zapisu listy obecności."""

    grupa: str = ""
    data: str = ""
    zapisane: List[str] = []
    pominiete: List[dict] = []


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("frekwencja.py: %s: %s", method, exc)
        raise ConnectorError("frekwencja.py: %s: %s" % (method, exc)) from exc


def db_get_frekwencja_studenta(
    nr_albumu: str,
    od: Optional[str] = None,
    do: Optional[str] = None,
) -> EduFrekwencjaStudent:
    """Frekwencja studenta (podsumowanie per grupa + nieobecności)."""
    params: dict = {"student": nr_albumu}
    if od:
        params["from_date"] = od
    if do:
        params["to_date"] = do
    result = _call("get_attendance", params)
    ogolem = result.get("overall") or {}
    return EduFrekwencjaStudent(
        student=result.get("student") or nr_albumu,
        ogolem=EduFrekwencjaOgolem(
            obecny=ogolem.get("present") or 0,
            nieobecny=ogolem.get("absent") or 0,
            procent=ogolem.get("percent"),
        ),
        grupy=[
            EduFrekwencjaGrupa(
                grupa=row.get("group") or "",
                nazwa=row.get("group_name"),
                przedmiot=row.get("course"),
                obecny=row.get("present") or 0,
                nieobecny=row.get("absent") or 0,
                urlop=row.get("leave") or 0,
                procent=row.get("percent"),
            )
            for row in result.get("groups") or []
        ],
        nieobecnosci=[
            EduNieobecnosc(
                data=str(row.get("date") or ""),
                grupa=row.get("group"),
                zajecia=row.get("course_schedule"),
                frekwencja=row.get("attendance"),
                usprawiedliwiona=bool(row.get("excused")),
            )
            for row in result.get("absences") or []
        ],
    )


def db_get_lista_obecnosci(
    grupa: str,
    wykladowca: str,
    data: Optional[str] = None,
) -> EduListaObecnosci:
    """Lista obecności grupy wykładowcy na dany dzień."""
    params: dict = {"group": grupa, "instructor": wykladowca}
    if data:
        params["date"] = data
    result = _call("get_group_attendance", params)
    return EduListaObecnosci(
        grupa=result.get("group") or grupa,
        data=str(result.get("date") or ""),
        wpisy=[
            EduWpisFrekwencji(
                student=row.get("student") or "",
                nazwisko=row.get("student_name"),
                nr_albumu=row.get("album_number"),
                zdjecie=row.get("image"),
                nr_w_grupie=row.get("group_roll_number"),
                status=row.get("status"),
                frekwencja=row.get("attendance"),
            )
            for row in result.get("students") or []
        ],
        data_zamrozenia=result.get("freeze_date"),
    )


def db_save_lista_obecnosci(
    grupa: str,
    wykladowca: str,
    data: str,
    wpisy: List[dict],
) -> EduZapisFrekwencji:
    """Zapis listy obecności (``wpisy`` = ``[{student, status}]``)."""
    params: dict = {
        "group": grupa,
        "instructor": wykladowca,
        "date": data,
        "entries": wpisy,
    }
    result = _call("save_group_attendance", params)
    return EduZapisFrekwencji(
        grupa=result.get("group") or grupa,
        data=str(result.get("date") or data),
        zapisane=result.get("saved") or [],
        pominiete=result.get("skipped") or [],
    )
