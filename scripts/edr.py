#!/usr/bin/env python
# coding: utf-8
"""
e-Doręczenia
"""
import argparse
import json
import os
import sys
import inspect

from fastapi import HTTPException

from connector.base import ConnectorConfigError, ConnectorError
from db.session import SessionLocal
from wf.schemas.wf_schemas import CurrentUser, EdrSyncInboxResponse

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = currentdir
parentdir = os.path.dirname(parentdir)
sys.path.insert(0, parentdir)

from connector.edoreczenia.edr_webcon import inbox_to_webcon, send_from_webcon, EdrConnector, \
  fetch_evidences_att

from connector.edoreczenia import edr_paperless
from connector.paperless.connector import get_connector as get_paperless_connector


def test_search(test=False):
  edrc=EdrConnector(test)
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='', krs='0000462569', name='', address={})
  if result.data:
    print(json.loads(result.data))
  result=edrc.edr.SzukajInstytucji(pesel='', nip='6762455185', regon='', krs='', name='', address={})
  if result.data:
    print(json.loads(result.data))
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='122557001', krs='', name='', address={})
  if result.data:
    print(json.loads(result.data))
  result=edrc.edr.SzukajInstytucji(pesel='', nip='', regon='', krs='', name='PODKARPACKA FUNDACJA WSPIERANIA INNOWACJI "GALICEA"', address={
    "countryCode":"PL",
    "city":"Jarosław",
    "buildingNumber":"2",
    "street":"OS. OSIEDLE NIEPODLEGŁOŚCI",
    "flatNumber":"16",
    "postalCode":"37-500",})
  if result.data:
    print(json.loads(result.data))



def _user(*groups: str) -> CurrentUser:
  return CurrentUser(
    id=0,
    sub='sub-edr',
    username='cron',
    email='cron@example.org',
    display_name='Cron',
    groups=frozenset(groups),
  )

if __name__ == "__main__":
  test=False
  parser = argparse.ArgumentParser(description='e-Doręczenia')
  parser.add_argument('cmd', help='operacja: send inbox resubmit poll evidences test')
  args = parser.parse_args()
  if args.cmd == 'inbox':
    #inbox_to_webcon(test)
    """Pobiera nowe wiadomości z EDR i startuje dla nich instancje procesu
    `edr-korespondencja` (musi być opublikowana - zob.
    `scripts/seed_edr_process.py`), a następnie wysyła zaseedowane
    (`staged`) dokumenty do Paperless-ngx (`submit_staged_documents`).
    `poll` (osobna komenda, wołana z crona po chwili) odczytuje wynik
    zadania i ustawia `paperless_document_id`."""
    try:
      db = SessionLocal()
      user=_user('tenar')
      paperless = get_paperless_connector()
      ### test:
      #import connector.edoreczenia.edr_paperless
      #edr_paperless.test_edr_to_paperless(db, paperless, actor=user, test=test)
#      exit(0)
      instances = edr_paperless.sync_inbox(db, paperless, actor=user, test=test)
      db.commit()
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      print(f"inbox: {len(instances)} nowych spraw, {submitted} dokumentów wysłanych do Paperless-ngx")
    except ConnectorConfigError as exc:
      print(f"Paperless-ngx nie skonfigurowany: {exc}")
      exit(0)
    except ConnectorError as exc:
      print(f"EDR/Paperless-ngx: {exc}")
      exit(0)
    except Exception as exc:
      print(f"Błąd: {exc}")
      exit(0)
    #sync=EdrSyncInboxResponse(
    #    created=len(instances), process_instance_ids=[instance.id for instance in instances])
    #print(instances)
    exit(0)
  elif args.cmd == 'resubmit':
    """Dosyła do Paperless-ngx dokumenty, które już są w bazie w stanie
    `staged` (np. po wcześniejszym błędzie Paperless-ngx) - bez
    odpytywania e-Doręczeń (`sync_inbox`)."""
    try:
      db = SessionLocal()
      paperless = get_paperless_connector()
      instances = edr_paperless.pending_staged_instances(db)
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      print(f"resubmit: {len(instances)} spraw z dokumentami do wysyłki, {submitted} dokumentów wysłanych do Paperless-ngx")
    except ConnectorConfigError as exc:
      print(f"Paperless-ngx nie skonfigurowany: {exc}")
      exit(0)
    except ConnectorError as exc:
      print(f"EDR/Paperless-ngx: {exc}")
      exit(0)
    except Exception as exc:
      print(f"Błąd: {exc}")
      exit(0)
    exit(0)
  elif args.cmd == 'poll':
    """Odpytuje Paperless-ngx o wynik wcześniej wysłanych (`submitted`)
    dokumentów - trzeba wołać z crona PO `inbox`, bo ingest jest
    asynchroniczny (zob. docstring `edr_paperless.poll_submitted_documents`)."""
    try:
      db = SessionLocal()
      user=_user('tenar')
      handled = edr_paperless.poll_submitted_documents(db, get_paperless_connector(), actor=user)
      print(f"poll: {handled} dokumentów obsłużonych")
    except ConnectorConfigError as exc:
      print(f"Paperless-ngx nie skonfigurowany: {exc}")
      exit(0)
    except ConnectorError as exc:
      print(f"EDR/Paperless-ngx: {exc}")
      exit(0)
    exit(0)
  elif args.cmd == 'evidences':
    """Dociąga potwierdzenia (evidences) dla oczekujących spraw i wysyła
    zaseedowane dokumenty do Paperless-ngx - analogicznie do `inbox`
    (zob. `edr_paperless.fetch_evidences`/`submit_staged_documents`)."""
    try:
      db = SessionLocal()
      user=_user('tenar')
      paperless = get_paperless_connector()
      fetched = edr_paperless.fetch_evidences(db, paperless, actor=user, test=test)
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      print(f"evidences: {fetched} nowych potwierdzeń, {submitted} dokumentów wysłanych do Paperless-ngx")
    except ConnectorConfigError as exc:
      print(f"Paperless-ngx nie skonfigurowany: {exc}")
      exit(0)
    except ConnectorError as exc:
      print(f"EDR/Paperless-ngx: {exc}")
      exit(0)
    except Exception as exc:
      print(f"Błąd: {exc}")
      exit(0)
    exit(0)
  elif args.cmd == 'send':
    print('send')
    send_from_webcon(test)
    exit(0)
  elif args.cmd == 'test':
    test_search(test)
    exit(0)
  parser.print_help()

