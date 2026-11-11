#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 2026-11-08: plan zajęć studenta i wykładowcy przez API portalowe education
# (education.portal.api.get_schedule).
"""Plan zajęć - API portalowe Frappe Education (``education.portal.api``).

Mapowanie pojęć:

  EduScheduleEntry      Course Schedule (data, godziny, sala, grupa, prowadzący)
  EduCourseRef          Course (kod, nazwa, rodzaj zajęć)
  EduPlan               odpowiedź portalu zawężona do listy wpisów

Tożsamość: hub przekazuje numer albumu studenta albo e-mail wykładowcy -
``education`` sam rozpoznaje rekord ``Student``/``Instructor`` po tych
identyfikatorach (``education/portal/auth.py``: ``resolve_student_ref``,
``resolve_instructor_ref``).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduCourseRef(BaseModel):
    """Przedmiot w planie (Course)."""

    code: str = ""
    name: str = ""
    type: Optional[str] = None  # Course.class_type: Lecture/Tutorial/Lab


class EduScheduleEntry(BaseModel):
    """Jeden wpis planu (Course Schedule)."""

    id: str
    data: str = ""  # YYYY-MM-DD
    dzien_tygodnia: Optional[int] = None  # 1 = poniedziałek (ISO)
    od: str = ""  # HH:MM:SS
    do: str = ""
    przedmiot: EduCourseRef
    grupa: str = ""
    sala: Optional[str] = None
    wykladowca: Optional[str] = None
    kierunek: Optional[str] = None
    tytul: Optional[str] = None
    kolor: Optional[str] = None


class EduPlan(BaseModel):
    """Plan zajęć w zakresie dat."""

    od: str
    do: str
    wpisy: List[EduScheduleEntry] = []


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("plan.py: %s: %s", method, exc)
        raise ConnectorError("plan.py: %s: %s" % (method, exc)) from exc


def _entry(row: dict) -> EduScheduleEntry:
    przedmiot = row.get("course") or {}
    return EduScheduleEntry(
        id=row.get("name") or "",
        data=str(row.get("date") or ""),
        dzien_tygodnia=row.get("weekday"),
        od=str(row.get("from") or ""),
        do=str(row.get("to") or ""),
        przedmiot=EduCourseRef(
            code=przedmiot.get("code") or "",
            name=przedmiot.get("name") or "",
            type=przedmiot.get("type"),
        ),
        grupa=row.get("group") or "",
        sala=row.get("room"),
        wykladowca=row.get("lecturer"),
        kierunek=row.get("program"),
        tytul=row.get("title"),
        kolor=row.get("color"),
    )


def _plan(result: dict) -> EduPlan:
    return EduPlan(
        od=str(result.get("from") or ""),
        do=str(result.get("to") or ""),
        wpisy=[_entry(row) for row in result.get("entries") or []],
    )


def db_get_plan_studenta(
    nr_albumu: str,
    od: Optional[str] = None,
    do: Optional[str] = None,
    kierunek: Optional[str] = None,
) -> EduPlan:
    """Plan studenta: zajęcia grup, do których jest zapisany."""
    params: dict = {"student": nr_albumu}
    if od:
        params["from_date"] = od
    if do:
        params["to_date"] = do
    if kierunek:
        params["program"] = kierunek
    return _plan(_call("get_schedule", params))


def db_get_plan_wykladowcy(
    email: str,
    od: Optional[str] = None,
    do: Optional[str] = None,
) -> EduPlan:
    """Plan wykładowcy: zajęcia grup, które prowadzi."""
    params: dict = {"instructor": email}
    if od:
        params["from_date"] = od
    if do:
        params["to_date"] = do
    return _plan(_call("get_schedule", params))


def db_get_kontekst_wykladowcy(email: str, semestr: Optional[str] = None) -> dict:
    """Grupy i przedmioty wykładowcy (``get_lecturer_context``)."""
    params: dict = {"instructor": email}
    if semestr:
        params["academic_term"] = semestr
    return _call("get_lecturer_context", params)
