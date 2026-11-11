# coding: utf-8
"""
Korespondencja studenta (``Student Correspondence``) - warstwa danych dla
dynamicznych formularzy "jform" (``edu/routers/jform_api.py``), których
odpowiednikiem po stronie uczelni jest para doctype'ów dodana do forka
``frappe/education`` (katalog ``../education``):

  * ``Student Correspondence`` - konkretna sprawa studenta. 
    Pole ``form_data`` (Code/JSON) trzyma dane formularza wypełnionego
    we frontendzie (react-jsonschema-form),
  * ``Correspondence Type`` - słownik typów spraw (odpowiednik listy
    formularzy z ``GET /jform/sprawa/formtypes``); ``process_model_key``
    wskazuje model procesu BPMN w module ``wf``, z którego pochodzi
    JSON Schema / uiSchema formularza (``ProcessModelVersion.spec_json``).

Kontrakt API, któremu to ma służyć (``galwork-wf/src/api/jform.ts``):

  * ``api_form_types``      -> ``db_get_correspondence_types`` (id = nazwa typu),
  * ``api_items_lista``     -> ``db_get_student_correspondences`` + ``correspondence_to_item``,
  * ``api_post_new_form``   -> ``db_create_correspondence``,
  * ``api_get_jform``       -> ``db_get_correspondence`` / form_data,
  * ``api_jform_update``    -> ``db_update_correspondence_form``,
  * ``uploadFile``          -> ``db_upload_correspondence_file``,
  * ``/lastattachement``    -> ``db_list_correspondence_files`` + ``db_get_file_content``.

Moduł NIE buduje schema/uischema - te pochodzą z wf (``process_model_key``)
i są doklejane w routerze; warstwa konektora odpowiada wyłącznie za system
rekordu (Frappe Education). Wzorzec (dziedziczenie po ``get_connector``,
``ConnectorError``, brak zapytań na starcie importu) - jak
``connector/frappe_education/student.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from connector.frappe_education.connector import ConnectorError, get_connector

log = logging.getLogger("connector.frappe_education")

CORRESPONDENCE_DOCTYPE = "Student Correspondence"
CORRESPONDENCE_TYPE_DOCTYPE = "Correspondence Type"

# Pola sprawy pobierane przy listowaniu/odczycie (jedno miejsce prawdy dla
# routera i testów). Odpowiadają polom widocznym na liście w galwork-wf.
CORRESPONDENCE_FIELDS = [
    "name", "student", "student_name", "album_number",
    "correspondence_type", "process_model_key", "subject",
    "status", "program", "academic_year", "description",
    "form_data", "process_instance_id", "signature_status",
    # odpowiedź z pieczęcią elektroniczną (edu/services/sealing.py)
    "signed_document",
    # odpowiedź dziekanatu z procesu edu-pismo
    "response", "response_date",
    "creation", "modified",
]

# metody whitelist doctype'u (erpnext-education, student_correspondence.py) - 
# zapis pól read_only i statusu przez db_set, niezależnie od docstatus dokumentu.
_METHOD_PREFIX = "education.education.doctype.student_correspondence.student_correspondence."

_TYPE_FIELDS = ["name", "type_name", "process_model_key", "is_active", "description"]

# Pola studenta potrzebne do rozwiązywania tożsamości (email/nr albumu).
_STUDENT_FIELDS = ["name", "album_number", "student_email_id", "private_email", "student_name"]

# Kolejność sprawy -> pola ``Item`` 
# ``id``/``ID`` to nazwa dokumentu Frappe (np. "EDU-COR-2026-00001");

def _json_field(value: Any) -> Any:
    """Pole typu Code(JSON) Frappe bywa stringiem - zwróć sparsowany dict."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            log.warning("correspondence.py: niepoprawny JSON w polu: %.120s", text)
            return {}
    return value if value is not None else {}


def _type_entry(row: dict) -> dict:
    name = row.get("name")
    return {
        "id": name,
        "name": row.get("type_name") or name,
        "process_model_key": row.get("process_model_key") or "",
        "description": row.get("description") or "",
    }


