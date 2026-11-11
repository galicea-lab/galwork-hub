#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 2026-11-08: oceny studenta przez API portalowe education
# (education.portal.api.get_grades). Zaliczenie i ocena liczone są po stronie
# Frappe (Grading Scale + portal.passing_percentage), a nie heurystyką na
# tekście oceny, jak w evaluation.py.
"""Oceny studenta - API portalowe Frappe Education.

Mapowanie:

  przedmiot      Course (kod, nazwa)
  final          ostatnie podejście (ostateczna ocena przedmiotu)
  podejscia      wszystkie zatwierdzone Assessment Result
  zaliczone      próg z konfiguracji ``portal.passing_percentage``
  SemestrStatus  Student Academic Status (rozliczenie semestru)
  Srednia        średnia ważona ECTS policzona przez Frappe
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduPrzedmiot(BaseModel):
    """Przedmiot w karcie ocen."""

    code: str = ""
    name: str = ""
    ects: float = 0
    passing_type: Optional[str] = None


class EduPodejscie(BaseModel):
    """Jedno podejście (jeden Assessment Result)."""

    id: str = ""
    plan: Optional[str] = None
    data: Optional[str] = None
    punkty: float = 0
    maksimum: float = 0
    procent: Optional[float] = None
    ocena: Optional[str] = None
    zaliczone: Optional[bool] = None


class EduOcena(BaseModel):
    """Ocena przedmiotu z listą podejść."""

    przedmiot: EduPrzedmiot
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None
    final: Optional[EduPodejscie] = None
    podejscia: List[EduPodejscie] = []
    zaliczone: Optional[bool] = None
    najlepszy_procent: Optional[float] = None


class EduSrednia(BaseModel):
    """Średnia z wyników (ETCS-weighted, liczona po stronie Frappe)."""

    metoda: str = "ects_weighted"
    wartosc: Optional[float] = None
    przedmioty: int = 0
    zaliczone: int = 0
    niezaliczone: int = 0


class EduSemestrStatus(BaseModel):
    """Rozliczenie semestru (Student Academic Status)."""

    id: str = ""
    zapis: Optional[str] = None
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None
    nr_semestru: Optional[int] = None
    status: Optional[str] = None
    letni: Optional[bool] = None
    do_sredniej: Optional[bool] = None
    zaplacony: Optional[bool] = None
    data_decyzji: Optional[str] = None


class EduOceny(BaseModel):
    """Karta ocen studenta."""

    student: str = ""
    prog_zaliczenia: float = 50
    przedmioty: List[EduOcena] = []
    srednia: EduSrednia = EduSrednia()
    semestry: List[EduSemestrStatus] = []


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("oceny.py: %s: %s", method, exc)
        raise ConnectorError("oceny.py: %s: %s" % (method, exc)) from exc


def _podejscie(row: dict) -> EduPodejscie:
    return EduPodejscie(
        id=row.get("assessment_result") or "",
        plan=row.get("assessment_plan"),
        data=row.get("date"),
        punkty=row.get("score") or 0,
        maksimum=row.get("maximum") or 0,
        procent=row.get("percentage"),
        ocena=row.get("grade"),
        zaliczone=row.get("passed"),
    )


def _ocena(row: dict) -> EduOcena:
    przedmiot = row.get("course") or {}
    final = row.get("final") or {}
    return EduOcena(
        przedmiot=EduPrzedmiot(
            code=przedmiot.get("code") or "",
            name=przedmiot.get("name") or "",
            ects=przedmiot.get("ects") or 0,
            passing_type=przedmiot.get("passing_type"),
        ),
        rok_akademicki=row.get("academic_year"),
        semestr=row.get("academic_term"),
        final=_podejscie(final) if final else None,
        podejscia=[_podejscie(item) for item in row.get("attempts") or []],
        zaliczone=row.get("passed"),
        najlepszy_procent=row.get("best_percentage"),
    )


def db_get_oceny_studenta(
    nr_albumu: str,
    rok: Optional[str] = None,
    semestr: Optional[str] = None,
) -> EduOceny:
    """Karta ocen studenta (opcjonalnie zawężona do roku/semestru)."""
    params: dict = {"student": nr_albumu}
    if rok:
        params["academic_year"] = rok
    if semestr:
        params["academic_term"] = semestr
    result = _call("get_grades", params)
    srednia = result.get("average") or {}
    return EduOceny(
        student=result.get("student") or nr_albumu,
        prog_zaliczenia=result.get("passing_percentage") or 50,
        przedmioty=[_ocena(row) for row in result.get("courses") or []],
        srednia=EduSrednia(
            metoda=srednia.get("method") or "ects_weighted",
            wartosc=srednia.get("value"),
            przedmioty=srednia.get("courses") or 0,
            zaliczone=srednia.get("passed") or 0,
            niezaliczone=srednia.get("failed") or 0,
        ),
        semestry=[
            EduSemestrStatus(
                id=row.get("name") or "",
                zapis=row.get("program_enrollment"),
                rok_akademicki=row.get("academic_year"),
                semestr=row.get("academic_term"),
                nr_semestru=row.get("semester_number"),
                status=row.get("status"),
                letni=bool(row.get("is_summer_semester")),
                do_sredniej=bool(row.get("counts_towards_average")),
                zaplacony=bool(row.get("is_paid")),
                data_decyzji=row.get("decision_date"),
            )
            for row in result.get("semesters") or []
        ],
    )


# oceny wykładowcy - siatka ocen grupy. Dwa wywołania portalu
# (get_group_assessments + get_group_roster) składają dane do jednego ekranu,
# zapis idzie przez save_group_results (walidacja sumy i skalowania w Frappe).
class EduKryterium(BaseModel):
    """Kryterium oceny z planu (Assessment Plan Criteria)."""

    kryterium: str = ""
    maksimum: float = 0
    waga: float = 0


class EduPlanOceniania(BaseModel):
    """Plan oceniania grupy (Assessment Plan)."""

    id: str = ""
    nazwa: Optional[str] = None
    przedmiot: Optional[str] = None
    grupa: Optional[str] = None
    grupa_ocen: Optional[str] = None
    skala: Optional[str] = None
    maksimum: float = 0
    data: Optional[str] = None
    semestr: Optional[str] = None
    rok_akademicki: Optional[str] = None
    zatwierdzony: bool = False
    kryteria: List[EduKryterium] = []
    wystawione: int = 0  # liczba zatwierdzonych Assessment Result


class EduStudentGrupy(BaseModel):
    """Student w siatce ocen grupy."""

    student: str = ""
    nazwisko: Optional[str] = None
    nr_albumu: Optional[str] = None
    zdjecie: Optional[str] = None
    nr_w_grupie: Optional[int] = None


class EduZapisOcen(BaseModel):
    """Wynik zapisu ocen (save_group_results)."""

    grupa: str = ""
    assessment_plan: str = ""
    zapisane: List[dict] = []
    count: int = 0


def _plan(row: dict) -> EduPlanOceniania:
    return EduPlanOceniania(
        id=row.get("name") or "",
        nazwa=row.get("assessment_name"),
        przedmiot=row.get("course"),
        grupa=row.get("student_group"),
        grupa_ocen=row.get("assessment_group"),
        skala=row.get("grading_scale"),
        maksimum=row.get("maximum_assessment_score") or 0,
        data=row.get("schedule_date"),
        semestr=row.get("academic_term"),
        rok_akademicki=row.get("academic_year"),
        zatwierdzony=(row.get("docstatus") == 1),
        kryteria=[
            EduKryterium(
                kryterium=item.get("criterion") or "",
                maksimum=item.get("maximum_score") or 0,
                waga=item.get("weightage") or 0,
            )
            for item in row.get("criteria") or []
        ],
        wystawione=row.get("results_submitted") or 0,
    )


def db_get_oceny_grupy(
    grupa: str,
    wykladowca: str,
) -> tuple[List[EduPlanOceniania], List[EduStudentGrupy]]:
    """Dane siatki ocen: plany oceniania grupy i jej studenci.

    Jedno wywołanie obsługuje ekran wykładowcy - dwa zapytania do Frappe
    (plany z kryteriami i lista grupy) zamiast osobnych endpointów po stronie
    frontendu.
    """
    plans = _call("get_group_assessments", {"group": grupa, "instructor": wykladowca})
    roster = _call("get_group_roster", {"group": grupa, "instructor": wykladowca})
    return (
        [_plan(row) for row in plans.get("plans") or []],
        [
            EduStudentGrupy(
                student=row.get("student") or "",
                nazwisko=row.get("student_name"),
                nr_albumu=row.get("album_number"),
                zdjecie=row.get("image"),
                nr_w_grupie=row.get("group_roll_number"),
            )
            for row in roster.get("students") or []
        ],
    )


def db_save_oceny_grupy(
    grupa: str,
    wykladowca: str,
    assessment_plan: str,
    wpisy: List[dict],
    zatwierdz: bool = False,
) -> EduZapisOcen:
    """Zapis ocen grupy (``wpisy`` = ``[{student, details, total_score, comment}]``).

    ``zatwierdz=True`` od razu zatwierdza wyniki (submit w Frappe); w przeciwnym
    razie zostają wersją roboczą do przejrzenia przez prowadzącego.
    """
    params: dict = {
        "group": grupa,
        "instructor": wykladowca,
        "assessment_plan": assessment_plan,
        "rows": wpisy,
        "submit": bool(zatwierdz),
    }
    result = _call("save_group_results", params)
    return EduZapisOcen(
        grupa=grupa,
        assessment_plan=result.get("assessment_plan") or assessment_plan,
        zapisane=result.get("saved") or [],
        count=result.get("count") or 0,
    )
