#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
import datetime
import logging
import pymssql
from pydantic import BaseModel
from typing import List, Optional, Any


from connector.base import ConnectorConfigError, ConnectorError

log = logging.getLogger("connector.webcon.edr_db")

_EDR_DB = {
  "host": "edr_db_host",
  "user": "edr_db_user",
  "password": "edr_db_password",
  "database": "edr_db_database",
}


def _connect_edr():
  """Połączenie z bazą e-Doręczeń (MSSQL) z sekcji [edr_db]."""
  import configparser
  import os

  parser = configparser.ConfigParser()
  parser.read(os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.ini"))
  params, missing = {}, []
  for ini_key, env_key in _EDR_DB.items():
    value = os.environ.get(env_key.upper()) or (
      parser["edr_db"].get(ini_key) if parser.has_section("edr_db") else None)
    if not value:
      missing.append(ini_key)
      continue
    params[ini_key] = value
  if missing:
    raise ConnectorConfigError(
      "Brak konfiguracji bazy e-Doręczeń w sekcji [edr_db] config/config.ini: "
      + ", ".join(missing) + " (lub zmienne EDR_DB_*).")
  try:
    return pymssql.connect(**params)
  except Exception as exc:
    log.error("e-Doręczenia: nie można połączyć się z %s/%s: %s",
              params["host"], params["database"], exc)
    raise ConnectorError(
      f"e-Doręczenia: nie można połączyć się z bazą {params['database']} "
      f"na {params['host']}: {exc}") from exc

select_edoreczenia="""SELECT
	WFD_ID, EDR_ID, STATUS
 FROM  edoreczenia"""

insert_edoreczenia="""INSERT INTO edoreczenia( WFD_ID, EDR_ID, STATUS )
 VALUES(%s,'%s','%s')
"""

class EDR_Rejestr(BaseModel):
  WFD_ID: int
  WDR_ID: str
  STATUS: str

def sql_select_edr(wfd_id):
  return select_edoreczenia+" where WFD_ID=%s" % wfd_id

def sql_insert_edr(wfd_id, edr_id, status):
  return insert_edoreczenia % (wfd_id, edr_id, status)

def db_get_edr(wfd_id) -> EDR_Rejestr:
  conn = _connect_edr()
  try:
    cursor = conn.cursor()
    cursor.execute(sql_select_edr(wfd_id))
    return cursor.fetchone()
  except Exception as exc:
    log.error("e-Doręczenia: db_get_edr(%s): %s", wfd_id, exc)
    raise ConnectorError(f"e-Doręczenia: db_get_edr({wfd_id}): {exc}") from exc
  finally:
    conn.close()


def db_insert_edr(wfd_id,edr_id,status):
  conn = _connect_edr()
  try:
    cursor = conn.cursor()
    cursor.execute(sql_insert_edr(wfd_id,edr_id,status))
    conn.commit()
    return 'OK'
  except Exception as exc:
    log.error("e-Doręczenia: db_insert_edr(%s): %s", wfd_id, exc)
    raise ConnectorError(f"e-Doręczenia: db_insert_edr({wfd_id}): {exc}") from exc
  finally:
    conn.close()

if __name__ == "__main__":
  print(db_insert_edr(1,'edr_id','status'))
  print(db_get_edr(1))
