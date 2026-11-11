#!/usr/bin/env python
# coding: utf-8
"""
e-Doręczenia - zadania cykliczne (scripts/cron_edr.sh).

  inbox      nowe wiadomości z EDR -> instancje `edr-korespondencja` + Paperless-ngx
  resubmit   ponowna wysyłka do Paperless-ngx dokumentów w stanie `staged`
  poll       wynik ingestu w Paperless-ngx (`submitted` -> `done`/`failed`)
  send       kolejka wysyłki: otwarte zadania "Wysyłka EDR" procesu `edr-wysylka`
  evidences  potwierdzenia (UPO) dla spraw w toku, przychodzących i wysłanych;
             zamyka "Potwierdzenie doręczenia" po dowodzie doręczenia
  test       wyszukiwanie w rejestrze adresów do doręczeń (diagnostyka)
"""
# 2026-11-08: `send` obsługuje proces edr-wysylka (connector/edoreczenia/edr_paperless.py::send_queued),
# `test` używa EdrConnector z connector/edoreczenia/edr_connector.py.
import argparse
import json
import os
import sys

currentdir = os.path.dirname(os.path.abspath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

from sqlalchemy import select  # noqa: E402

from connector.base import BaseConnector, ConnectorConfigError, ConnectorError  # noqa: E402
from connector.edoreczenia import edr_paperless  # noqa: E402
from connector.edoreczenia.edr_connector import EdrConnector  # noqa: E402
from connector.paperless.connector import get_connector as get_paperless_connector  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from models.user import User  # noqa: E402
from wf.schemas.wf_schemas import CurrentUser  # noqa: E402

_edr_config = BaseConnector.load_ini_section('edr')

#: 2026-11-08: grupy użytkownika systemowego crona. `edr-system` to
#: kandydat zadania "Wysyłka EDR" w spiffworkflow/kancelaria/edr-wysylka.
SYSTEM_GROUPS = ('tenar', edr_paperless.SEND_GROUP)


def test_search(test=False):
  edrc = EdrConnector(test)
  queries = (
    dict(pesel='', nip='', regon='', krs='0000462569', name='', address={}),
    dict(pesel='', nip='6762455185', regon='', krs='', name='', address={}),
    dict(pesel='', nip='', regon='122557001', krs='', name='', address={}),
    dict(pesel='', nip='', regon='', krs='',
         name='PODKARPACKA FUNDACJA WSPIERANIA INNOWACJI "GALICEA"',
         address={
           "countryCode": "PL",
           "city": "Jarosław",
           "buildingNumber": "2",
           "street": "OS. OSIEDLE NIEPODLEGŁOŚCI",
           "flatNumber": "16",
           "postalCode": "37-500",
         }),
  )
  for query in queries:
    result = edrc.edr.SzukajInstytucji(**query)
    if result.data:
      print(json.loads(result.data))


def _user(db) -> CurrentUser:
  """2026-11-08: użytkownik, w imieniu którego działa cron.

  `[edr] system_user` (nazwa albo e-mail z tabeli `users`) - wtedy zdarzenia
  i zadania mają prawdziwy `users.id` (klucze obce w PostgreSQL). Bez tej
  opcji zostaje dotychczasowe id=0."""
  login = (_edr_config.get('system_user') or '').strip()
  user = None
  if login:
    user = db.execute(
      select(User).where((User.name == login) | (User.email == login))
    ).scalars().first()
    if user is None:
      print(f"[edr] system_user={login!r} nie istnieje w tabeli users - używam id=0")
  return CurrentUser(
    id=user.id if user else 0,
    sub=(user.keycloak_sub if user else None) or 'sub-edr',
    username=(user.name if user else None) or 'cron',
    email=(user.email if user else None) or 'cron@example.org',
    display_name='Cron e-Doręczenia',
    groups=frozenset(SYSTEM_GROUPS),
  )


def _run(cmd: str, test: bool) -> None:
  db = SessionLocal()
  try:
    if cmd == 'inbox':
      # Nowe wiadomości -> instancje `edr-korespondencja` (model musi być
      # opublikowany, zob. scripts/seed_edr_process.py) i ingest do Paperless-ngx.
      # Wynik ingestu odczytuje `poll`.
      paperless = get_paperless_connector()
      instances = edr_paperless.sync_inbox(db, paperless, actor=_user(db), test=test)
      db.commit()
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      print(f"inbox: {len(instances)} nowych spraw, {submitted} dokumentów wysłanych do Paperless-ngx")
    elif cmd == 'resubmit':
      # Dokumenty `staged` już w bazie (np. po błędzie Paperless-ngx),
      # bez odpytywania e-Doręczeń.
      paperless = get_paperless_connector()
      instances = edr_paperless.pending_staged_instances(db)
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      print(f"resubmit: {len(instances)} spraw z dokumentami do wysyłki, "
            f"{submitted} dokumentów wysłanych do Paperless-ngx")
    elif cmd == 'poll':
      # Ingest w Paperless-ngx jest asynchroniczny - wołać po `inbox`.
      handled = edr_paperless.poll_submitted_documents(
        db, get_paperless_connector(), actor=_user(db))
      print(f"poll: {handled} dokumentów obsłużonych")
    elif cmd == 'send':
      result = edr_paperless.send_queued(
        db, get_paperless_connector(), actor=_user(db), test=test)
      print(f"send: {result.sent} wysłanych, {result.failed} do wyjaśnienia "
            f"(zadanie \"Problem z wysyłką\"), {result.deferred} odłożonych do ponowienia")
    elif cmd == 'evidences':
      paperless = get_paperless_connector()
      actor = _user(db)
      fetched = edr_paperless.fetch_evidences(db, paperless, actor=actor, test=test)
      submitted = edr_paperless.submit_staged_documents(db, paperless)
      # 2026-11-08: sprawy z potwierdzeniem doręczenia zamykają się same.
      delivered = edr_paperless.complete_delivered(db, actor=actor)
      print(f"evidences: {fetched} nowych potwierdzeń, {submitted} dokumentów wysłanych "
            f"do Paperless-ngx, {delivered} przesyłek doręczonych")
  finally:
    db.close()


COMMANDS = ('inbox', 'resubmit', 'poll', 'send', 'evidences', 'test')


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='e-Doręczenia')
  parser.add_argument('cmd', choices=COMMANDS, help='operacja: ' + ' '.join(COMMANDS))
  parser.add_argument('--test', action='store_true',
                      help='środowisko testowe e-Doręczeń ([edr] test_address/test_key)')
  args = parser.parse_args()
  if args.cmd == 'test':
    test_search(args.test)
    sys.exit(0)
  # Cron nie powinien dostawać kodu błędu przy chwilowych problemach z
  # EDR/Paperless-ngx - komunikat trafia do cron_edr.log.
  try:
    _run(args.cmd, args.test)
  except ConnectorConfigError as exc:
    print(f"{args.cmd}: Paperless-ngx/EDR nie skonfigurowany: {exc}")
  except ConnectorError as exc:
    print(f"{args.cmd}: EDR/Paperless-ngx: {exc}")
  except Exception as exc:
    print(f"{args.cmd}: Błąd: {exc}")
  sys.exit(0)
