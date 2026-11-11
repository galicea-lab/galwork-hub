#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding: utf-8
"""
Dane studenta - na Frappe Education (REST).

Mapowanie pojęć:

  Dziekanat                              Frappe Education
  ------------------------------------  --------------------------------
  Osoba + Student                      Student
  nr albumu                            Student.album_number      
  Przynaleznosc (zapis na kierunek)    Program Enrollment
  KIERUNEK/TYP/RODZAJ                  Program Enrollment.program -> Program
  Kierunek/Typ - nazwy.                Program (program_name, program_abbreviation,
                                        study_mode, degree_type)         
  Lista_Semestrow (semestr w zapisie)  Student Academic Status           
  NR_SEMESTRU                          Student Academic Status.semester_number
  ZALICZONY/ZWAR/ZAMKNIETY/ZAPLACONY   Student Academic Status.status/is_paid/
                                        counts_towards_average            
  płatne/niepłatne                     Program Enrollment.is_tuition_based
  rok akademicki                       Academic Year (year_start_date)
  semestr letni/zimowy                 Academic Term.is_summer_term      


TODO: Braki / rzeczy obecnie niemożliwe do zrealizowania:

  * PESEL - nie dodajemy pola PESEL do Frappe Student -
    minimalizacja danych wrażliwych, RODO, więc `PESEL` jest zawsze ''.
  * "specjalność" (specjalizacja w ramach kierunku) - Frappe Education nie
    ma takiego pojęcia (Program jest płaski, bez pod-specjalizacji);
    `specjalnosc1`/`specjalnosc2` są zawsze `None`.
  * numer/data wydania dowodu osobistego, kraj wydania paszportu - nie
    dodano tych pól do Studenta (tylko `identity_card_number`/
    `passport_number`/`citizenship`) - zwracane jako ''.
"""

from __future__ import annotations

import datetime
import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector
from connector.frappe_education.util import ni, r_cyfra, rok_akademicki, rok_studiow

log = logging.getLogger("connector.frappe_education")

_STUDENT_FIELDS = [
    "name", "album_number", "first_name", "middle_name", "last_name",
    "student_email_id", "private_email", "student_mobile_number",
    "phone_home", "phone_work", "date_of_birth", "gender",
    "address_line_1", "address_line_2", "city", "country",
    "nip", "identity_card_number", "passport_number", "citizenship",
    "polon_student_uid", "polon_locality_type",
]


# ---------------------------------------------------------------------
# modele
# ---------------------------------------------------------------------

class EduStudia(BaseModel):
    k_skrot: str
    k_nazwa: str
    poziom: str  # 'pierwszy' / 'drugi' - przybliżenie z Program.degree_type
    rok_akademicki: str
    semestr: str  # 'letni' / 'zimowy'
    nr_semestru: int
    rok_studiow: int
    platne: bool
    zaliczony: bool
    zwar: bool
    zamkniety: bool
    zaplacony: bool


class EduStudentStudia(BaseModel):
    email: str
    nr_albumu: str
    nazwisko: str
    data_ur: str
    tel: str
    studia: List[EduStudia] = []


class EduStudent1(BaseModel):
    nr: str
    email: str
    imie_i: str
    imie_ii: str = ""
    nazwisko: str
    nr_semestru: int = 0


def _degree_poziom(degree_type: str | None) -> str:
    """'pierwszy'/'drugi' - przybliżenie polskich stopni kształcenia"""
    if degree_type in ("Bachelor", "Engineer"):
        return "pierwszy"
    return "drugi"


def _plec_from_gender(gender: str | None) -> int:
    """1 (kobieta) / 2 (mężczyzna).

    Frappe `Gender` to dowolny słownik (domyślnie Male/Female/Other/...) - dziedzictwo forka;
    Galwork działa zgodnie z biologią.
    """
    if gender and gender.strip().lower() == "female":
        return 1
    return 2


def _get_student(connector, *, email: str | None = None, nr_albumu: str | None = None) -> dict | None:
    if email:
        filters = [["student_email_id", "=", email]]
    elif nr_albumu:
        filters = [["album_number", "=", nr_albumu]]
    else:
        raise ConnectorError("student.py: _get_student: podaj email albo nr_albumu")
    rows = connector.get_list("Student", filters=filters, fields=_STUDENT_FIELDS, limit_page_length=1)
    return rows[0] if rows else None


def db_email2nr_albumu(email: str) -> str:
    try:
        connector = get_connector()
        student = _get_student(connector, email=email)
        return (student or {}).get("album_number") or ""
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("student.py: db_email2nr_albumu: %s", exc)
        raise ConnectorError("student.py: db_email2nr_albumu: %s" % exc) from exc


