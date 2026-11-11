# coding: utf-8
# Korespondencja studenta z dziekanatem (sprawy).
#
# Tylko endpointy oparte na Frappe Education 
# (doctype `Student Correspondence` i słownik `Correspondence Type`). 
#
# Ścieżki pod prefiksem /jform pozostają zgodne z frontendem galwork-edu
# (src/api/jform.ts). Każdy endpoint wymaga tokenu; student ma dostęp
# tylko do własnych spraw, pracownik dziekanatu - do wszystkich.

from __future__ import annotations

import logging
from typing import Annotated, Dict

import jsonschema
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from connector.frappe_education.connector import ConnectorError
from connector.frappe_education.correspondence import (
    correspondence_form_data,
    correspondence_process_model_key,
    correspondence_to_item,
    db_create_correspondence,
    db_find_student,
    db_get_correspondence,
    # właściciel sprawy bez pobierania całego dokumentu.
    db_get_correspondence_owner,
    db_get_correspondence_types,
    db_get_file_content,
    db_get_student_correspondences,
    db_list_correspondence_files,
    db_update_correspondence,
    db_update_correspondence_form,
    db_upload_correspondence_file,
)
from edu.dependencies import is_own_email, is_staff, user_email

# 2026-11-06: pieczęć elektroniczna odpowiedzi (edu/services/sealing.py).
from edu.services.sealing import SealSettings, seal_case
from edu.util import content_disposition
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import CurrentUser, ProcessInstanceStatus
from wf.services.process_service import ProcessServiceError, get_service
# odpowiedzi z komunikatem Frappe dla użytkownika (error_message),
# a w logach tylko bezpieczne str(e) - zob. connector/frappe_education/connector.py.
from connector.frappe_education.connector import error_message

logger = logging.getLogger(__name__)

router = APIRouter(
  prefix='/jform',
  tags=['korespondencja'],
  responses={404: {"description": "Not found"}},
)


def _error(e: Exception, **empty) -> Dict:
  source = ' (Frappe Education)' if isinstance(e, ConnectorError) else ''
  return {'code': -7, 'message': 'Niepowodzenie%s: %s' % (source, error_message(e)), **empty}


def _own_student_name(current_user: CurrentUser) -> str:
  found = db_find_student(email=user_email(current_user))
  return (found or {}).get('name') or ''


def _is_case_owner(doc: dict, current_user: CurrentUser) -> bool:
  if is_own_email(current_user, doc.get('email')):
    return True
  return bool(doc.get('student')) and doc.get('student') == _own_student_name(current_user)


# 2026-11-08 //
def _is_owner_of(case_id: str, current_user: CurrentUser) -> bool:
  """Czy sprawa należy do zalogowanego studenta - na podstawie samych pól
  `email` / `student`; nieistniejąca sprawa to `False`."""
  owner = db_get_correspondence_owner(case_id)
  return bool(owner) and _is_case_owner(owner, current_user)


def _check_case_access(case_id: str, current_user: CurrentUser) -> None:
  """Dostęp do sprawy (dziekanat albo właściciel) sprawdzany przed pobraniem
  jej treści; cudza i nieistniejąca sprawa dają ten sam błąd 403."""
  if is_staff(current_user) or _is_owner_of(case_id, current_user):
    return
  raise HTTPException(status_code=403, detail='Zabroniony dostęp')


def _get_case_checked(case_id: str, current_user: CurrentUser) -> dict:
  """Sprawa, jeśli należy do zalogowanego studenta lub pytający to dziekanat."""
  _check_case_access(case_id, current_user)
  return db_get_correspondence(case_id)
# // --


# 2026-11-06: złożone pismo (proces wystartował) nie jest już edytowalne -
# jego dane są w procesie, a zmiany idą przez zadania (np. "Uzupełnienie").
def _is_submitted(doc: dict) -> bool:
  return bool(doc.get('process_instance_id')) or (doc.get('status') or 'Draft') != 'Draft'


def _case_meta(doc: dict) -> dict:
  return {
    'status': doc.get('status') or 'Draft',
    'subject': doc.get('subject') or '',
    'response': doc.get('response') or '',
    'response_date': str(doc.get('response_date') or ''),
    'process_instance_id': doc.get('process_instance_id') or '',
  }


