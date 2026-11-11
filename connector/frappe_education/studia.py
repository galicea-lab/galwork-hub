#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 2026-11-08: dane studenta, zapisy na kierunek i statusy semestrów przez API
# portalowe education (education.portal.api.get_studies) - ekran "Moje studia".
"""Profil studenta i przebieg studiów - API portalowe Frappe Education.

Numer dowodu i paszportu przychodzą z Frappe zamaskowane (ostatnie 4 znaki).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class DaneStudenta(BaseModel):
    """Dane osobowe studenta (bez pełnych numerów dokumentów)."""

    student: str = ""
    imie: Optional[str] = None
    drugie_imie: Optional[str] = None
    nazwisko: Optional[str] = None
    nazwisko_pelne: Optional[str] = None
    nr_albumu: Optional[str] = None
    email: Optional[str] = None
    email_prywatny: Optional[str] = None
    telefon: Optional[str] = None
    data_urodzenia: Optional[str] = None
    plec: Optional[str] = None
    obywatelstwo: Optional[str] = None
    narodowosc: Optional[str] = None
    kraj: Optional[str] = None
    miasto: Optional[str] = None
    adres: Optional[str] = None
    nr_dowodu: Optional[str] = None  # zamaskowany
    nr_paszportu: Optional[str] = None  # zamaskowany
    zdjecie: Optional[str] = None
    data_przyjecia: Optional[str] = None


class ZapisNaKierunek(BaseModel):
    """Zapis na kierunek (Program Enrollment)."""

    id: str = ""
    kierunek: Optional[str] = None
    kierunek_nazwa: Optional[str] = None
    kierunek_skrot: Optional[str] = None
    stopien: Optional[str] = None
    tryb: Optional[str] = None
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None
    data_zapisu: Optional[str] = None
    grupa: Optional[str] = None
    kategoria: Optional[str] = None
    platne: bool = False


class StatusSemestru(BaseModel):
    """Rozliczenie semestru (Student Academic Status)."""

    id: str = ""
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None
    nr_semestru: Optional[int] = None
    status: Optional[str] = None
    letni: bool = False
    do_sredniej: bool = False
    zaplacony: bool = False
    data_decyzji: Optional[str] = None
    data_rejestracji: Optional[str] = None


class MojeStudia(BaseModel):
    """Profil i przebieg studiów studenta."""

    student: DaneStudenta
    zapisy: List[ZapisNaKierunek] = []
    semestry: List[StatusSemestru] = []
    aktualny_zapis: Optional[ZapisNaKierunek] = None
    aktualny_semestr: Optional[StatusSemestru] = None
    ustawienia: Dict[str, Optional[str]] = {}


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("studia.py: %s: %s", method, exc)
        raise ConnectorError("studia.py: %s: %s" % (method, exc)) from exc


def _dane(row: dict) -> DaneStudenta:
    return DaneStudenta(
        student=row.get("name") or "",
        imie=row.get("first_name"),
        drugie_imie=row.get("middle_name"),
        nazwisko=row.get("last_name"),
        nazwisko_pelne=row.get("student_name"),
        nr_albumu=row.get("album_number"),
        email=row.get("student_email_id"),
        email_prywatny=row.get("private_email"),
        telefon=row.get("student_mobile_number"),
        data_urodzenia=row.get("date_of_birth"),
        plec=row.get("gender"),
        obywatelstwo=row.get("citizenship"),
        narodowosc=row.get("nationality"),
        kraj=row.get("country"),
        miasto=row.get("city"),
        adres=row.get("address"),
        nr_dowodu=row.get("identity_card_number"),
        nr_paszportu=row.get("passport_number"),
        zdjecie=row.get("image"),
        data_przyjecia=row.get("joining_date"),
    )


def _zapis(row: dict) -> ZapisNaKierunek:
    return ZapisNaKierunek(
        id=row.get("name") or "",
        kierunek=row.get("program"),
        kierunek_nazwa=row.get("program_name"),
        kierunek_skrot=row.get("program_abbreviation"),
        stopien=row.get("degree_type"),
        tryb=row.get("study_mode"),
        rok_akademicki=row.get("academic_year"),
        semestr=row.get("academic_term"),
        data_zapisu=row.get("enrollment_date"),
        grupa=row.get("student_batch"),
        kategoria=row.get("student_category"),
        platne=bool(row.get("is_tuition_based")),
    )


def _semestr(row: dict) -> StatusSemestru:
    return StatusSemestru(
        id=row.get("name") or "",
        rok_akademicki=row.get("academic_year"),
        semestr=row.get("academic_term"),
        nr_semestru=row.get("semester_number"),
        status=row.get("status"),
        letni=bool(row.get("is_summer_semester")),
        do_sredniej=bool(row.get("counts_towards_average")),
        zaplacony=bool(row.get("is_paid")),
        data_decyzji=row.get("decision_date"),
        data_rejestracji=row.get("registration_date"),
    )


def db_get_moje_studia(nr_albumu: str) -> MojeStudia:
    """Profil studenta, zapisy na kierunek i statusy semestrów."""
    result = _call("get_studies", {"student": nr_albumu})
    zapisy = [_zapis(row) for row in result.get("enrollments") or []]
    semestry = [_semestr(row) for row in result.get("semesters") or []]
    aktualny_zapis = result.get("current_enrollment")
    aktualny_semestr = result.get("current_semester")
    return MojeStudia(
        student=_dane(result.get("student") or {}),
        zapisy=zapisy,
        semestry=semestry,
        aktualny_zapis=_zapis(aktualny_zapis) if aktualny_zapis else None,
        aktualny_semestr=_semestr(aktualny_semestr) if aktualny_semestr else None,
        ustawienia=result.get("settings") or {},
    )