def db_get_student_studia(email: Optional[str], nr_albumu: Optional[str]) -> EduStudentStudia:
    """
    Uwaga wydajnościowa: Frappe REST nie robi joinów po stronie serwera, więc
    funkcja wykonuje kilka zapytań (Student -> Program Enrollment -> Program/
    Academic Year/Academic Term -> Student Academic Status), ale liczba
    zapytań nie rośnie z liczbą zapisów studenta (dociąganie "w paczkach"
    przez `name in [...]`).
    """
    try:
        connector = get_connector()
        student = _get_student(connector, email=email, nr_albumu=nr_albumu)
        if not student:
            return EduStudentStudia(email="", nr_albumu="", nazwisko="", data_ur="", tel="", studia=[])

        enrollments = connector.get_list(
            "Program Enrollment",
            filters=[["student", "=", student["name"]], ["docstatus", "!=", 2]],
            fields=["name", "program", "academic_year", "academic_term", "is_tuition_based"],
            limit_page_length=100,
        )

        programs = {}
        years = {}
        terms = {}
        if enrollments:
            program_names = sorted({e["program"] for e in enrollments if e.get("program")})
            year_names = sorted({e["academic_year"] for e in enrollments if e.get("academic_year")})
            term_names = sorted({e["academic_term"] for e in enrollments if e.get("academic_term")})
            if program_names:
                for p in connector.get_list(
                    "Program", filters=[["name", "in", program_names]],
                    fields=["name", "program_name", "program_abbreviation", "degree_type"],
                    limit_page_length=len(program_names),
                ):
                    programs[p["name"]] = p
            if year_names:
                for y in connector.get_list(
                    "Academic Year", filters=[["name", "in", year_names]],
                    fields=["name", "year_start_date"], limit_page_length=len(year_names),
                ):
                    years[y["name"]] = y
            if term_names:
                for t in connector.get_list(
                    "Academic Term", filters=[["name", "in", term_names]],
                    fields=["name", "is_summer_term"], limit_page_length=len(term_names),
                ):
                    terms[t["name"]] = t

        statuses_by_enrollment: dict[str, dict] = {}
        if enrollments:
            enrollment_names = [e["name"] for e in enrollments]
            status_rows = connector.get_list(
                "Student Academic Status",
                filters=[["program_enrollment", "in", enrollment_names]],
                fields=["program_enrollment", "semester_number", "status", "is_paid",
                        "counts_towards_average"],
                order_by="creation desc",
                limit_page_length=500,
            )
            for row in status_rows:
                # pierwszy (najnowszy, dzięki order_by desc) wpis per zapis = aktualny stan semestru
                statuses_by_enrollment.setdefault(row["program_enrollment"], row)

        result = EduStudentStudia(
            email=student.get("student_email_id") or "",
            nr_albumu=student.get("album_number") or student["name"],
            nazwisko=ni("", student.get("first_name"), student.get("middle_name"), student.get("last_name")),
            data_ur=str(student.get("date_of_birth") or "")[:10],
            tel=student.get("student_mobile_number") or "",
            studia=[],
        )
        for enr in enrollments:
            program = programs.get(enr.get("program"), {})
            year = years.get(enr.get("academic_year"), {})
            term = terms.get(enr.get("academic_term"), {})
            status = statuses_by_enrollment.get(enr["name"], {})
            nr_semestru = int(status.get("semester_number") or 0)
            status_value = status.get("status")
            year_start = year.get("year_start_date")
            rok = int(str(year_start)[:4]) if year_start else 0
            result.studia.append(EduStudia(
                k_skrot=program.get("program_abbreviation") or "",
                k_nazwa=program.get("program_name") or "",
                poziom=_degree_poziom(program.get("degree_type")),
                rok_akademicki=rok_akademicki(rok) if rok else "",
                semestr="letni" if term.get("is_summer_term") else "zimowy",
                nr_semestru=nr_semestru,
                rok_studiow=rok_studiow(nr_semestru) if nr_semestru else 0,
                platne=bool(enr.get("is_tuition_based")),
                zaliczony=(status_value == "Passed"),
                zwar=(status_value == "Conditional Pass"),
                zamkniety=(status_value in ("Closed", "Passed", "Failed")),
                zaplacony=bool(status.get("is_paid")),
            ))
        return result
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("student.py: db_get_student_studia: %s", exc)
        raise ConnectorError("student.py: db_get_student_studia: %s" % exc) from exc


