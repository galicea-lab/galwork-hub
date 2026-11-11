# coding: utf-8
# Tożsamość osoby w Education (student, wykładowca) - na jej
# podstawie serwis studencki proponuje przełączanie roli. Dane:
# education.portal.api.get_identity (education/portal/api.py).
"""Kim jest osoba zalogowana w Education - studentem, wykładowcą, oboma.

Hub uwierzytelnia użytkownika w Keycloak i zna tylko jego e-mail, dlatego
`get_identity` jest wołane z jawnym `email`. Frappe odpowiada rekordem
`Student` i `Instructor` (albo niczym), a hub dokłada do tego role z Keycloak
(dziekanat, administrator) - te dwie informacje razem dają listę dostępnych
ról w interfejsie.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class StudentTozsamosc(BaseModel):
    """Student: numer albumu i dane do nagłówka ekranu."""

    student: str = ""
    nr_albumu: Optional[str] = None
    imie: Optional[str] = None
    drugie_imie: Optional[str] = None
    nazwisko: Optional[str] = None
    imie_nazwisko: Optional[str] = None
    email: Optional[str] = None
    zdjecie: Optional[str] = None


class WykladowcaTozsamosc(BaseModel):
    """Wykładowca: nazwa rekordu `Instructor`, wydział i e-mail do logowania."""

    instructor: str = ""
    imie_nazwisko: Optional[str] = None
    wydzial: Optional[str] = None
    email: Optional[str] = None
    zdjecie: Optional[str] = None


class EduTozsamosc(BaseModel):
    """Odpowiedź `get_identity`: student i/lub wykładowca (None, gdy nie dotyczy)."""

    email: str = ""
    student: Optional[StudentTozsamosc] = None
    wykladowca: Optional[WykladowcaTozsamosc] = None


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("tozsamosc.py: %s: %s", method, exc)
        raise ConnectorError("tozsamosc.py: %s: %s" % (method, exc)) from exc


def _student(row: Optional[dict]) -> Optional[StudentTozsamosc]:
    if not row:
        return None
    return StudentTozsamosc(
        student=row.get("name") or "",
        nr_albumu=row.get("album_number"),
        imie=row.get("first_name"),
        drugie_imie=row.get("middle_name"),
        nazwisko=row.get("last_name"),
        imie_nazwisko=row.get("student_name"),
        email=row.get("student_email_id"),
        zdjecie=row.get("image"),
    )


def _wykladowca(row: Optional[dict]) -> Optional[WykladowcaTozsamosc]:
    if not row:
        return None
    return WykladowcaTozsamosc(
        instructor=row.get("name") or "",
        imie_nazwisko=row.get("instructor_name"),
        wydzial=row.get("department"),
        email=row.get("email"),
        zdjecie=row.get("image"),
    )


def db_get_tozsamosc(email: str) -> EduTozsamosc:
    """Rekord studenta i wykładowcy osoby o podanym e-mailu (puste, gdy brak)."""

    result = _call("get_identity", {"email": email})
    return EduTozsamosc(
        email=email,
        student=_student(result.get("student")),
        wykladowca=_wykladowca(result.get("instructor")),
    )
