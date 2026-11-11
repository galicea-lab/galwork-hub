# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Konfiguracja usługi kodów jednorazowych - sekcja [konto]
# w config/config.ini (zmienne środowiskowe KONTO_<KLUCZ> mają pierwszeństwo).

from __future__ import annotations

import os
from dataclasses import dataclass, fields

from config.config import section


class KontoConfigError(Exception):
  """Brak albo niepoprawna konfiguracja usługi kodów."""


PESEL_MODES = ('off', 'optional', 'required')
USERNAME_MODES = ('email', 'phone')
CHANNELS = ('sms', 'email')


def as_bool(value: str | None) -> bool:
  return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'tak')


def _as_tuple(value: str) -> tuple[str, ...]:
  return tuple(item.strip() for item in str(value).split(',') if item.strip())


@dataclass(frozen=True)
class KontoSettings:
  # kanały dostarczania kodów, w kolejności preferencji przy resecie
  channels: tuple[str, ...] = ('sms', 'email')
  # numery kierunkowe, dla których wysyłamy SMS (bez '+')
  sms_country_codes: tuple[str, ...] = ('48',)
  # numer kierunkowy dla numerów wpisanych bez prefiksu
  default_country_code: str = '48'
  # ile kont może mieć ten sam numer telefonu
  accounts_per_phone: int = 1
  # pula operacji z kodem (założenie konta + odzyskania) na kanał i konto
  operations_quota: int = 3
  code_length: int = 6
  code_ttl: int = 600
  max_attempts: int = 5
  # maks. liczba wysłań kodu w ramach jednej operacji (z ponowieniami)
  max_sends: int = 3
  resend_interval: int = 60
  tickets_per_destination_per_day: int = 5
  tickets_per_ip_per_hour: int = 10
  # globalny dzienny limit SMS (ochrona kosztów)
  sms_per_day: int = 1000
  pesel: str = 'off'
  username: str = 'email'
  # grupa Keycloak nowych kont (ścieżka)
  group: str = '/kandydaci'
  # klucz HMAC skrótów kodów, kanałów, adresów IP i PESEL-u
  secret: str = ''
  captcha_verify_url: str = ''
  captcha_secret: str = ''
  captcha_site_key: str = ''
  # adres logowania Keycloak / portalu, zwracany po założeniu konta
  login_url: str = ''
  sms_template: str = 'Kod: {code}. Ważny {minutes} min. Nie przekazuj go nikomu.'
  mail_subject: str = 'Kod jednorazowy'
  mail_template: str = ('Twój kod jednorazowy: {code}\n\n'
                        'Kod jest ważny {minutes} min. Jeśli to nie Ty - zignoruj tę wiadomość.\n')
  reset_notice_subject: str = 'Zmieniono hasło'
  reset_notice_template: str = ('Hasło do Twojego konta zostało zmienione. '
                                'Jeśli to nie Ty - skontaktuj się z komisją rekrutacyjną.\n')
  # montowanie routera w głównej aplikacji huba (/v1/konto); zalecane
  # osobne wdrożenie: uvicorn konto.app:app
  mount_in_hub: bool = False

  @classmethod
  def from_values(cls, values: dict) -> 'KontoSettings':
    kwargs = {}
    for f in fields(cls):
      if f.name not in values or values[f.name] is None:
        continue
      raw = values[f.name]
      default = f.default
      try:
        if isinstance(default, bool):
          kwargs[f.name] = as_bool(raw)
        elif isinstance(default, int):
          kwargs[f.name] = int(raw)
        elif isinstance(default, tuple):
          kwargs[f.name] = _as_tuple(raw)
        else:
          kwargs[f.name] = str(raw).replace('\\n', '\n')
      except ValueError as exc:
        raise KontoConfigError(f'[konto] {f.name}: niepoprawna wartość') from exc
    settings = cls(**kwargs)
    settings.validate()
    return settings

  @classmethod
  def raw_values(cls) -> dict:
    """Surowe wartości [konto] bez walidacji."""
    values = dict(section('konto'))
    # zmienne KONTO_<KLUCZ> także dla kluczy nieobecnych w pliku (np. KONTO_SECRET)
    for f in fields(cls):
      env_value = os.environ.get(f'KONTO_{f.name.upper()}')
      if env_value is not None:
        values[f.name] = env_value
    return values

  @classmethod
  def load(cls) -> 'KontoSettings':
    return cls.from_values(cls.raw_values())

  def validate(self) -> None:
    unknown = set(self.channels) - set(CHANNELS)
    if unknown or not self.channels:
      raise KontoConfigError(f'[konto] channels: dozwolone {", ".join(CHANNELS)}')
    if self.pesel not in PESEL_MODES:
      raise KontoConfigError(f'[konto] pesel: dozwolone {", ".join(PESEL_MODES)}')
    if self.username not in USERNAME_MODES:
      raise KontoConfigError(f'[konto] username: dozwolone {", ".join(USERNAME_MODES)}')
    if not 4 <= self.code_length <= 10:
      raise KontoConfigError('[konto] code_length: 4-10')

  def require_secret(self) -> bytes:
    if len(self.secret) < 32:
      raise KontoConfigError('[konto] secret: wymagany klucz co najmniej 32 znaki '
                             '(zmienna KONTO_SECRET)')
    return self.secret.encode()


# 2026-11-10: przełącznik montowania w hubie czytany jak reszta [konto]
# (KONTO_MOUNT_IN_HUB działa także bez sekcji w pliku), bez walidacji
# pozostałych ustawień - błąd konfiguracji kodów nie blokuje startu huba.
def mount_in_hub() -> bool:
  return as_bool(KontoSettings.raw_values().get('mount_in_hub'))