def case_form_schema(db : Session, process_model_key : str, data : dict | None = None):
  """JSON Schema + uiSchema formularza sprawy z modelu procesu w `wf`.

  `Correspondence Type.process_model_key` wskazuje opublikowaną wersję
  (`ProcessModelVersion.spec_json`); dla nowej sprawy bierzemy formularz
  startowy (startEvent), a dla istniejącej - ten sam schemat, bo `form_data`
  sprawy to dane właśnie tego formularza. Brak modelu/ schematu = puste dict
  (frontend pokazuje zapasowy edytor JSON).
  """
  if not process_model_key:
    return {}, {}
  try:
    service = get_service()
    version = service.published_version(db, process_model_key)
    _task_id, schema, ui_schema = service.start_form(version, data=data or {})
    return schema or {}, ui_schema or {}
  except Exception as exc:
    logger.warning('case_form_schema(%s): %s', process_model_key, exc)
    return {}, {}


@router.get('/sprawa/formtypes', response_model=Dict)
def get_case_form_types(current_user: CurrentUser = Depends(get_current_wf_user)) -> Dict:
  """Typy spraw (słownik `Correspondence Type`) - `{id, name}` dla frontendu."""
  try:
    forms = [{'id': t['id'], 'name': t['name']}
             for t in db_get_correspondence_types()]
    return {'code': 0, 'message': 'OK', 'result': forms}
  except Exception as e:
    logger.info('jform/sprawa/formtypes: %s', e)
    return _error(e, result=[])


@router.get('/sprawa/list', response_model=Dict)
def get_case_list(email : str = '', nr_albumu : str = '',
                  current_user: CurrentUser = Depends(get_current_wf_user)) -> Dict:
  """Lista spraw studenta.

  Student dostaje własne sprawy; dziekanat może wskazać studenta przez `email` albo `nr_albumu`.
  """
  try:
    if not is_staff(current_user):
      if nr_albumu or (email and not is_own_email(current_user, email)):
        return {'code': -2, 'message': 'Zabroniony dostęp', 'list': []}
      email = user_email(current_user)
    if not email and not nr_albumu:
      return {'code': -3, 'message': 'Podaj email albo nr_albumu', 'list': []}
    docs = db_get_student_correspondences(email=email or None, nr_albumu=nr_albumu or None)
    return {'code': 0, 'message': '', 'list': [correspondence_to_item(d) for d in docs]}
  except Exception as e:
    logger.info('jform/sprawa/list: %s', e)
    return _error(e, list=[])


@router.post('/sprawa/new', response_model=Dict)
def post_case_new(kierunek : str = '',
                  student : str = '',
                  formtype : str = '',
                  email : str = '',
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user),
                  ) -> Dict:
  """Nowa sprawa (korespondencja) w Frappe Education.

  `formtype` = `name` (`type_name`) z `Correspondence Type`. Student zakłada
  sprawę tylko dla siebie (rozpoznanie po e-mailu z tokenu); dziekanat może
  wskazać studenta przez `student` (nr albumu) albo `email`.
  """
  empty = {'inst': {}, 'schema': {}, 'uischema': {}, 'data': {}}
  try:
    if not formtype:
      return {'code': -1, 'message': 'Brak typu sprawy (formtype)', **empty}
    if is_staff(current_user) and (email or (student and student != 'nr_albumu')):
      if email:
        found = db_find_student(email=email)
      else:
        found = db_find_student(nr_albumu=student)
    else:
      if email and not is_own_email(current_user, email):
        return {'code': -2, 'message': 'Zabroniony dostęp', **empty}
      email = user_email(current_user)
      found = db_find_student(email=email)
    student_name = (found or {}).get('name') or ''
    if not student_name:
      return {'code': -1, 'message': 'Nie znaleziono studenta', **empty}
    doc = db_create_correspondence(
      student=student_name,
      correspondence_type=formtype,
      program=kierunek or None,
    )
    name = doc.get('name')
    schema, uischema = case_form_schema(db, correspondence_process_model_key(doc))
    inst = {'id': name, 'instanceNumber': name, 'status': doc.get('status') or 'Draft'}
    return {
      'code': 0,
      'message': 'OK',
      'inst': inst,
      'schema': schema,
      'uischema': uischema,
      'data': correspondence_form_data(doc),
    }
  except HTTPException:
    raise
  except Exception as e:
    logger.info('jform/sprawa/new: %s', e)
    return _error(e, **empty)


@router.post('/sprawa/update', response_model=Dict)
def post_case_update(id : str,
                     update : Dict,
                     current_user: CurrentUser = Depends(get_current_wf_user),
                     ) -> Dict:
  """Zapis danych formularza sprawy do Frappe (`Student Correspondence.form_data`)."""
  try:
    doc = _get_case_checked(id, current_user)
    if _is_submitted(doc):
      return {'code': -4, 'message': 'Pismo zostało już złożone', 'result': {}}
    merged = correspondence_form_data(doc)
    merged.update(update)
    res = db_update_correspondence_form(id, merged)
    return {'code': 0, 'message': 'OK', 'result': res}
  except HTTPException:
    raise
  except Exception as e:
    logger.info('jform/sprawa/update: %s', e)
    return _error(e, result={})