def db_get_correspondence_types(active_only: bool = True) -> list[dict]:
    """Typy spraw (``Correspondence Type``) - odpowiednik listy formularzy.

    Zwraca ``[{'id', 'name', 'process_model_key', 'description'}]``, gdzie
    ``id`` = nazwa dokumentu Frappe (``type_name``, bo tak działa naming
    "By fieldname"). Router mapuje to na ``FormType {id, name}``.
    """
    try:
        connector = get_connector()
        filters = [["is_active", "=", 1]] if active_only else None
        rows = connector.get_list(
            CORRESPONDENCE_TYPE_DOCTYPE,
            filters=filters,
            fields=_TYPE_FIELDS,
            order_by="type_name asc",
            limit_page_length=500,
        )
        return [_type_entry(row) for row in rows]
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_correspondence_types: %s", exc)
        raise ConnectorError("correspondence.py: db_get_correspondence_types: %s" % exc) from exc


def db_get_correspondence_type(type_name: str) -> dict | None:
    """Pojedynczy typ sprawy po nazwie (``Correspondence Type.name``)."""
    if not type_name:
        return None
    try:
        connector = get_connector()
        rows = connector.get_list(
            CORRESPONDENCE_TYPE_DOCTYPE,
            filters=[["name", "=", type_name]],
            fields=_TYPE_FIELDS,
            limit_page_length=1,
        )
        return _type_entry(rows[0]) if rows else None
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_correspondence_type: %s", exc)
        raise ConnectorError("correspondence.py: db_get_correspondence_type: %s" % exc) from exc


def db_find_student(*, email: str | None = None, nr_albumu: str | None = None) -> dict | None:
    """Student Frappe po e-mailu (``student_email_id``) albo numerze albumu.

    Kolejność tak jak w ``galwork-wf``: formularz nowej sprawy wysyła
    ``student='nr_albumu'`` (placeholder) i realny ``email``, więc to e-mail
    jest podstawowym kluczem.
    """
    if email:
        filters = [["student_email_id", "=", email]]
    elif nr_albumu:
        filters = [["album_number", "=", nr_albumu]]
    else:
        return None
    try:
        connector = get_connector()
        rows = connector.get_list(
            "Student", filters=filters, fields=_STUDENT_FIELDS, limit_page_length=1)
        return rows[0] if rows else None
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_find_student: %s", exc)
        raise ConnectorError("correspondence.py: db_find_student: %s" % exc) from exc


def _student_ref(student: str | None, email: str | None, nr_albumu: str | None) -> str:
    """Nazwa dokumentu ``Student`` na podstawie name/email/nr_albumu."""
    if student:
        return student
    found = db_find_student(email=email, nr_albumu=nr_albumu)
    return (found or {}).get("name") or ""


