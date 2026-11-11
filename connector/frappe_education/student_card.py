#!/usr/bin/env python
# -*- coding: utf-8 -*-
# mLegitymacja w serwisie studenckim (education.portal.api.*) -
# stan zamówienia, zamówienie/przedłużenie, jednorazowe kody QR oraz wniosek
# o unieważnienie. Rozmowę z MLS prowadzi wyłącznie Frappe
# (education/integrations/mlegitymacje); hub nie widzi PESEL ani zdjęcia.
"""Legitymacja mLegitymacja - API portalowe Frappe Education.

  EduLegitymacja.stan   none / ordered / issued
  EduKodyLegitymacji    jednorazowy odczyt QR i kodu aktywacyjnego (audytowany
                        po stronie Frappe) - hub przekazuje go dalej i nie
                        zapisuje
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

_METHOD_PREFIX = "education.portal.api."


class EduLegitymacja(BaseModel):
    """Stan legitymacji studenta (bez kodów)."""

    student: str = ""
    stan: str = "none"  # none / ordered / issued
    zamowienie: Optional[str] = None
    rodzaj: Optional[str] = None
    status: Optional[str] = None
    mls_status: Optional[str] = None
    numer_mls: Optional[str] = None
    nr_karty: Optional[str] = None
    wazna_od: Optional[str] = None
    wazna_do: Optional[str] = None
    termin_qr: Optional[str] = None
    blad: Optional[str] = None
    zamowiono: Optional[str] = None
    kody_dostepne: bool = False


class EduKodyLegitymacji(BaseModel):
    """Jednorazowy odczyt kodów aktywacyjnych."""

    zamowienie: str = ""
    qr: str = ""
    kod_aktywacyjny: str = ""
    wazny_do: Optional[str] = None
    ostrzezenie: Optional[str] = None


class EduZamowienieLegitymacji(BaseModel):
    """Wynik zamówienia / unieważnienia."""

    zamowienie: str = ""
    rodzaj: Optional[str] = None
    status: Optional[str] = None
    blad: Optional[str] = None


def _call(method: str, params: dict) -> dict:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("student_card.py: %s: %s", method, exc)
        raise ConnectorError("student_card.py: %s: %s" % (method, exc)) from exc


def db_get_legitymacja(nr_albumu: str) -> EduLegitymacja:
    """Stan legitymacji (bez kodów QR i aktywacyjnych)."""
    result = _call("get_card", {"student": nr_albumu})
    return EduLegitymacja(
        student=result.get("student") or nr_albumu,
        stan=result.get("kind") or "none",
        zamowienie=result.get("order"),
        rodzaj=result.get("order_kind"),
        status=result.get("status"),
        mls_status=result.get("mls_status"),
        numer_mls=result.get("mls_order_id"),
        nr_karty=result.get("student_card_id"),
        wazna_od=result.get("release_date"),
        wazna_do=result.get("expire_date"),
        termin_qr=result.get("valid_to"),
        blad=result.get("error"),
        zamowiono=result.get("ordered_on"),
        kody_dostepne=bool(result.get("codes_available")),
    )


def db_zamow_legitymacje(
    nr_albumu: str,
    rodzaj: str = "Issue",
    wazna_od: Optional[str] = None,
    wazna_do: Optional[str] = None,
) -> EduZamowienieLegitymacji:
    """Zamówienie legitymacji (``Issue``) albo przedłużenie (``Renewal``)."""
    params: dict = {"student": nr_albumu, "kind": rodzaj}
    if wazna_od:
        params["release_date"] = wazna_od
    if wazna_do:
        params["expire_date"] = wazna_do
    result = _call("order_card", params)
    return EduZamowienieLegitymacji(
        zamowienie=result.get("name") or "",
        rodzaj=result.get("kind"),
        status=result.get("status"),
        blad=result.get("error"),
    )


def db_pobierz_kody_legitymacji(nr_albumu: str) -> EduKodyLegitymacji:
    """Jednorazowy odczyt QR i kodu aktywacyjnego (Frappe audytuje wywołanie).

    Hub nie zapisuje tych danych; przekazuje je do przeglądarki i tyle.
    """
    result = _call("reveal_card_codes", {"student": nr_albumu})
    return EduKodyLegitymacji(
        zamowienie=result.get("order") or "",
        qr=result.get("qr_code") or "",
        kod_aktywacyjny=result.get("activation_code") or "",
        wazny_do=result.get("valid_to"),
        ostrzezenie=result.get("warning"),
    )


def db_zamow_uniewaznienie_legitymacji(
    nr_albumu: str, powod: Optional[str] = None
) -> EduZamowienieLegitymacji:
    """Wniosek o unieważnienie legitymacji (osobne zamówienie w Frappe)."""
    params: dict = {"student": nr_albumu}
    if powod:
        params["reason"] = powod
    result = _call("request_card_revocation", params)
    return EduZamowienieLegitymacji(
        zamowienie=result.get("name") or "",
        rodzaj=result.get("kind"),
        status=result.get("status"),
        blad=result.get("error"),
    )


def db_odswiez_legitymacje(nr_albumu: str) -> EduLegitymacja:
    """Ponowne odpytanie MLS o stan zamówienia (education.portal.api.refresh_card).

    Scheduler Frappe odświeża zamówienia co 10 minut; to jest przycisk
    "odśwież" na ekranie studenta.
    """
    result = _call("refresh_card", {"student": nr_albumu})
    return EduLegitymacja(
        student=result.get("student") or nr_albumu,
        stan=result.get("kind") or "none",
        zamowienie=result.get("order"),
        rodzaj=result.get("order_kind"),
        status=result.get("status"),
        mls_status=result.get("mls_status"),
        numer_mls=result.get("mls_order_id"),
        nr_karty=result.get("student_card_id"),
        wazna_od=result.get("release_date"),
        wazna_do=result.get("expire_date"),
        termin_qr=result.get("valid_to"),
        blad=result.get("error"),
        zamowiono=result.get("ordered_on"),
        kody_dostepne=bool(result.get("codes_available")),
    )
