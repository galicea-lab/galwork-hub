# connector/webcon/config.py
# Reszta z modułu konfiguracji Webcon (resztę przeniesiono 
# do standardowych paraametrów Galwork: config.ini

from connector.base import BaseConnector

_cfg = BaseConnector.load_ini_section('webcon')


def _as_bool(value, default=False):
  if value is None:
    return default
  return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'tak')


dev = _as_bool(_cfg.get('dev'), False)

dbNo = _cfg.get('db_no', '1')

_prefix = 'dev_' if dev else ''
clientId = _cfg.get(_prefix + 'client_id', '') or ''
clientSecret = _cfg.get(_prefix + 'client_secret', '') or ''
baseUrl = (_cfg.get(_prefix + 'base_url', '') or '') + dbNo
loginUrl = _cfg.get(_prefix + 'login_url', '') or ''

sprawy_workflow = int(_cfg.get('sprawy_workflow', '0') or 0)
sprawy_processid = int(_cfg.get('sprawy_processid', '0') or 0)

formlarze_nieaktywne = [
    int(x) for x in (_cfg.get('formlarze_nieaktywne', '') or '').split(',') if x.strip()
]
