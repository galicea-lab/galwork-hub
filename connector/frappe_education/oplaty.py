#!/usr/bin/env python
# -*- coding: utf-8 -*-
# należności studenta (Fees) przez API portalowe education
# (education.portal.api.get_fees). Płatności online (bramka) - osobny krok,
# tutaj tylko stan i kwoty.
"""Opłaty studenta - API portalowe Frappe Education.

  EduOplata            Fees (kwota, zaległość, termin, stan)
  stan: paid / due / overdue - liczony po stronie Frappe
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduOplata(BaseModel):
    """Jedna należność (Fees)."""

    id: str = ""
    data_wystawienia: Optional[str] = None
    termin: Optional[str] = None
    waluta: Optional[str] = None
    kwota: float = 0
    zaleglosc: float = 0
    stan: str = "due"  # paid / due / overdue
    kierunek: Optional[str] = None
    rok_akademicki: Optional[str] = None
    semestr: Optional[str] = None


class EduOplaty(BaseModel):
    """Należności studenta z sumami."""

    student: str = ""
    oplaty: List[EduOplata] = []
    suma_zaleglosci: float = 0
    suma_zaplacona: float = 0
    suma_przeterminowana: float = 0
    wymaga_zaplaty: bool = False


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("oplaty.py: %s: %s", method, exc)
        raise ConnectorError("oplaty.py: %s: %s" % (method, exc)) from exc


def db_get_oplaty_studenta(nr_albumu: str) -> EduOplaty:
    """Opłaty studenta (tylko zatwierdzone dokumenty Fees)."""
    result = _call("get_fees", {"student": nr_albumu})
    sumy = result.get("totals") or {}
    return EduOplaty(
        student=result.get("student") or nr_albumu,
        oplaty=[
            EduOplata(
                id=row.get("name") or "",
                data_wystawienia=row.get("posting_date"),
                termin=row.get("due_date"),
                waluta=row.get("currency"),
                kwota=row.get("total") or 0,
                zaleglosc=row.get("outstanding") or 0,
                stan=row.get("state") or "due",
                kierunek=row.get("program"),
                rok_akademicki=row.get("academic_year"),
                semestr=row.get("academic_term"),
            )
            for row in result.get("fees") or []
        ],
        suma_zaleglosci=sumy.get("outstanding") or 0,
        suma_zaplacona=sumy.get("paid") or 0,
        suma_przeterminowana=sumy.get("overdue") or 0,
        wymaga_zaplaty=bool(result.get("payment_required")),
    )