def db_get_studenci_aktywni(
    rok: Optional[str] = None,
    letni: Optional[int] = None,
    kod_kierunku: Optional[str] = None,
    nr_semestru: Optional[int] = None,
) -> List[EduStudent1]:
    """
    "Aktywny" = `Program Enrollment` nieskasowany (`docstatus != 2`); 
    przyjęto zatwierdzony (`docstatus == 1`) zapis jako najbliższy odpowiednik
    "aktywnego" zapisu; nieprzesłane (`docstatus == 0`) wersje robocze są
    wykluczone tak jak skasowane.
    """
    try:
        connector = get_connector()
        filters: list = [["docstatus", "=", 1]]
        if kod_kierunku:
            programs = connector.get_list(
                "Program", filters=[["program_abbreviation", "=", kod_kierunku]],
                fields=["name"], limit_page_length=1,
            )
            if not programs:
                return []
            filters.append(["program", "=", programs[0]["name"]])
        if rok:
            try:
                rok_i = int(str(rok).split("/")[0])
            except ValueError:
                rok_i = None
            if rok_i:
                years = connector.get_list(
                    "Academic Year",
                    filters=[["year_start_date", "between", [f"{rok_i}-01-01", f"{rok_i}-12-31"]]],
                    fields=["name"], limit_page_length=5,
                )
                if not years:
                    return []
                filters.append(["academic_year", "in", [y["name"] for y in years]])
        if letni is not None:
            terms = connector.get_list(
                "Academic Term", filters=[["is_summer_term", "=", 1 if int(letni) else 0]],
                fields=["name"], limit_page_length=1000,
            )
            filters.append(["academic_term", "in", [t["name"] for t in terms]])

        enrollments = list(connector.get_all(
            "Program Enrollment", filters=filters,
            fields=["name", "student", "student_name"],
        ))
        if not enrollments:
            return []

        student_names = sorted({e["student"] for e in enrollments})
        students = {
            s["name"]: s
            for s in connector.get_list(
                "Student", filters=[["name", "in", student_names]],
                fields=["name", "album_number", "student_email_id", "first_name", "middle_name", "last_name"],
                limit_page_length=len(student_names),
            )
        }

        semester_by_enrollment: dict[str, int] = {}
        if nr_semestru is not None:
            status_rows = connector.get_list(
                "Student Academic Status",
                filters=[["program_enrollment", "in", [e["name"] for e in enrollments]],
                         ["semester_number", "=", nr_semestru]],
                fields=["program_enrollment", "semester_number"],
                limit_page_length=len(enrollments),
            )
            matching_enrollments = {r["program_enrollment"] for r in status_rows}
            enrollments = [e for e in enrollments if e["name"] in matching_enrollments]
            semester_by_enrollment = {r["program_enrollment"]: r["semester_number"] for r in status_rows}

        out: List[EduStudent1] = []
        for enr in enrollments:
            student = students.get(enr["student"])
            if not student:
                continue
            out.append(EduStudent1(
                nr=student.get("album_number") or student["name"],
                email=student.get("student_email_id") or "",
                imie_i=student.get("first_name") or "",
                imie_ii=student.get("middle_name") or "",
                nazwisko=student.get("last_name") or "",
                nr_semestru=semester_by_enrollment.get(enr["name"], 0),
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("student.py: db_get_studenci_aktywni: %s", exc)
        raise ConnectorError("student.py: db_get_studenci_aktywni: %s" % exc) from exc


def db_get_student_form(nr_albumu: str, kierunek: str) -> dict:
    """ dane do pism/zaświadczeń/decyzji. Zwraca `dict`, nie instancję pydantic.

    Pola niedostępne we Frappe Education (zob. docstring modułu) są zwracane
    jako '' / None .
    """
    try:
        connector = get_connector()
        student = _get_student(connector, nr_albumu=nr_albumu)
        if not student:
            raise ConnectorError(
                f"student.py: db_get_student_form: brak studenta o nr_albumu={nr_albumu!r}"
            )
        programs = connector.get_list(
            "Program", filters=[["program_abbreviation", "=", kierunek]],
            fields=["name", "program_name", "program_abbreviation", "degree_type", "study_mode"],
            limit_page_length=1,
        )
        if not programs:
            raise ConnectorError(f"student.py: db_get_student_form: nieznany kierunek={kierunek!r}")
        program = programs[0]

        enrollments = connector.get_list(
            "Program Enrollment",
            filters=[["student", "=", student["name"]], ["program", "=", program["name"]],
                     ["docstatus", "!=", 2]],
            fields=["name", "academic_year", "academic_term"],
            order_by="enrollment_date desc",
            limit_page_length=1,
        )
        academic_year_row, academic_term_row = {}, {}
        semester_number = 0
        if enrollments:
            enr = enrollments[0]
            if enr.get("academic_year"):
                rows = connector.get_list(
                    "Academic Year", filters=[["name", "=", enr["academic_year"]]],
                    fields=["name", "year_start_date"], limit_page_length=1,
                )
                academic_year_row = rows[0] if rows else {}
            if enr.get("academic_term"):
                rows = connector.get_list(
                    "Academic Term", filters=[["name", "=", enr["academic_term"]]],
                    fields=["name", "is_summer_term"], limit_page_length=1,
                )
                academic_term_row = rows[0] if rows else {}
            status_rows = connector.get_list(
                "Student Academic Status",
                filters=[["program_enrollment", "=", enr["name"]]],
                fields=["semester_number"], order_by="creation desc", limit_page_length=1,
            )
            if status_rows:
                semester_number = int(status_rows[0].get("semester_number") or 0)

        # maksymalny semester_number w planie studiów (Program Course) =
        # liczba semestrów programu
        # Uwaga: "Program Course" jest tabelą potomną (child table, bez
        # własnych `permissions` w doctype) - Frappe REST nie pozwala jej
        # wylistować bezpośrednio przez `/api/resource/Program Course`
        # (poza Administratorem), trzeba doczytać ją jako część dokumentu
        # rodzica (`/api/resource/Program/<name>`).
        program_doc = connector.get_doc("Program", program["name"])
        program_courses = program_doc.get("courses") or []
        ile_semestrow = max([pc.get("semester_number") or 0 for pc in program_courses], default=0)

        plec = _plec_from_gender(student.get("gender"))
        rok_start = int(str(academic_year_row.get("year_start_date") or "")[:4] or 0)
        poziom_studiow = 1 if _degree_poziom(program.get("degree_type")) == "pierwszy" else 2

        zostalo = (ile_semestrow - semester_number + 1) if ile_semestrow else 0
        today = datetime.date.today()
        lata = ((zostalo * 6) / 12).__floor__() if zostalo else 0
        if today.month >= 10:
            if zostalo and lata * 2 == zostalo:
                koniec = datetime.date(today.year + lata, 9, 30)
            else:
                koniec = datetime.date(today.year + lata + 1, 3, 31)
        else:
            if lata > 0 and zostalo and lata * 2 != zostalo:
                koniec = datetime.date(today.year + lata + 1, 3, 31)
            else:
                koniec = datetime.date(today.year + lata, 9, 30)

        form = {
            "adres": ", ".join(filter(None, [student.get("address_line_1"), student.get("address_line_2")])),
            "miejscowosc": student.get("city") or "",
            "kraj": student.get("country") or "",
            "nr_dowod": student.get("identity_card_number") or student.get("passport_number") or "",
            "dowod_wyd": "",  # NIEDOSTĘPNE - zob. docstring modułu/integracja.md
            "dowod_wazn": "",  # NIEDOSTĘPNE
            "dowod_kraj": "",  # NIEDOSTĘPNE
            "pan_i": "Pani" if plec == 1 else "Pan",
            "student_ka": "studentka" if plec == 1 else "student",
            "studentem_ka": "studentką" if plec == 1 else "studentem",
            "urodzony_a": "urodzona" if plec == 1 else "urodzony",
            "szanowny_a": "Szanowna Pani" if plec == 1 else "Szanowny Panie",
            "zlozylem_am": "Złożyłam" if plec == 1 else "Złożyłem",
            "zostalem_am": "zostałam" if plec == 1 else "zostałem",
            "skreslony_a": "skreślona" if plec == 1 else "skreślony",
            "data_urodzenia": str(student.get("date_of_birth") or ""),
            "nr_semestru": semester_number,
            "semestr": "letni" if academic_term_row.get("is_summer_term") else "zimowy",
            "kierunek": program.get("program_name") or "",
            "stopien": "II" if poziom_studiow == 2 else "I",
            "s_nazwa": program.get("degree_type") or "",
            "s_skrot": (program.get("degree_type") or "")[:1],
            "rok_akademicki0": rok_start,
            "rok_akademicki": rok_akademicki(rok_start) if rok_start else "",
            "telefon_kom": student.get("student_mobile_number") or "",
            "email": student.get("student_email_id") or "",
            "plec": plec,
            "specjalnosc1": None,  # NIEDOSTĘPNE - Frappe Education nie ma specjalizacji
            "specjalnosc2": None,
            "tryb": "studia stacjonarne" if program.get("study_mode") == "Full-Time" else "studia niestacjonarne",
            "ile_semestrow": ile_semestrow,
            "rok_studiow": rok_studiow(semester_number) if semester_number else 0,
            "data_konca": koniec.strftime("%Y-%m-%d"),
            "poziom_studiow": poziom_studiow,
        }
        form["r_rok_studiow"] = r_cyfra(form["rok_studiow"])
        form["r_semestr_studiow"] = r_cyfra(semester_number)
        form["r_poziom_studiow"] = r_cyfra(poziom_studiow)
        return form
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("student.py: db_get_student_form: %s", exc)
        raise ConnectorError("student.py: db_get_student_form: %s" % exc) from exc
