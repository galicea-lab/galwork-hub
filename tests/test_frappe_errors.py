# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# błędy konektora Frappe bez treści odpowiedzi w str(), z
# komunikatem dla użytkownika w user_message.

import json
import unittest

from connector.base import ConnectorError
from connector.frappe_education.connector import (
  FrappeEducationConnector,
  FrappeRequestError,
  error_message,
)

PESEL = '44051401458'


class Response:
  def __init__(self, status_code, body):
    self.status_code = status_code
    self.ok = 200 <= status_code < 300
    self._body = body
    self.text = body if isinstance(body, str) else json.dumps(body)

  def json(self):
    if isinstance(self._body, str):
      raise ValueError('not JSON')
    return self._body


def raised(resp, what='call_method(admissions_poland.portal.api.get_candidate)'):
  connector = FrappeEducationConnector.__new__(FrappeEducationConnector)
  try:
    connector._raise_for_status(resp, what)
  except FrappeRequestError as exc:
    return exc
  raise AssertionError('no error raised')


VALIDATION = {
  'exc_type': 'ValidationError',
  'exception': f'frappe.exceptions.ValidationError: Invalid PESEL: {PESEL}',
  'exc': '["Traceback (most recent call last):\\n  File ... pesel=' + PESEL + '"]',
  '_server_messages': json.dumps([json.dumps({
    'message': f'<strong>Invalid PESEL</strong>: {PESEL}', 'title': 'Message'})]),
}


class TestFrappeErrors(unittest.TestCase):
  def test_str_has_no_response_body(self):
    exc = raised(Response(417, VALIDATION))
    self.assertEqual(
      str(exc),
      'frappe_education call_method(admissions_poland.portal.api.get_candidate) -> 417 ValidationError')
    self.assertNotIn(PESEL, str(exc))
    self.assertNotIn('Traceback', str(exc))
    self.assertIsInstance(exc, ConnectorError)

  def test_details_are_attributes(self):
    exc = raised(Response(417, VALIDATION))
    self.assertEqual((exc.status_code, exc.exc_type), (417, 'ValidationError'))
    self.assertEqual(exc.user_message, f'Invalid PESEL: {PESEL}')

  def test_message_from_exception_without_server_messages(self):
    body = {'exc_type': 'DoesNotExistError',
            'exception': 'frappe.exceptions.DoesNotExistError: Student X not found'}
    exc = raised(Response(404, body))
    self.assertEqual(str(exc).split(' -> ')[1], '404 DoesNotExistError')
    self.assertEqual(exc.user_message, 'Student X not found')

  def test_non_json_response(self):
    exc = raised(Response(502, '<html>Bad Gateway</html>'))
    self.assertEqual(str(exc), 'frappe_education call_method(admissions_poland.portal.api.get_candidate) -> 502')
    self.assertIsNone(exc.user_message)

  def test_error_message_prefers_user_message_and_follows_causes(self):
    inner = raised(Response(417, VALIDATION))
    try:
      try:
        raise inner
      except ConnectorError as exc:
        raise ConnectorError(f'oceny.py: {exc}') from exc
    except ConnectorError as outer:
      self.assertEqual(error_message(outer), f'Invalid PESEL: {PESEL}')
      self.assertNotIn(PESEL, str(outer))

  def test_error_message_falls_back_to_str(self):
    self.assertEqual(error_message(ConnectorError('Frappe niedostępne')), 'Frappe niedostępne')

  def test_success_does_not_raise(self):
    connector = FrappeEducationConnector.__new__(FrappeEducationConnector)
    connector._raise_for_status(Response(200, {'message': 'ok'}), 'x')


if __name__ == '__main__':
  unittest.main()
