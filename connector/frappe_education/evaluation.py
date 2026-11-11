#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding: utf-8
# 2026-10-05
"""Karty Okresowych Osiągnięć Studenta (KOOS) i statusy semestralne na Frappe Education.

2026-11-08: karta semestru pochodzi z ``education.portal.api.get_semester_card``
(``education/portal/semester_card.py`` w aplikacji Education): przedmioty
semestru z planu studiów, forma zajęć i zaliczenia, ECTS, podejścia z terminem
(I/II/poprawka/komis wg ``Assessment Group``), wykładowca z tytułem, przedmioty
dodatkowe i sumy ECTS. Zaliczenie i średnie liczy Frappe (skala ocen + próg
``portal.passing_percentage``), nie hub.

Statusy: z doctype `Student Academic Status`
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector
from connector.frappe_education.oceny import EduSemestrStatus

log = logging.getLogger("connector.frappe_education")

_METHOD = "education.portal.api.get_semester_card"


class StatusEntry(BaseModel):
    id: str  # Student Academic Status.name
    status: str
    data: Optional[str] = None
    powod: str = ""


class StudentStatusHistory(BaseModel):
    nr: str
    rok: Optional[int] = None
    kierunek: str
    nr_semestru: Optional[int] = None
    statusy: List[StatusEntry] = []


# ----------------------------------------------------------------------
# 2026-11-08: Karta Okresowych Osiągnięć (get_semester_card)
# ----------------------------------------------------------------------
class EduWykladowca(BaseModel):
    """Wykładowca przedmiotu (egzaminator albo prowadzący grupę)."""

    id: str = ""
    nazwa: str = ""
    tytul: Optional[str] = None
    opis: str = ""  # tytuł + imię i nazwisko, gotowe do wydruku


class EduPodejscieKOOS(BaseModel):
    """Jedno podejście do zaliczenia (Assessment Result)."""

    nr: int = 0
    termin: str = ""  # etykieta terminu (I, II, K...) albo numer podejścia
    grupa_ocen: Optional[str] = None  # Assessment Group
    id: str = ""
    plan: Optional[str] = None
    data: Optional[str] = None
    punkty: float = 0
    maksimum: float = 0
    procent: Optional[float] = None
    ocena: Optional[str] = None
    zaliczone: Optional[bool] = None
    egzaminator: Optional[str] = None


class EduPrzedmiotKOOS(BaseModel):
    """Przedmiot na karcie semestru."""

    kod: str = ""
    nazwa: str = ""
    forma: Optional[str] = None  # Course.class_type (Lecture, Laboratory...)
    forma_zal: Optional[str] = None  # Course.passing_type (Graded, Pass-Fail)
    ects: float = 0
    semestr_planu: Optional[int] = None
    obowiazkowy: Optional[bool] = None
    kategoria: str = "program"  # program | repeat | program_difference | other_semester
    dodatkowy: bool = False
    wykladowca: Optional[EduWykladowca] = None
    wykladowcy: List[EduWykladowca] = []
    podejscia: List[EduPodejscieKOOS] = []
    ocena: Optional[str] = None
    zaliczony: Optional[bool] = None  # None = brak oceny


class EduSumyKOOS(BaseModel):
    przedmioty: int = 0
    zaliczone: int = 0
    niezaliczone: int = 0
    bez_oceny: int = 0
    ects_planowe: float = 0
    ects_uzyskane: float = 0
    ects_dodatkowe: float = 0
    ects_dodatkowe_uzyskane: float = 0
    srednia_procent: Optional[float] = None
    srednia_ocen: Optional[float] = None


class EduKartaOkresowa(BaseModel):
    """Karta Okresowych Osiągnięć Studenta - jeden semestr jednego zapisu."""

    student: str = ""  # Student.name
    nr_albumu: str = ""
    imie: str = ""
    nazwisko: str = ""
    kierunek: str = ""  # Program.program_name
    kierunek_id: str = ""  # Program.name
    k_skrot: str = ""
    stopien: Optional[str] = None  # Program.degree_type
    tryb: Optional[str] = None  # Program.study_mode
    zapis: str = ""  # Program Enrollment
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None  # Academic Term.name
    semestr_nazwa: Optional[str] = None
    nr_semestru: Optional[int] = None
    rok_studiow: Optional[int] = None
    rok_studiow_rzymski: Optional[str] = None
    na_semestr: Optional[int] = None
    status_semestru: Optional[EduSemestrStatus] = None
    prog_zaliczenia: float = 50
    data: Optional[str] = None
    przedmioty: List[EduPrzedmiotKOOS] = []
    sumy: EduSumyKOOS = EduSumyKOOS()


def _wykladowca(row: dict | None) -> Optional[EduWykladowca]:
    if not row:
        return None
    return EduWykladowca(id=row.get("instructor") or "", nazwa=row.get("name") or "",
                         tytul=row.get("title"), opis=row.get("display") or "")


def _podejscie(row: dict) -> EduPodejscieKOOS:
    return EduPodejscieKOOS(
        nr=row.get("number") or 0, termin=row.get("label") or "",
        grupa_ocen=row.get("assessment_group"), id=row.get("assessment_result") or "",
        plan=row.get("assessment_plan"), data=row.get("date"),
        punkty=row.get("score") or 0, maksimum=row.get("maximum") or 0,
        procent=row.get("percentage"), ocena=row.get("grade"),
        zaliczone=row.get("passed"), egzaminator=row.get("examiner"),
    )


def _przedmiot(row: dict) -> EduPrzedmiotKOOS:
    return EduPrzedmiotKOOS(
        kod=row.get("code") or "", nazwa=row.get("name") or "",
        forma=row.get("class_type"), forma_zal=row.get("passing_type"),
        ects=row.get("ects") or 0, semestr_planu=row.get("plan_semester"),
        obowiazkowy=row.get("required"), kategoria=row.get("category") or "program",
        dodatkowy=bool(row.get("additional")),
        wykladowca=_wykladowca(row.get("lecturer")),
        wykladowcy=[w for w in (_wykladowca(r) for r in row.get("lecturers") or []) if w],
        podejscia=[_podejscie(a) for a in row.get("attempts") or []],
        ocena=row.get("grade"), zaliczony=row.get("passed"),
    )


def karta_z_portalu(result: dict) -> EduKartaOkresowa:
    """Odpowiedź ``get_semester_card`` -> model huba."""

    student = result.get("student") or {}
    program = result.get("program") or {}
    term = result.get("academic_term") or {}
    status = result.get("semester_status")
    totals = result.get("totals") or {}
    return EduKartaOkresowa(
        student=student.get("name") or "",
        nr_albumu=student.get("album_number") or "",
        imie=student.get("first_name") or "",
        nazwisko=student.get("last_name") or "",
        kierunek=program.get("program_name") or "",
        kierunek_id=program.get("name") or "",
        k_skrot=program.get("program_abbreviation") or "",
        stopien=program.get("degree_type"),
        tryb=program.get("study_mode"),
        zapis=result.get("program_enrollment") or "",
        rok_akademicki=result.get("academic_year"),
        semestr=term.get("name"),
        semestr_nazwa=term.get("term_name"),
        nr_semestru=result.get("semester_number"),
        rok_studiow=result.get("year_of_study"),
        rok_studiow_rzymski=result.get("year_of_study_roman"),
        na_semestr=result.get("next_semester_number"),
        status_semestru=EduSemestrStatus(
            id=status.get("name") or "", zapis=result.get("program_enrollment"),
            rok_akademicki=status.get("academic_year"), semestr=status.get("academic_term"),
            nr_semestru=status.get("semester_number"), status=status.get("status"),
            letni=bool(status.get("is_summer_semester")),
            do_sredniej=bool(status.get("counts_towards_average")),
            zaplacony=bool(status.get("is_paid")), data_decyzji=status.get("decision_date"),
        ) if status else None,
        prog_zaliczenia=result.get("passing_percentage") or 50,
        data=result.get("generated_on"),
        przedmioty=[_przedmiot(row) for row in result.get("courses") or []],
        sumy=EduSumyKOOS(
            przedmioty=totals.get("courses") or 0,
            zaliczone=totals.get("passed") or 0,
            niezaliczone=totals.get("failed") or 0,
            bez_oceny=totals.get("not_graded") or 0,
            ects_planowe=totals.get("ects_planned") or 0,
            ects_uzyskane=totals.get("ects_earned") or 0,
            ects_dodatkowe=totals.get("ects_additional") or 0,
            ects_dodatkowe_uzyskane=totals.get("ects_additional_earned") or 0,
            srednia_procent=totals.get("average_percentage"),
            srednia_ocen=totals.get("average_grade"),
        ),
    )


def db_get_karta_okresowa(nr_albumu: str, semestr: Optional[str] = None,
                          kierunek: Optional[str] = None) -> EduKartaOkresowa:
    """KOOS studenta. ``semestr`` = Academic Term.name (domyślnie ostatni
    rozliczony), ``kierunek`` = Program.name (domyślnie ostatni zapis)."""

    params: dict = {"student": nr_albumu}
    if semestr:
        params["academic_term"] = semestr
    if kierunek:
        params["program"] = kierunek
    try:
        result = get_connector().call_method(_METHOD, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("evaluation.py: get_semester_card: %s", exc)
        raise ConnectorError("evaluation.py: get_semester_card: %s" % exc) from exc
    return karta_z_portalu(result or {})


class EduKarty:
    """KOOS."""

    def __init__(self, rok: int, letni: int, tryb: str = "Full-Time"):
        self.rok = rok
        self.letni = 1 if letni else 0
        self.tryb = tryb
        self.rok_akademicki = f"{rok}/{rok + 1}"

    # ------------------------------------------------------------------
    def _program(self, connector, kierunek: str) -> dict | None:
        rows = connector.get_list(
            "Program", filters=[["program_abbreviation", "=", kierunek]],
            fields=["name", "program_name", "program_abbreviation"], limit_page_length=1,
        )
        return rows[0] if rows else None

    def _student(self, connector, nr_albumu: str) -> dict | None:
        rows = connector.get_list(
            "Student", filters=[["album_number", "=", nr_albumu]],
            fields=["name", "album_number", "student_email_id", "first_name", "last_name"],
            limit_page_length=1,
        )
        return rows[0] if rows else None

    def _enrollment(self, connector, program_name: str, student_name: str) -> dict | None:
        rows = connector.get_list(
            "Program Enrollment",
            filters=[["program", "=", program_name], ["student", "=", student_name],
                     ["docstatus", "!=", 2]],
            fields=["name"], order_by="enrollment_date desc", limit_page_length=1,
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    def lista_statusow(self, kierunek: str) -> List[StudentStatusHistory]:
        """Odpowiednik `historia statusów wszystkich studentów danego programu."""
        try:
            connector = get_connector()
            program = self._program(connector, kierunek)
            if not program:
                return []
            enrollments = list(connector.get_all(
                "Program Enrollment",
                filters=[["program", "=", program["name"]], ["docstatus", "!=", 2]],
                fields=["name", "student"],
            ))
            if not enrollments:
                return []
            by_enrollment = {e["name"]: e for e in enrollments}
            students = {
                s["name"]: s
                for s in connector.get_list(
                    "Student", filters=[["name", "in", [e["student"] for e in enrollments]]],
                    fields=["name", "album_number"], limit_page_length=len(enrollments),
                )
            }
            status_rows = list(connector.get_all(
                "Student Academic Status",
                filters=[["program_enrollment", "in", list(by_enrollment.keys())]],
                fields=["name", "program_enrollment", "semester_number", "status",
                        "decision_date", "reason"],
                order_by="creation asc",
            ))
            history: dict[str, StudentStatusHistory] = {}
            for row in status_rows:
                enr = by_enrollment.get(row["program_enrollment"])
                if not enr:
                    continue
                student = students.get(enr["student"], {})
                nr = student.get("album_number") or enr["student"]
                entry = history.get(nr)
                if entry is None:
                    entry = StudentStatusHistory(
                        nr=nr, rok=self.rok, kierunek=kierunek,
                        nr_semestru=row.get("semester_number"), statusy=[],
                    )
                    history[nr] = entry
                entry.statusy.append(StatusEntry(
                    id=row["name"], status=row.get("status") or "",
                    data=row.get("decision_date"), powod=row.get("reason") or "",
                ))
            return list(history.values())
        except ConnectorError:
            raise
        except Exception as exc:
            log.error("evaluation.py: lista_statusow: %s", exc)
            raise ConnectorError("evaluation.py: lista_statusow: %s" % exc) from exc

    def statusy(self, kierunek: str, nr_albumu: str) -> List[StatusEntry]:
        """statusy studenta w BIEŻĄCYM (self.rok/self.letni) semestrze."""
        try:
            connector = get_connector()
            program = self._program(connector, kierunek)
            student = self._student(connector, nr_albumu)
            if not program or not student:
                return []
            enrollment = self._enrollment(connector, program["name"], student["name"])
            if not enrollment:
                return []
            rows = connector.get_list(
                "Student Academic Status",
                filters=[["program_enrollment", "=", enrollment["name"]],
                         ["is_summer_semester", "=", self.letni]],
                fields=["name", "status", "decision_date", "reason"],
                order_by="creation desc", limit_page_length=20,
            )
            return [
                StatusEntry(id=r["name"], status=r.get("status") or "",
                            data=r.get("decision_date"), powod=r.get("reason") or "")
                for r in rows
            ]
        except ConnectorError:
            raise
        except Exception as exc:
            log.error("evaluation.py: statusy: %s", exc)
            raise ConnectorError("evaluation.py: statusy: %s" % exc) from exc

    def nastepny_status(self, kierunek: str, nr_albumu: str) -> List[StatusEntry]:
        """ status w semestrze NASTĘPUJĄCYM po (self.rok, self.letni)."""
        try:
            connector = get_connector()
            program = self._program(connector, kierunek)
            student = self._student(connector, nr_albumu)
            if not program or not student:
                return []
            enrollment = self._enrollment(connector, program["name"], student["name"])
            if not enrollment:
                return []
            next_letni = 0 if self.letni else 1
            next_rok = self.rok if self.letni == 0 else self.rok + 1
            rows = connector.get_list(
                "Student Academic Status",
                filters=[["program_enrollment", "=", enrollment["name"]],
                         ["is_summer_semester", "=", next_letni]],
                fields=["name", "status", "decision_date", "reason", "registration_date"],
                order_by="creation desc", limit_page_length=20,
            )
            # przybliżenie roku: filtrowanie po dacie rejestracji w next_rok,
            # jeśli jest ustawiona
            filtered = [r for r in rows if not r.get("registration_date")
                        or str(r["registration_date"]).startswith(str(next_rok))]
            return [
                StatusEntry(id=r["name"], status=r.get("status") or "",
                            data=r.get("decision_date"), powod=r.get("reason") or "")
                for r in (filtered or rows)
            ]
        except ConnectorError:
            raise
        except Exception as exc:
            log.error("evaluation.py: nastepny_status: %s", exc)
            raise ConnectorError("evaluation.py: nastepny_status: %s" % exc) from exc

    def data_decyzji_skreslenia(self, kierunek: str, nr_albumu: str, domyslnie: str) -> str:
        """zob. ograniczenie dot. (nie)prawomocności w docstringu modułu."""
        try:
            lista = self.statusy(kierunek, nr_albumu)
            for status in lista:
                if status.status == "Suspended" and status.data:
                    return str(status.data)[:10]
            return domyslnie
        except ConnectorError:
            raise
        except Exception as exc:
            log.error("evaluation.py: data_decyzji_skreslenia: %s", exc)
            raise ConnectorError("evaluation.py: data_decyzji_skreslenia: %s" % exc) from exc

    # ------------------------------------------------------------------
    def _term(self, connector) -> dict | None:
        """Academic Term dla (self.rok, self.letni)."""
        rows = connector.get_list(
            "Academic Term",
            filters=[["is_summer_term", "=", self.letni]],
            fields=["name", "academic_year"], limit_page_length=50,
        )
        rows = [r for r in rows if str(r.get("academic_year", "")).startswith(str(self.rok))]
        return rows[0] if rows else None

    def db_get_student(self, kierunek_skrot: str, nr_albumu: str) -> dict:
        """2026-11-08: karta semestru (self.rok, self.letni) z
        ``get_semester_card`` jako słownik (``EduKartaOkresowa``); ``{}`` gdy
        nie ma programu, semestru albo zapisu studenta."""
        try:
            connector = get_connector()
            program = self._program(connector, kierunek_skrot)
            term = self._term(connector)
            if not program or not term:
                return {}
            karta = db_get_karta_okresowa(nr_albumu, term["name"], program["name"])
            return karta.model_dump()
        except ConnectorError as exc:
            # brak studenta / zapisu / semestru: Frappe zwraca 404 DoesNotExistError
            if " -> 404" in str(exc) or "DoesNotExistError" in str(exc):
                return {}
            raise
        except Exception as exc:
            log.error("evaluation.py: db_get_student: %s", exc)
            raise ConnectorError("evaluation.py: db_get_student: %s" % exc) from exc