def db_get_student_correspondences(
    *,
    student: str | None = None,
    email: str | None = None,
    nr_albumu: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Sprawy studenta (lista ``Item``) - najnowsze pierwsze.
    Rekordy skasowane (``docstatus=2``) są pomijane.
    """
    try:
        student_name = _student_ref(student, email, nr_albumu)
        if not student_name:
            return []
        connector = get_connector()
        return connector.get_list(
            CORRESPONDENCE_DOCTYPE,
            filters=[["student", "=", student_name], ["docstatus", "!=", 2]],
            fields=CORRESPONDENCE_FIELDS,
            order_by="creation desc",
            limit_page_length=limit,
        )
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_student_correspondences: %s", exc)
        raise ConnectorError("correspondence.py: db_get_student_correspondences: %s" % exc) from exc


def db_get_correspondence(name: str) -> dict:
    """Pojedyncza sprawa (pełny dokument Frappe)."""
    try:
        return get_connector().get_doc(CORRESPONDENCE_DOCTYPE, name)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_correspondence: %s", exc)
        raise ConnectorError("correspondence.py: db_get_correspondence: %s" % exc) from exc


#  2026-11-08 //
def db_get_correspondence_owner(name: str) -> dict | None:
    """Tylko pola potrzebne do sprawdzenia właściciela sprawy (`email`,
    `student`), bez treści i danych formularza; `None`, gdy sprawy nie ma."""
    try:
        row = get_connector().get_value(CORRESPONDENCE_DOCTYPE, {"name": name}, ["email", "student"])
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_correspondence_owner: %s", exc)
        raise ConnectorError("correspondence.py: db_get_correspondence_owner: %s" % exc) from exc
    return row if isinstance(row, dict) and row else None
# // --


def db_get_correspondence_form_data(name: str) -> dict:
    """Dane formularza sprawy (``form_data`` jako dict)."""
    doc = db_get_correspondence(name)
    return _json_field(doc.get("form_data"))


def db_create_correspondence(
    *,
    student: str,
    correspondence_type: str,
    subject: str | None = None,
    description: str | None = None,
    program: str | None = None,
    academic_year: str | None = None,
    process_model_key: str | None = None,
    form_data: dict | None = None,
) -> dict:
    """Tworzy sprawę studenta (``Student Correspondence``).

    ``process_model_key`` jest dociągane z typu sprawy, jeśli nie podano go
    wprost - pole ``fetch_from`` działa w formularzu Frappe, ale nie
    gwarantuje wypełnienia przy tworzeniu przez REST, więc ustawiamy je
    jawnie. Gdy Frappe odrzuci zapis pola tylko-do-odczytu, klucz i tak
    zostanie rozwiązany po stronie routera z ``correspondence_type``.
    """
    if not student:
        raise ConnectorError("correspondence.py: db_create_correspondence: brak studenta")
    if not correspondence_type:
        raise ConnectorError("correspondence.py: db_create_correspondence: brak typu sprawy")
    if not process_model_key:
        entry = db_get_correspondence_type(correspondence_type)
        process_model_key = (entry or {}).get("process_model_key") or ""
    data: dict[str, Any] = {
        "student": student,
        "correspondence_type": correspondence_type,
        "subject": subject or correspondence_type,
        "status": "Draft",
        "form_data": json.dumps(form_data or {}, ensure_ascii=False),
    }
    if description:
        data["description"] = description
    if program:
        data["program"] = program
    if academic_year:
        data["academic_year"] = academic_year
    if process_model_key:
        data["process_model_key"] = process_model_key
    try:
        return get_connector().create_doc(CORRESPONDENCE_DOCTYPE, data)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_create_correspondence: %s", exc)
        raise ConnectorError("correspondence.py: db_create_correspondence: %s" % exc) from exc


def db_update_correspondence(
    name: str,
    *,
    form_data: dict | None = None,
    status: str | None = None,
    subject: str | None = None,
    description: str | None = None,
) -> dict:
    """Aktualizuje sprawę (najczęściej ``form_data`` po zapisie formularza)."""
    data: dict[str, Any] = {}
    if form_data is not None:
        data["form_data"] = json.dumps(form_data, ensure_ascii=False)
    if status:
        data["status"] = status
    if subject:
        data["subject"] = subject
    if description is not None:
        data["description"] = description
    try:
        if not data:
            return db_get_correspondence(name)
        return get_connector().update_doc(CORRESPONDENCE_DOCTYPE, name, data)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_update_correspondence: %s", exc)
        raise ConnectorError("correspondence.py: db_update_correspondence: %s" % exc) from exc


def _call(method: str, params: dict) -> Any:
    try:
        return get_connector().call_method(_METHOD_PREFIX + method, params, http_method="POST")
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: %s: %s", method, exc)
        raise ConnectorError("correspondence.py: %s: %s" % (method, exc)) from exc


# 2026-11-06: zapis przebiegu procesu (edu/services/case_sync.py).
def db_set_correspondence_process(name: str, process_instance_id: str) -> Any:
    """Identyfikator instancji procesu `wf` w sprawie (``process_instance_id``)."""
    return _call("set_process_instance", {"name": name, "process_instance_id": process_instance_id})


def db_set_correspondence_status(name: str, status: str) -> Any:
    """Status sprawy (``Draft``, ``Submitted``, ``In Progress``, ``Completed``, ``Rejected``...)."""
    return _call("update_status", {"name": name, "status": status})


def db_record_correspondence_response(name: str, response: str, status: str | None = None) -> Any:
    """Odpowiedź dziekanatu (``response``, ``response_date``) i opcjonalnie status."""
    params = {"name": name, "response": response}
    if status:
        params["status"] = status
    return _call("record_response", params)


# 2026-11-06: pieczęć elektroniczna odpowiedzi (edu/services/sealing.py).
def db_get_correspondence_pdf(name: str, print_format: str | None = None,
                              letterhead: str | None = None) -> bytes:
    """PDF sprawy z szablonu wydruku Frappe (``Print Format``)."""
    try:
        return get_connector().get_print_pdf(
            CORRESPONDENCE_DOCTYPE, name, print_format or None, letterhead=letterhead or None)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_correspondence_pdf: %s", exc)
        raise ConnectorError("correspondence.py: db_get_correspondence_pdf: %s" % exc) from exc


def db_set_correspondence_signed(name: str, status: str) -> Any:
    """``signature_status = Signed`` przy zachowaniu statusu sprawy."""
    return _call("update_status", {"name": name, "status": status, "signature_status": "Signed"})


def db_update_correspondence_form(name: str, form_data: dict) -> dict:
    """Zapis ``form_data`` (odpowiednik ``api_jform_update``)."""
    return db_update_correspondence(name, form_data=form_data or {})


def db_list_correspondence_files(name: str) -> list[dict]:
    """Załączniki sprawy (najnowsze pierwsze) - ``File`` po powiązaniu."""
    try:
        return get_connector().get_file_list(CORRESPONDENCE_DOCTYPE, name)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_list_correspondence_files: %s", exc)
        raise ConnectorError("correspondence.py: db_list_correspondence_files: %s" % exc) from exc


def db_upload_correspondence_file(
    name: str,
    filename: str,
    content: bytes,
    *,
    is_private: bool = True,
    fieldname: str | None = None,
    content_type: str = "application/octet-stream",
) -> dict:
    """Wgrywa załącznik i podpina go do sprawy (``upload_file``)."""
    try:
        return get_connector().upload_file(
            CORRESPONDENCE_DOCTYPE, name, filename, content,
            is_private=is_private, fieldname=fieldname, content_type=content_type)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_upload_correspondence_file: %s", exc)
        raise ConnectorError("correspondence.py: db_upload_correspondence_file: %s" % exc) from exc


def db_get_file_content(file_url: str) -> bytes:
    """Treść pliku (publicznego albo prywatnego) z Frappe."""
    try:
        return get_connector().get_file_content(file_url)
    except ConnectorError:
        raise
    except Exception as exc:
        log.error("correspondence.py: db_get_file_content: %s", exc)
        raise ConnectorError("correspondence.py: db_get_file_content: %s" % exc) from exc


def correspondence_process_model_key(doc: dict) -> str:
    """Klucz modelu procesu sprawy - z dokumentu albo z jej typu."""
    key = (doc.get("process_model_key") or "").strip()
    if key:
        return key
    type_name = doc.get("correspondence_type") or ""
    if not type_name:
        return ""
    entry = db_get_correspondence_type(type_name)
    return (entry or {}).get("process_model_key") or ""


def correspondence_to_item(doc: dict) -> dict:
    """Sprawa -> wiersz listy ``Item`` oczekiwany przez jform.ts."""
    creation = str(doc.get("creation") or "")
    return {
        "id": doc.get("name") or "",
        "ID": doc.get("name") or "",
        "Step": doc.get("status") or "",
        "Rodzaj_sprawy": doc.get("correspondence_type") or doc.get("subject") or "",
        "Akronim": "",
        "Sygnatura": doc.get("name") or "",
        "Student": doc.get("album_number") or doc.get("student") or "",
        "Status": doc.get("status") or "",
        "Data": creation[:10],
        "tm": str(doc.get("modified") or creation),
        # 2026-11-06: temat pisma i odpowiedź dziekanatu (proces edu-pismo)
        "Temat": doc.get("subject") or "",
        "Odpowiedz": doc.get("response") or "",
        # 2026-11-06: czy jest odpowiedź z pieczęcią (GET /sprawa/signed)
        "Pieczec": bool(doc.get("signed_document")),
    }


def correspondence_form_data(doc: dict) -> dict:
    """``form_data`` sprawy jako dict (z surowego dokumentu)."""
    return _json_field(doc.get("form_data"))


__all__ = [
    "db_set_correspondence_process",
    "db_set_correspondence_status",
    "db_record_correspondence_response",
    "db_get_correspondence_pdf",
    "db_set_correspondence_signed",
    "CORRESPONDENCE_DOCTYPE",
    "CORRESPONDENCE_TYPE_DOCTYPE",
    "db_get_correspondence_types",
    "db_get_correspondence_type",
    "db_find_student",
    "db_get_student_correspondences",
    "db_get_correspondence",
    "db_get_correspondence_form_data",
    "db_create_correspondence",
    "db_update_correspondence",
    "db_update_correspondence_form",
    "db_list_correspondence_files",
    "db_upload_correspondence_file",
    "db_get_file_content",
    "correspondence_process_model_key",
    "correspondence_to_item",
    "correspondence_form_data",
]
