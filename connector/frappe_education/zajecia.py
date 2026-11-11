#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding: utf-8
# 2026-10-05
"""Sale, kadra, semestry, grupy/przedmioty.

Mapowanie i - ważne - ograniczenia:

  Sala (sale)              -> Room (room_name, room_number,
                                        seating_capacity) - BEZ budynku,
                                        rodzaju sali, uwag, kolejności
                                        - zob. `EduRoom`.
  Pracownik (kadra)        -> Instructor (instructor_name, employee,
                                        department) - BEZ tytułu naukowego
                                        - Frappe Education
                                        go nie modeluje.
  Semestr (per kierunek+tok)  -> Academic Term (WSPÓLNY dla wszystkich
                                        programów) + Program - PRZYBLIŻENIE,
                                        zob. `db_edu_semesters` niżej.
  Przedmiot/Pracownik       -> Student Group + Student Group
      (rozliczenie "pensum"          Instructor - ale TYLKO struktura "kto
      dydaktycznego prowadzących)     uczy jaką grupę którego przedmiotu".
                                       Żadne z pól rozliczenia pensum
                                       (GN_PLAN_PENSUM, GN_WYK_PENSUM,
                                       GN_PROC, GN_ILOSC_GODZ, ...) NIE MA
                                       odpowiednika - Frappe Education nie
                                       jest systemem kadrowo-płacowym i nie
                                       liczy godzin dydaktycznych do pensum.
                                       Takie rozliczenie żyje normalnie w
                                       module HR/Payroll (np. ERPNext HR) -
                                       tu zwracane jako 0, nie zgadywane.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")


class EduRoom(BaseModel):
    id: str
    name: str
    room_number: str = ""
    capacity: int = 0
    building: Optional[str] = None  # NIEDOSTĘPNE
    room_type: Optional[str] = None  # NIEDOSTĘPNE
    remarks: str = ""  # NIEDOSTĘPNE


class EduStaffMember(BaseModel):
    id: str
    title: str = ""  # NIEDOSTĘPNE (tytuł naukowy)
    first_name: str = ""
    last_name: str = ""
    department: Optional[str] = None


class EduSemester(BaseModel):
    id: str
    rok: int
    letni: bool
    nazwa: str
    nr_semestru: Optional[int] = None  # NIEDOSTĘPNE, zob. docstring modułu
    k_skrot: str
    k_nazwa: str
    ects: Optional[float] = None  # suma ECTS programu (przybliżenie - patrz niżej)


class EduGroupSubject(BaseModel):
    id: str
    nazwa_grupy: str
    przedmiot: str
    forma: str = ""
    forma_zal: str = ""
    liczba_godzin: int = 0
    ects: float = 0.0


class EduGroupStaffMember(BaseModel):
    id: str
    nazwa_grupy: str
    przedmiot: str
    instruktor: str
    plan_godzin: int = 0  # 0 - brak rozliczenia pensum, zob. docstring modułu


def db_edu_rooms() -> List[EduRoom]:
    try:
        connector = get_connector()
        rooms = connector.get_list(
            "Room", fields=["name", "room_name", "room_number", "seating_capacity"],
            limit_page_length=500,
        )
        out = []
        for r in rooms:
            try:
                capacity = int(r.get("seating_capacity") or 0)
            except (TypeError, ValueError):
                capacity = 0
            out.append(EduRoom(
                id=r["name"], name=r.get("room_name") or r["name"],
                room_number=r.get("room_number") or "", capacity=capacity,
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("zajecia.py: db_edu_rooms: %s", exc)
        raise ConnectorError("zajecia.py: db_edu_rooms: %s" % exc) from exc


def db_edu_staff() -> List[EduStaffMember]:
    try:
        connector = get_connector()
        instructors = connector.get_list(
            "Instructor", fields=["name", "instructor_name", "department"],
            limit_page_length=1000,
        )
        out = []
        for i in instructors:
            full_name = i.get("instructor_name") or ""
            first, _, last = full_name.partition(" ")
            out.append(EduStaffMember(
                id=i["name"], first_name=first, last_name=last or full_name,
                department=i.get("department"),
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("zajecia.py: db_edu_staff: %s", exc)
        raise ConnectorError("zajecia.py: db_edu_staff: %s" % exc) from exc


def db_edu_semesters(year: int, summer: int) -> List[EduSemester]:
    """TODO: zamiast "Semestr" per (kierunek, tok/kohorta),
    Frappe Education ma `Academic Term` wspólny dla wszystkich programów.
    Tu: dla każdego `Academic Term` zgodnego z (year, summer) zwracany jest
    jeden wiersz na każdy `Program` - `NUMER_SEMESTRU` pozostaje `None`
    (nie da się go wyznaczyć bez kontekstu konkretnego zapisu studenta/
    kohorty - we Frappe numer semestru żyje na `Student Academic Status`,
    per zapis studenta, nie na poziomie "semestru w ogóle"), a `ECTS` to
    suma ECTS WSZYSTKICH kursów programu (`Program.courses[].ects_credits`),
    nie tylko tego semestru.

    Jeśli dokładne odwzorowanie "semestr N konkretnej kohorty programu X" MUSI
    być dostępne, to - wymagałoby to dodania do Education struktury kohort/naborów 
    podobnej do `edu.batch` z Odoo.
    """
    try:
        connector = get_connector()
        terms = connector.get_list(
            "Academic Term",
            filters=[["is_summer_term", "=", 1 if int(summer) else 0]],
            fields=["name", "term_name", "academic_year"],
            limit_page_length=50,
        )
        terms = [t for t in terms if str(t.get("academic_year", "")).startswith(str(year))]
        if not terms:
            return []
        programs = connector.get_list(
            "Program", fields=["name", "program_name", "program_abbreviation"],
            limit_page_length=500,
        )
        out = []
        for term in terms:
            for program in programs:
                program_doc = connector.get_doc("Program", program["name"])
                ects = sum(
                    float(c.get("ects_credits") or 0) for c in (program_doc.get("courses") or [])
                )
                out.append(EduSemester(
                    id=f"{term['name']}::{program['name']}",
                    rok=int(year), letni=bool(int(summer)),
                    nazwa=term.get("term_name") or term["name"],
                    nr_semestru=None,
                    k_skrot=program.get("program_abbreviation") or "",
                    k_nazwa=program.get("program_name") or "",
                    ects=ects or None,
                ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("zajecia.py: db_edu_semesters: %s", exc)
        raise ConnectorError("zajecia.py: db_edu_semesters: %s" % exc) from exc


def _student_groups_for_term(connector, year: int, summer: int) -> list[dict]:
    terms = connector.get_list(
        "Academic Term",
        filters=[["is_summer_term", "=", 1 if int(summer) else 0]],
        fields=["name", "academic_year"], limit_page_length=50,
    )
    terms = [t for t in terms if str(t.get("academic_year", "")).startswith(str(year))]
    if not terms:
        return []
    return list(connector.get_all(
        "Student Group",
        filters=[["academic_term", "in", [t["name"] for t in terms]]],
        fields=["name", "student_group_name", "course"],
    ))


def db_edu_group_subjects(year: int, summer: int) -> List[EduGroupSubject]:
    """tylko struktura (grupa + przedmiot); `forma`/`forma_zal`/`liczba_godzin`/
    `ects` dociągane z `Course`."""
    try:
        connector = get_connector()
        groups = _student_groups_for_term(connector, year, summer)
        if not groups:
            return []
        course_names = sorted({g["course"] for g in groups if g.get("course")})
        courses = {}
        if course_names:
            for c in connector.get_list(
                "Course", filters=[["name", "in", course_names]],
                fields=["name", "course_name", "class_type", "passing_type", "contact_hours", "ects_credits"],
                limit_page_length=len(course_names),
            ):
                courses[c["name"]] = c
        out = []
        for g in groups:
            course = courses.get(g.get("course"), {})
            out.append(EduGroupSubject(
                id=g["name"],
                nazwa_grupy=g.get("student_group_name") or g["name"],
                przedmiot=course.get("course_name") or g.get("course") or "",
                forma=course.get("class_type") or "",
                forma_zal=course.get("passing_type") or "",
                liczba_godzin=int(course.get("contact_hours") or 0),
                ects=float(course.get("ects_credits") or 0),
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("zajecia.py: db_edu_group_subjects: %s", exc)
        raise ConnectorError("zajecia.py: db_edu_group_subjects: %s" % exc) from exc


def db_edu_group_staff(year: int, summer: int) -> List[EduGroupStaffMember]:
    """TYLKO "kto uczy jaką grupę"; `plan_godzin` jest zawsze 0 (brak rozliczenia pensum -
    zob. docstring modułu)."""
    try:
        connector = get_connector()
        groups = _student_groups_for_term(connector, year, summer)
        if not groups:
            return []
        course_names = sorted({g["course"] for g in groups if g.get("course")})
        courses = {}
        if course_names:
            for c in connector.get_list(
                "Course", filters=[["name", "in", course_names]],
                fields=["name", "course_name"], limit_page_length=len(course_names),
            ):
                courses[c["name"]] = c["course_name"]
        out = []
        for g in groups:
            group_doc = connector.get_doc("Student Group", g["name"])
            for instr in (group_doc.get("instructors") or []):
                out.append(EduGroupStaffMember(
                    id=f"{g['name']}::{instr.get('instructor')}",
                    nazwa_grupy=g.get("student_group_name") or g["name"],
                    przedmiot=courses.get(g.get("course"), g.get("course") or ""),
                    instruktor=instr.get("instructor_name") or instr.get("instructor") or "",
                    plan_godzin=0,
                ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("zajecia.py: db_edu_group_staff: %s", exc)
        raise ConnectorError("zajecia.py: db_edu_group_staff: %s" % exc) from exc
