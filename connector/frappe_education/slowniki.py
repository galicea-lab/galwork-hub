#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding: utf-8
"""Słowniki:.
  RokAkademicki                 -> Academic Year + jego Academic Term-y
                                    (potrzebne pola `is_summer_term` i
                                    `exam_session_start/end_date` dodane do
                                    Academic Term specjalnie pod tę
                                    integrację - zob. integracja.md)
  Kierunek/Typ/                 -> Program (program_name,
     (lista kierunków)             program_abbreviation, degree_type)
  JednostkaOrgan                -> Department (drzewo jednostek) - ale
      (instytuty/jednostki)        Department NIE JEST doctype'em modułu
                                    Education
  dziekanaty                    -> jak wyżej (Department) - bez pól
      (tel/www dziekanatu)          `tel`/`www` (patrz niżej)
  Miejscowosc (słownik          -> NIEDOSTĘPNE (patrz `db_get_localities`)
      miejscowości PL/TERYT)

"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")


class EduAcademicYear(BaseModel):
    rok: int
    rok_akademicki: str
    pocz_zim: Optional[str] = None
    kon_zim: Optional[str] = None
    pocz_let: Optional[str] = None
    kon_let: Optional[str] = None
    pocz_ses_zim: Optional[str] = None
    kon_ses_zim: Optional[str] = None
    pocz_ses_let: Optional[str] = None
    kon_ses_let: Optional[str] = None


class EduFieldOfStudy(BaseModel):
    kierunek: str
    k_skrot: str
    stopien: int
    s_nazwa: str
    s_skrot: str


class EduDeanOffice(BaseModel):
    skrot: str
    nazwa: str
    tel: str = ""
    www: str = ""
    id: str


class EduInstitute(BaseModel):
    id: str
    nazwa: str
    skrot: str


def db_get_academic_years() -> List[EduAcademicYear]:
    """ Zakresy dat (semestr zimowy, letni, sesja zimowa, sesja letnia) we Frappe są zwykle 
    w dwóch rekordach `Academic Term` danego `Academic Year`
    (is_summer_term=0/1) - stąd dla każdego roku robione jest tu dodatkowe
    zapytanie o jego semestry.
    """
    try:
        connector = get_connector()
        years = connector.get_list(
            "Academic Year", fields=["name", "academic_year_name", "year_start_date"],
            order_by="year_start_date desc", limit_page_length=100,
        )
        out: List[EduAcademicYear] = []
        for year in years:
            start = year.get("year_start_date")
            if not start:
                continue
            rok = int(str(start)[:4])
            terms = connector.get_list(
                "Academic Term",
                filters=[["academic_year", "=", year["name"]]],
                fields=["is_summer_term", "term_start_date", "term_end_date",
                        "exam_session_start_date", "exam_session_end_date"],
                limit_page_length=10,
            )
            zimowy = next((t for t in terms if not t.get("is_summer_term")), {})
            letni = next((t for t in terms if t.get("is_summer_term")), {})
            out.append(EduAcademicYear(
                rok=rok,
                rok_akademicki=year.get("academic_year_name") or f"{rok}/{rok + 1}",
                pocz_zim=zimowy.get("term_start_date"),
                kon_zim=zimowy.get("term_end_date"),
                pocz_let=letni.get("term_start_date"),
                kon_let=letni.get("term_end_date"),
                pocz_ses_zim=zimowy.get("exam_session_start_date"),
                kon_ses_zim=zimowy.get("exam_session_end_date"),
                pocz_ses_let=letni.get("exam_session_start_date"),
                kon_ses_let=letni.get("exam_session_end_date"),
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("slowniki.py: db_get_academic_years: %s", exc)
        raise ConnectorError("slowniki.py: db_get_academic_years: %s" % exc) from exc


def db_get_fields_of_study() -> List[EduFieldOfStudy]:
    try:
        connector = get_connector()
        programs = connector.get_list(
            "Program",
            fields=["program_name", "program_abbreviation", "degree_type"],
            order_by="program_name asc",
            limit_page_length=500,
        )
        out = []
        for p in programs:
            degree = p.get("degree_type") or ""
            stopien = 1 if degree in ("Bachelor", "Engineer") else 2
            out.append(EduFieldOfStudy(
                kierunek=p.get("program_name") or "",
                k_skrot=p.get("program_abbreviation") or "",
                stopien=stopien,
                s_nazwa=degree,
                s_skrot=degree[:1] if degree else "",
            ))
        return out
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("slowniki.py: db_get_fields_of_study: %s", exc)
        raise ConnectorError("slowniki.py: db_get_fields_of_study: %s" % exc) from exc


def db_get_dean_offices() -> List[EduDeanOffice]:
    """ brak `tel`/`www` """
    try:
        connector = get_connector()
        departments = connector.get_list(
            "Department", fields=["name"], limit_page_length=200,
        )
        return [
            EduDeanOffice(skrot=d["name"], nazwa=d["name"], tel="", www="", id=d["name"])
            for d in departments
        ]
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("slowniki.py: db_get_dean_offices: %s", exc)
        raise ConnectorError("slowniki.py: db_get_dean_offices: %s" % exc) from exc


def db_get_institutes() -> List[EduInstitute]:
    try:
        connector = get_connector()
        departments = connector.get_list(
            "Department", fields=["name"], limit_page_length=200,
        )
        return [EduInstitute(id=d["name"], nazwa=d["name"], skrot=d["name"]) for d in departments]
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("slowniki.py: db_get_institutes: %s", exc)
        raise ConnectorError("slowniki.py: db_get_institutes: %s" % exc) from exc


def db_get_localities(fragment: str):
    """NIE ZREALIZOWANE - Frappe Education nie modeluje polskiego słownika
    miejscowości/TERYT - To nie jest dziedzina systemu
    dziekanatowego, a słownik administracyjny GUS.
    TODO:
      1. zaimportować słownik TERYT (https://eteryt.stat.gov.pl/) do
         osobnego doctype'u w `education` (albo osobnej tabeli w nodavia-hub),
      2. albo wołać zewnętrzne API (np. GUS BDL / TERYT WebAPI) na żywo.
    Żadna z tych opcji nie jest częścią modułu Frappe Education - stąd
    jawny `ConnectorError` (zgodnie z zasadą "nie połykamy błędu"), a nie cicho zwrócona pusta lista.
    """
    raise ConnectorError(
        "slowniki.py: db_get_localities: Frappe Education nie ma słownika "
        "miejscowości/TERYT - wymaga osobnej integracji z danymi GUS/TERYT "
        "(poza zakresem modułu Education); fragment=%r" % fragment
    )
