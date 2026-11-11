# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from zoneinfo import ZoneInfo

from config.config import section


def _as_tuple(value: str) -> tuple[str, ...]:
  return tuple(item.strip() for item in str(value).split(',') if item.strip())


@dataclass(frozen=True)
class KalendarzSettings:
  # strefa czasowa odpowiedzi i zdarzeń bez strefy
  timezone: str = 'Europe/Warsaw'
  # domyślny zakres (dni od `od`), gdy nie podano `do`
  domyslne_dni: int = 30
  # najdłuższy zakres jednego zapytania
  max_dni: int = 400
  # łączny limit czasu na zebranie źródeł (sekundy)
  timeout: float = 15.0
  # kalendarz ogólny uczelni - adresy ICS po przecinku (Google / M365 / inne)
  uczelnia_ics: tuple[str, ...] = ()
  uczelnia_nazwa: str = 'Kalendarz uczelni'
  # ważność pobranego pliku ICS w pamięci (sekundy)
  cache_ttl: int = 900
  # subskrypcja ICS (GET /v1/kalendarz/ics/<token>.ics):
  # publiczny adres modułu (np. https://hub.uczelnia.pl/v1/kalendarz) -
  # puste: z adresu żądania (za proxy uruchamiać z --proxy-headers)
  public_url: str = ''
  # adres portalu (galwork-edu) - odnośniki w opisach zdarzeń; puste = bez
  portal_url: str = ''
  # zakres kanału: dni wstecz i naprzód od dziś
  ics_dni_wstecz: int = 30
  ics_dni_naprzod: int = 180
  # gotowy plik kanału w pamięci (s) - klienci kalendarzy odpytują często
  ics_cache_ttl: int = 600

  @property
  def tz(self) -> ZoneInfo:
    return ZoneInfo(self.timezone)

  @classmethod
  def from_values(cls, values: dict) -> 'KalendarzSettings':
    kwargs = {}
    for f in fields(cls):
      if f.name not in values or values[f.name] in (None, ''):
        continue
      raw = values[f.name]
      if f.name == 'uczelnia_ics':
        kwargs[f.name] = _as_tuple(raw)
      elif f.type in ('int', int):
        kwargs[f.name] = int(raw)
      elif f.type in ('float', float):
        kwargs[f.name] = float(raw)
      else:
        kwargs[f.name] = str(raw).strip()
    settings = cls(**kwargs)
    settings.tz  # nieznana strefa = błąd przy starcie, nie przy pierwszym zapytaniu
    return settings

  @classmethod
  def load(cls) -> 'KalendarzSettings':
    values = dict(section('kalendarz'))
    for f in fields(cls):
      env_value = os.environ.get(f'KALENDARZ_{f.name.upper()}')
      if env_value is not None:
        values[f.name] = env_value
    return cls.from_values(values)


@lru_cache(maxsize=1)
def get_settings() -> KalendarzSettings:
  return KalendarzSettings.load()
