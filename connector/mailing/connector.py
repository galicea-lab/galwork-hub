# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Wysyłka e-maili przez SMTP - sekcja [mailing] w
# config/config.ini (server, port, login, password, sender).
#
# Port 465 - SMTP over TLS, inne porty - STARTTLS. backend = log (tylko
# środowisko deweloperskie) zapisuje treść do logu zamiast wysyłać.

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from connector.base import BaseConnector, ConnectorError


class MailError(ConnectorError):
  """Nieudane wysłanie e-maila (bez treści i adresata)."""


def _masked(email: str) -> str:
  local, _, domain = email.partition('@')
  return f'{local[:1]}…@{domain}' if domain else '…'


class MailConnector(BaseConnector):
  name = 'mailing'

  @property
  def backend(self) -> str:
    return (self.config.get('backend', 'smtp') or 'smtp').strip().lower()

  def connect(self) -> smtplib.SMTP:
    server = self.config.require('server')
    port = int(self.config.get('port', '587'))
    timeout = int(self.config.get('timeout', '15'))
    context = ssl.create_default_context()
    if port == 465:
      client = smtplib.SMTP_SSL(server, port, timeout=timeout, context=context)
    else:
      client = smtplib.SMTP(server, port, timeout=timeout)
      client.starttls(context=context)
    login = self.config.get('login')
    if login:
      client.login(login, self.config.require('password'))
    return client

  def test_connection(self) -> bool:
    if self.backend == 'log':
      return True
    try:
      self.connect().quit()
      return True
    except (OSError, smtplib.SMTPException, ConnectorError) as exc:
      self.logger.warning('mailing: test_connection() nieudany: %s', type(exc).__name__)
      return False

  def send(self, to: str, subject: str, text: str) -> None:
    if self.backend == 'log':
      self.logger.warning('E-mail (backend log) do %s: %s / %s', _masked(to), subject, text)
      return
    message = EmailMessage()
    sender = self.config.require('sender')
    message['From'] = sender
    message['To'] = to
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid()
    message.set_content(text)
    try:
      client = self.connect()
      try:
        client.send_message(message)
      finally:
        client.quit()
    except (OSError, smtplib.SMTPException) as exc:
      raise MailError(f'E-mail: nie wysłano ({type(exc).__name__})') from exc
    self.logger.info('E-mail wysłany do %s', _masked(to))


_connector: MailConnector | None = None


def get_connector() -> MailConnector:
  global _connector
  if _connector is None:
    _connector = MailConnector()
  return _connector
