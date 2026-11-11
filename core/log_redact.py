# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Logi huba bez danych osobowych i tokenów. Do logu trafia
# metoda, ścieżka i nazwy parametrów zapytania (bez wartości), nagłówki tylko
# z listy dozwolonych i rozmiar treści, nigdy sama treść ani nagłówek
# Authorization / Cookie.

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit

# Nagłówki bez danych osobowych i sekretów, które wolno logować.
SAFE_HEADERS = frozenset({
  'accept',
  'accept-encoding',
  'accept-language',
  'content-length',
  'content-type',
  'host',
  'origin',
  'user-agent',
  'x-request-id',
})

REDACTED = '*'

#  sekrety w ścieżce (adres subskrypcji kalendarza zawiera
# token zamiast nagłówka Authorization - klienci kalendarzy nie wysyłają
# nagłówków). Segment po prefiksie jest zastępowany `*`.
SECRET_PATH = re.compile(r'(/kalendarz/ics/)[^/?#]+')


def redact_query(target: str) -> str:
  """Ścieżka (albo URL) z wartościami parametrów zapytania zastąpionymi `*`."""
  parts = urlsplit(target)
  path = SECRET_PATH.sub(rf'\g<1>{REDACTED}', parts.path or '/')
  if parts.scheme:
    path = f'{parts.scheme}://{parts.netloc}{path}'
  if not parts.query:
    return path
  names = [name for name, _value in parse_qsl(parts.query, keep_blank_values=True)]
  return f"{path}?{'&'.join(f'{name}={REDACTED}' for name in names)}"


def safe_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
  """Nagłówki z listy dozwolonych; pozostałe tylko z nazwą."""
  items = headers.items() if isinstance(headers, Mapping) else headers
  return {
    name.lower(): (value if name.lower() in SAFE_HEADERS else REDACTED)
    for name, value in items
  }


class RedactAccessLogFilter(logging.Filter):
  """Usuwa wartości parametrów zapytania z logu dostępu Uvicorn.

  Rekord `uvicorn.access` ma argumenty
  (adres klienta, metoda, ścieżka z zapytaniem, wersja HTTP, status).
  """

  def filter(self, record: logging.LogRecord) -> bool:
    args = record.args
    if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
      record.args = (*args[:2], redact_query(args[2]), *args[3:])
    return True
