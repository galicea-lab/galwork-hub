# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-10: terminy zadań w obiegach (HumanTask.due_at).
# Widoczność jak w skrzynce "Moje zadania" (wf/routers/human_task_router.py):
# zadania podjęte przez użytkownika lub osobę, którą zastępuje, oraz otwarte,
# w których jest wskazanym kandydatem (login, grupa, rola). W odróżnieniu od
# skrzynki - bez otwartych zadań bez kandydatów: są widoczne dla każdego i
# zasypałyby kalendarz wszystkim użytkownikom.

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from kalendarz.schemas import Zdarzenie, ZdarzenieProps
from kalendarz.zrodla import Kontekst, Zrodlo, czas_lokalny, poczatek_dnia
from wf.models import HumanTask, HumanTaskCandidate
from wf.schemas.wf_schemas import HumanTaskStatus

AKTYWNE = (HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED)


class ZadaniaZrodlo(Zrodlo):
  id = 'zadania'
  nazwa = 'Terminy zadań'
  opis = 'Terminy wykonania zadań w obiegach, w których bierze udział użytkownik'

  def dotyczy(self, ctx: Kontekst) -> bool:
    return ctx.db is not None

  def zdarzenia(self, ctx: Kontekst, od: date, do: date) -> list[Zdarzenie]:
    user = ctx.current_user
    acting_ids = tuple(user.acting_user_ids) or (0,)
    principals = tuple({user.username} | set(user.groups) | set(user.roles)) or ('',)
    od_utc = poczatek_dnia(od, ctx.strefa).astimezone(UTC)
    do_utc = poczatek_dnia(do + timedelta(days=1), ctx.strefa).astimezone(UTC)

    stmt = (
      select(HumanTask)
      .options(selectinload(HumanTask.process_instance))
      .where(
        HumanTask.status.in_(AKTYWNE),
        HumanTask.due_at.is_not(None),
        HumanTask.due_at >= od_utc,
        HumanTask.due_at < do_utc,
        or_(
          HumanTask.claimed_by_id.in_(acting_ids),
          and_(
            HumanTask.status == HumanTaskStatus.OPEN,
            HumanTask.candidates.any(HumanTaskCandidate.principal_value.in_(principals)),
          ),
        ),
      )
      .order_by(HumanTask.due_at)
    )
    teraz = datetime.now(UTC)
    wynik = []
    for task in ctx.db.execute(stmt).scalars().all():
      due = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)
      proces = task.process_instance.title if task.process_instance else None
      wynik.append(Zdarzenie(
        id=f'zadania:{task.id}',
        title=f'Termin: {task.name}',
        start=czas_lokalny(due, ctx.strefa),
        extendedProps=ZdarzenieProps(
          zrodlo=self.id,
          typ='termin_zadania',
          ref=f'/tasks/{task.id}',
          opis=proces,
          szczegoly={
            'zadanie': task.name,
            'proces': proces,
            'status': str(task.status),
            'podjete_przeze_mnie': task.claimed_by_id in user.acting_user_ids,
            'po_terminie': due < teraz,
          },
        ),
      ))
    return wynik