# 2026-11-05: endpoint wymaga teraz tokenu i sprawdza właściciela sprawy.
# Frontend musi dodać nagłówek.
@router.post('/sprawa/attach', response_model=Dict)
def post_case_attachment(file: Annotated[bytes, File(description="Plik załącznika")],
                         id: Annotated[str, Form()],
                         att_name: Annotated[str, Form()],
                         current_user: CurrentUser = Depends(get_current_wf_user),
                         ) -> Dict:
  """Dodaje załącznik do sprawy (Frappe Education `File`, dołączony do dokumentu)."""
  try:
    if not file:
      return {'code': -1, 'message': 'Brak załącznika', 'result': {}}
    # Sam dostęp - dodanie załącznika nie wymaga treści sprawy.
    _check_case_access(id, current_user)
    res = db_upload_correspondence_file(id, att_name, file)
    return {'code': 0, 'message': 'OK', 'result': res}
  except HTTPException:
    raise
  except Exception as e:
    logger.info('jform/sprawa/attach: %s', e)
    return _error(e, result={})


@router.get('/sprawa/form', response_model=Dict)
def get_case_form(id : str,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user),
                  ) -> Dict:
  """Formularz istniejącej sprawy: schema/uischema z modelu procesu + `data`."""
  empty = {'schema': {}, 'uischema': {}, 'data': {}}
  try:
    doc = _get_case_checked(id, current_user)
    data = correspondence_form_data(doc)
    schema, uischema = case_form_schema(db, correspondence_process_model_key(doc), data=data)
    return {'code': 0, 'message': 'OK', 'uischema': uischema, 'schema': schema, 'data': data,
            'case': _case_meta(doc)}
  except HTTPException:
    raise
  except Exception as e:
    logger.info('jform/sprawa/form: %s', e)
    return _error(e, **empty)


def _running_instance(db: Session, case_id: str) -> ProcessInstance | None:
  """Instancja procesu tej sprawy w `wf` (bez anulowanych).

  Chroni przed drugim startem, gdy zapis `process_instance_id` do Frappe
  się nie powiódł (słuchacz edu/services/case_sync.py tylko loguje błąd).
  """
  return db.execute(
    select(ProcessInstance)
    .where(ProcessInstance.business_key == case_id,
           ProcessInstance.status != ProcessInstanceStatus.CANCELLED)
  ).scalars().first()


# 2026-11-06: złożenie pisma = start procesu BPMN (np. edu-pismo).
@router.post('/sprawa/submit', response_model=Dict)
def post_case_submit(id : str,
                     db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(get_current_wf_user),
                     ) -> Dict:
  """Składa pismo: uruchamia proces z `Correspondence Type.process_model_key`.

  Pismo składa student, właściciel sprawy (zadanie "Uzupełnienie" jest
  przypisane do jego konta Keycloak). `business_key` instancji = nazwa sprawy
  Frappe; `process_instance_id` i status `Submitted` zapisuje we Frappe
  edu/services/case_sync.py. Dane formularza są sprawdzane schematem
  formularza startowego procesu.
  """
  try:
    # właściciel sprawy bez pobierania całego dokumentu.
    if not _is_owner_of(id, current_user):
      if is_staff(current_user):
        return {'code': -2, 'message': 'Pismo składa student, którego dotyczy sprawa'}
      raise HTTPException(status_code=403, detail='Zabroniony dostęp')
    doc = db_get_correspondence(id)
    if _is_submitted(doc) or _running_instance(db, id) is not None:
      return {'code': -4, 'message': 'Pismo zostało już złożone'}
    key = correspondence_process_model_key(doc)
    if not key:
      return {'code': -5, 'message': 'Typ sprawy nie ma przypisanego procesu'}
    service = get_service()
    try:
      version = service.published_version(db, key)
    except ProcessServiceError as exc:
      return {'code': -5, 'message': str(exc)}
    form = correspondence_form_data(doc)
    _task_id, schema, _ui = service.start_form(version, data=form)
    if schema:
      try:
        jsonschema.validate(instance=form, schema=schema)
      except jsonschema.ValidationError as exc:
        return {'code': -3, 'message': 'Uzupełnij formularz: %s' % exc.message}
    subject = str(form.get('temat') or doc.get('subject') or doc.get('correspondence_type') or id)
    data = {
      **form,
      'case_id': id,
      'student': doc.get('student') or '',
      'student_name': doc.get('student_name') or '',
      'album_number': doc.get('album_number') or '',
      'student_username': current_user.username,
      'student_email': user_email(current_user),
      'correspondence_type': doc.get('correspondence_type') or '',
    }
    if form.get('temat') and form.get('temat') != doc.get('subject'):
      db_update_correspondence(id, subject=subject[:140])
    instance = service.start_instance(
      db, version, title=f"{doc.get('correspondence_type') or 'Pismo'}: {subject}"[:200],
      business_key=id, data=data, actor=current_user)
    return {'code': 0, 'message': 'OK', 'process_instance_id': str(instance.id),
            'status': 'Submitted'}
  except HTTPException:
    raise
  except Exception as e:
    db.rollback()
    logger.info('jform/sprawa/submit: %s', e)
    return _error(e)


