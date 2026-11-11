# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Usługa kodów - przepustki (wysłane kody) i wykonane
# operacje (pula). Bez danych jawnych: kanał, kod i adres IP tylko jako
# skróty HMAC (konto.policy.keyed_hash).

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class KontoTicket(Base):
  __tablename__ = 'konto_ticket'

  id: Mapped[str] = mapped_column(String(64), primary_key=True)
  # 'register' | 'reset'
  purpose: Mapped[str] = mapped_column(String(16))
  # 'sms' | 'email'
  channel: Mapped[str] = mapped_column(String(8))
  destination_hash: Mapped[str] = mapped_column(String(64), index=True)
  # identyfikator użytkownika Keycloak (reset)
  user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
  # NULL - przepustka pozorna (reset nieistniejącego konta), żaden kod nie pasuje
  code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
  ip_hash: Mapped[str] = mapped_column(String(64), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
  expires_at: Mapped[datetime] = mapped_column(DateTime)
  last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  sends: Mapped[int] = mapped_column(Integer, default=0)
  attempts: Mapped[int] = mapped_column(Integer, default=0)
  completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KontoOperation(Base):
  __tablename__ = 'konto_operation'

  id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
  purpose: Mapped[str] = mapped_column(String(16))
  destination_hash: Mapped[str] = mapped_column(String(64), index=True)
  user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime)


# sesja weryfikacji przez login.gov.pl. Imię i nazwisko z WK
# przechowywane tylko do zakończenia operacji (maks. kilkanaście minut),
# potem czyszczone; identyfikator osoby tylko jako skrót HMAC.
class KontoWkSession(Base):
  __tablename__ = 'konto_wk_session'

  # RelayState
  id: Mapped[str] = mapped_column(String(64), primary_key=True)
  # 'register' | 'reset'
  purpose: Mapped[str] = mapped_column(String(16))
  authn_request_id: Mapped[str] = mapped_column(String(64))
  ip_hash: Mapped[str] = mapped_column(String(64), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
  expires_at: Mapped[datetime] = mapped_column(DateTime)
  # po poprawnej odpowiedzi WK
  ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
  verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  # 'pesel' | 'eid'
  identity_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
  identity_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
  first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
  last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
  completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
