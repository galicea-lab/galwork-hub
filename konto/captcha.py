# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Weryfikacja CAPTCHA przed wysłaniem kodu. Wspólny format
# siteverify (hCaptcha, Cloudflare Turnstile, reCAPTCHA): POST secret,
# response, remoteip, sitekey -> {"success": true|false}.

from __future__ import annotations

import logging

import requests

logger = logging.getLogger('konto.captcha')


class CaptchaVerifier:

  def __init__(self, verify_url: str, secret: str, timeout: int = 10, site_key: str = ''):
    self.verify_url = verify_url
    self.secret = secret
    self.timeout = timeout
    # hCaptcha zaleca przekazanie sitekey - odrzuca wtedy token z innej witryny
    self.site_key = site_key

  @property
  def enabled(self) -> bool:
    return bool(self.verify_url and self.secret)

  def verify(self, token: str | None, client_ip: str | None) -> bool:
    if not self.enabled:
      return True
    if not token:
      return False
    data = {'secret': self.secret, 'response': token}
    if client_ip:
      data['remoteip'] = client_ip
    if self.site_key:
      data['sitekey'] = self.site_key
    try:
      resp = requests.post(self.verify_url, data=data, timeout=self.timeout)
      return bool(resp.ok and resp.json().get('success'))
    except (requests.RequestException, ValueError) as exc:
      logger.warning('CAPTCHA: weryfikacja nieudana (%s)', type(exc).__name__)
      return False
