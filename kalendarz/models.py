# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Subskrypcja kalendarza (kanał ICS). Jedna na użytkownika;
# w bazie tylko skrót SHA-256 tokenu - sam token użytkownik widzi raz, przy
# wygenerowaniu. Nowy token zastępuje poprzedni (stary adres przestaje działać).

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class KalendarzSubskrypcja(Base):
  __tablename__ = 'kalendarz_subskrypcja'

  id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
  user_id: Mapped[int] = mapped_column(
    Integer, ForeignKey('users.id', ondelete='CASCADE'), unique=True, index=True)
  token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
  # 'pelna' | 'minimalna' - minimalna: bez kwot, opisów spraw i szczegółów
  szczegolowosc: Mapped[str] = mapped_column(String(16), default='minimalna')
  # rola, dla której zbierane są terminy akademickie (None - ustalana z Education)
  rola: Mapped[str | None] = mapped_column(String(16), nullable=True)
  utworzono: Mapped[datetime] = mapped_column(DateTime)
  ostatnie_pobranie: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  liczba_pobran: Mapped[int] = mapped_column(Integer, default=0)
