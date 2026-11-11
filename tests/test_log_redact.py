# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# testy wycinania danych osobowych i tokenów z logów.

import logging

from core.log_redact import RedactAccessLogFilter, redact_query, safe_headers


def test_query_values_are_removed():
  assert (
    redact_query('/v1/edu/student/form?nr_albumu=01112&kierunek=INF')
    == '/v1/edu/student/form?nr_albumu=*&kierunek=*'
  )


def test_full_url_keeps_scheme_host_and_path():
  assert (
    redact_query('https://hub.example.org/v1/edu/oceny?email=jan%40example.org&pesel=44051401359')
    == 'https://hub.example.org/v1/edu/oceny?email=*&pesel=*'
  )


def test_url_without_query_is_unchanged():
  assert redact_query('/v1/edu/plan') == '/v1/edu/plan'
  assert redact_query('http://localhost:8000/health') == 'http://localhost:8000/health'


def test_blank_and_repeated_parameters():
  assert redact_query('/x?a=&a=2&b') == '/x?a=*&a=*&b=*'


def test_secret_headers_are_redacted():
  headers = safe_headers({
    'Authorization': 'Bearer eyJhbGciOiJSUzI1NiJ9.secret',
    'Cookie': 'sid=abc',
    'Content-Type': 'application/json',
    'X-User-Token': 'eyJ...',
    'User-Agent': 'Mozilla/5.0',
  })
  assert headers == {
    'authorization': '*',
    'cookie': '*',
    'content-type': 'application/json',
    'x-user-token': '*',
    'user-agent': 'Mozilla/5.0',
  }


def test_headers_as_pairs():
  assert safe_headers([('Accept', 'application/json'), ('X-Api-Key', 'k')]) == {
    'accept': 'application/json',
    'x-api-key': '*',
  }


def test_uvicorn_access_record_is_redacted():
  record = logging.LogRecord(
    'uvicorn.access', logging.INFO, __file__, 1,
    '%s - "%s %s HTTP/%s" %d',
    ('127.0.0.1:5000', 'GET', '/v1/edu/student/form?nr_albumu=01112', '1.1', 200),
    None,
  )
  assert RedactAccessLogFilter().filter(record) is True
  assert record.getMessage() == '127.0.0.1:5000 - "GET /v1/edu/student/form?nr_albumu=* HTTP/1.1" 200'


def test_other_records_pass_unchanged():
  record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, 'started', None, None)
  assert RedactAccessLogFilter().filter(record) is True
  assert record.getMessage() == 'started'


# oken subskrypcji kalendarza jest częścią ścieżki.
def test_calendar_feed_token_is_removed_from_path():
  assert redact_query('/v1/kalendarz/ics/Sekret-123_abc.ics') == '/v1/kalendarz/ics/*'
  assert redact_query('https://hub.example.com/v1/kalendarz/ics/Sekret.ics?x=1') == \
    'https://hub.example.com/v1/kalendarz/ics/*?x=*'
  assert redact_query('/v1/kalendarz/subskrypcja') == '/v1/kalendarz/subskrypcja'
