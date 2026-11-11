#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 2026-11-10: kalendarz akademicki studenta i wykładowcy przez API
# portalowe education (education.portal.api.get_calendar).
"""Kalendarz akademicki - API portalowe Frappe Education.

Terminy organizacji studiów bez zajęć (te są w ``plan.py``): początek i koniec
roku i semestru, sesja egzaminacyjna, egzaminy (``Assessment Plan``), terminy
płatności, urlopy studenta, dni wolne. Opis typów i formatu dat:
``education/portal/README.md`` (sekcja „Kalendarz akademicki”).

Daty zwracane przez Frappe są lokalne (strefa ``strefa``); zdarzenia całodniowe
mają **włączny** koniec. Przeliczenie na format kalendarza robi moduł
``kalendarz/`` huba.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduZdarzenie(BaseModel):
    """Jedno zdarzenie kalendarza akademickiego."""

    id: str
    typ: str  # academic_year_start, term_start, exam_session, exam, fee_due, ...
    tytul: str = ""
    caly_dzien: bool = True
    start: str = ""  # YYYY-MM-DD albo YYYY-MM-DDTHH:MM:SS (czas lokalny)
    koniec: str = ""  # jw.; dla całodniowych - włącznie
    doctype: Optional[str] = None
    docname: Optional[str] = None
    szczegoly: Dict[str, Any] = {}


class EduKalendarz(BaseModel):
    od: str = ""
    do: str = ""
    strefa: Optional[str] = None
    zdarzenia: List[EduZdarzenie] = []


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("kalendarz.py: %s: %s", method, exc)
        raise ConnectorError("kalendarz.py: %s: %s" % (method, exc)) from exc


def _zdarzenie(row: dict) -> EduZdarzenie:
    return EduZdarzenie(
        id=row.get("id") or "",
        typ=row.get("type") or "",
        tytul=row.get("title") or "",
        caly_dzien=bool(row.get("all_day", True)),
        start=str(row.get("start") or ""),
        koniec=str(row.get("end") or row.get("start") or ""),
        doctype=row.get("doctype"),
        docname=row.get("docname"),
        szczegoly=row.get("details") or {},
    )


def _kalendarz(result: dict) -> EduKalendarz:
    return EduKalendarz(
        od=str(result.get("from") or ""),
        do=str(result.get("to") or ""),
        strefa=result.get("timezone"),
        zdarzenia=[_zdarzenie(row) for row in result.get("events") or []],
    )


def _params(od: Optional[str], do: Optional[str], typy: Optional[Iterable[str]]) -> dict:
    params: dict = {}
    if od:
        params["from_date"] = od
    if do:
        params["to_date"] = do
    if typy:
        params["types"] = ",".join(typy)
    return params


def db_get_kalendarz_studenta(
    nr_albumu: str,
    od: Optional[str] = None,
    do: Optional[str] = None,
    typy: Optional[Iterable[str]] = None,
) -> EduKalendarz:
    """Kalendarz studenta: terminy wspólne, egzaminy jego grup, opłaty, urlopy."""
    return _kalendarz(_call("get_calendar", {"student": nr_albumu, **_params(od, do, typy)}))


def db_get_kalendarz_wykladowcy(
    email: str,
    od: Optional[str] = None,
    do: Optional[str] = None,
    typy: Optional[Iterable[str]] = None,
) -> EduKalendarz:
    """Kalendarz wykładowcy: terminy wspólne i egzaminy prowadzonych grup."""
    return _kalendarz(_call("get_calendar", {"instructor": email, **_params(od, do, typy)}))