@router.get('/sprawa/attachments', response_model=Dict)
def get_case_attachments(id : str,
                         current_user: CurrentUser = Depends(get_current_wf_user)) -> Dict:
  """Lista załączników sprawy (najnowsze pierwsze)."""
  try:
    # sam dostęp - lista plików nie wymaga treści sprawy.
    _check_case_access(id, current_user)
    files = db_list_correspondence_files(id)
    rows = [{'id': f.get('name'), 'file_name': f.get('file_name'),
             'creation': str(f.get('creation') or '')} for f in files]
    return {'code': 0, 'message': 'OK', 'result': rows}
  except HTTPException:
    raise
  except Exception as e:
    logger.info('jform/sprawa/attachments: %s', e)
    return _error(e, result=[])


@router.get('/sprawa/attachment')
def get_case_attachment(id : str, att_id : str = '',
                        current_user: CurrentUser = Depends(get_current_wf_user)) -> Response:
  """Treść załącznika sprawy; bez `att_id` - najnowszy załącznik."""
  # sam dostęp - załącznik nie wymaga treści sprawy.
  _check_case_access(id, current_user)
  files = db_list_correspondence_files(id)
  match = next((f for f in files if f.get('name') == att_id), None) if att_id else (files[0] if files else None)
  if not match:
    raise HTTPException(status_code=404, detail='Brak załącznika')
  content = db_get_file_content(match['file_url'])
  fn = str(match.get('file_name') or 'zalacznik')
  media = 'application/pdf' if fn.lower().endswith('.pdf') else 'application/octet-stream'
  headers = {'Content-Disposition': content_disposition(fn, inline=True)}
  return Response(content, headers=headers, media_type=media)


# 2026-11-06: opieczętowana odpowiedź (`signed_document`) i ręczne
# ponowienie pieczęci, gdy pieczętowanie w tle się nie udało.
@router.get('/sprawa/signed')
def get_case_signed(id : str,
                    current_user: CurrentUser = Depends(get_current_wf_user)) -> Response:
  """Odpowiedź dziekanatu z pieczęcią elektroniczną (PDF)."""
  doc = _get_case_checked(id, current_user)
  file_url = doc.get('signed_document')
  if not file_url:
    raise HTTPException(status_code=404, detail='Odpowiedź nie ma jeszcze pieczęci')
  content = db_get_file_content(file_url)
  fn = str(file_url).rsplit('/', 1)[-1] or f'{id}-pieczec.pdf'
  headers = {'Content-Disposition': content_disposition(fn, inline=True)}
  return Response(content, headers=headers, media_type='application/pdf')


@router.post('/sprawa/seal', response_model=Dict)
def post_case_seal(id : str,
                   db: Session = Depends(get_db),
                   current_user: CurrentUser = Depends(get_current_wf_user),
                   ) -> Dict:
  """Pieczętuje odpowiedź teraz (jedna próba, bez kolejki) - dla dziekanatu."""
  if not is_staff(current_user):
    raise HTTPException(status_code=403, detail='Zabroniony dostęp')
  settings = SealSettings.load()
  if not settings.enabled:
    return {'code': -5, 'message': 'Pieczęć elektroniczna jest wyłączona ([seal] enabled)'}
  try:
    doc = db_get_correspondence(id)
    if doc.get('status') not in ('Completed', 'Rejected'):
      return {'code': -4, 'message': 'Sprawa nie ma jeszcze odpowiedzi do opieczętowania'}
    instance = db.execute(
      select(ProcessInstance).where(ProcessInstance.business_key == id)
      .order_by(ProcessInstance.started_at.desc())
    ).scalars().first()
    if instance is None:
      return {'code': -4, 'message': 'Sprawa nie ma instancji procesu'}
    file_url = seal_case(id, instance.id, str(doc.get('status')), settings=settings,
                         requested_by=f'galwork-hub:{current_user.username}')
    return {'code': 0, 'message': 'OK', 'signed_document': file_url}
  except HTTPException:
    raise
  except Exception as e:
    db.rollback()
    logger.warning('jform/sprawa/seal %s: %s', id, e)
    return _error(e)
