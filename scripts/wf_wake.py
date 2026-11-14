#!/usr/bin/env python
# coding: utf-8
"""
2026-11-13: budzenie instancji procesów z krokiem systemowym czekającym
na wynik (`serviceTask` z `handler`, np. podpis w Signius) - instancje
z minionym `ProcessInstance.next_wakeup_at` (scripts/cron_wf.sh).

Użytkownik crona: `[wf] system_user` (nazwa albo e-mail z tabeli users),
a bez niej `[edr] system_user` - kroki systemowe zapisują dokumenty
i zdarzenia z jego `users.id`.

Użycie:
    python3 scripts/wf_wake.py            # wszystkie zaległe instancje
    python3 scripts/wf_wake.py --list     # tylko wypisz zaległe
"""
import argparse
import os
import sys
from datetime import UTC, datetime

currentdir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(currentdir))

from sqlalchemy import select  # noqa: E402

from connector.base import BaseConnector  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from models.user import User  # noqa: E402
from wf.models import ProcessInstance  # noqa: E402
from wf.schemas.wf_schemas import CurrentUser  # noqa: E402
from wf.services.process_service import get_service  # noqa: E402


def _system_user(db) -> CurrentUser:
  login = ''
  for section in ('wf', 'edr'):
    login = (BaseConnector.load_ini_section(section).get('system_user') or '').strip()
    if login:
      break
  user = db.execute(select(User).where((User.name == login) | (User.email == login))
                    ).scalars().first() if login else None
  if user is None:
    sys.exit("[wf_wake] ustaw [wf] system_user (albo [edr] system_user) - użytkownik z tabeli users")
  return CurrentUser(id=user.id, sub=user.keycloak_sub or 'sub-wf', username=user.name,
                     email=user.email, display_name='Cron obiegów', groups=frozenset())


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('--list', action='store_true', help='tylko wypisz zaległe instancje')
  args = parser.parse_args()
  db = SessionLocal()
  try:
    now = datetime.now(UTC)
    if args.list:
      for instance in db.execute(select(ProcessInstance).where(
          ProcessInstance.next_wakeup_at.is_not(None),
          ProcessInstance.next_wakeup_at <= now)).scalars():
        print(f"{instance.id}  {instance.next_wakeup_at:%F %T}  {instance.title}")
      return 0
    woken = get_service().wake_due(db, _system_user(db), now)
    for instance in woken:
      state = f"czeka do {instance.next_wakeup_at:%F %T}" if instance.next_wakeup_at else instance.status
      print(f"[wf_wake] {instance.id} {instance.title}: {state}"
            + (f" - {instance.error_message}" if instance.error_message else ''))
    print(f"[wf_wake] obudzono {len(woken)} instancji")
    return 0
  finally:
    db.close()


if __name__ == '__main__':
  sys.exit(main())
