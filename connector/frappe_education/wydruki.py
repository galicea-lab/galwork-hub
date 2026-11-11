#!/usr/bin/env python
# -*- coding: utf-8 -*-
# coding: utf-8
"""Rejestr wydruków (pism/decyzji/zaświadczeń) na Frappe Education.

"""

from __future__ import annotations

import logging
from typing import List

from pydantic import BaseModel

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")


class EduWydruk(BaseModel):
    id: str  # File.name (STRING - zob. docstring modułu)
    nr_albumu: str
    tytul: str
    plik: str  # file_url (ścieżka), NIE treść binarna
    autor: str
    create_time: str
    ident: str = ""  # NIEDOSTĘPNE
    wzor_ident: str = ""  # NIEDOSTĘPNE
    rejestr_nazwa: str  # tu: attached_to_doctype, nie nazwa


def _student_by_album(connector, nr_albumu: str) -> dict | None:
    rows = connector.get_list(
        "Student", filters=[["album_number", "=", nr_albumu]],
        fields=["name", "album_number"], limit_page_length=1,
    )
    return rows[0] if rows else None


def db_get_rej_wydruku(nr_albumu: str) -> List[EduWydruk]:
    try:
        connector = get_connector()
        student = _student_by_album(connector, nr_albumu)
        if not student:
            return []
        files = list(connector.get_file_list("Student", student["name"]))
        statuses = connector.get_list(
            "Student Academic Status",
            filters=[["student", "=", student["name"]]],
            fields=["name"], limit_page_length=200,
        )
        for status in statuses:
            files.extend(connector.get_file_list("Student Academic Status", status["name"]))
        out = []
        for f in files:
            out.append(EduWydruk(
                id=f["name"],
                nr_albumu=nr_albumu,
                tytul=f.get("file_name") or "",
                plik=f.get("file_url") or "",
                autor=f.get("owner") or "",
                create_time=str(f.get("creation") or ""),
                rejestr_nazwa="Student",
            ))
        out.sort(key=lambda w: w.create_time, reverse=True)
        return out[:100]
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("wydruki.py: db_get_rej_wydruku: %s", exc)
        raise ConnectorError("wydruki.py: db_get_rej_wydruku: %s" % exc) from exc


def db_get_plik_wydruku(id: str, fn: str) -> bytes:
    try:
        connector = get_connector()
        file_doc = connector.get_doc("File", id)
        file_url = file_doc.get("file_url")
        if not file_url:
            raise ConnectorError(f"wydruki.py: db_get_plik_wydruku: File {id!r} bez file_url")
        return connector.get_file_content(file_url)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("wydruki.py: db_get_plik_wydruku: %s", exc)
        raise ConnectorError("wydruki.py: db_get_plik_wydruku: %s" % exc) from exc
