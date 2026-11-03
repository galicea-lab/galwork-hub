#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
import datetime
import sqlite3
from pydantic import BaseModel
from typing import List, Optional, Any

select_edoreczenia="""SELECT
	WFD_ID, EDR_ID, STATUS
 FROM  edoreczenia"""

insert_edoreczenia="""INSERT INTO edoreczenia( WFD_ID, EDR_ID, STATUS )
 VALUES(%s,'%s','%s')
"""


# Funkcja do połączenia z bazą danych i stworzenia tabeli, jeśli nie istnieje
def init_db(db_name="api.db"):
  conn = sqlite3.connect(db_name)
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS edoreczenia (
            WFD_ID INTEGER,
            EDR_ID TEXT,
            STATUS TEXT
        );
    """)
  conn.commit()
  conn.close()

class EDR_Rejestr(BaseModel):
  WFD_ID: int
  WDR_ID: str
  STATUS: str

def sql_select_edr(wfd_id):
  return select_edoreczenia+" where WFD_ID=%s" % wfd_id

def sql_insert_edr(wfd_id, edr_id, status):
  return insert_edoreczenia % (wfd_id, edr_id, status)

def db_get_edr(wfd_id, db_name="api.db") -> EDR_Rejestr:
  try:
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute(sql_select_edr(wfd_id))
    edr=cursor.fetchone()
    conn.close()
    return edr
  except Exception as e:
    return None


def db_insert_edr(wfd_id,edr_id,status, db_name="api.db"):
  try:
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    result = cursor.execute(sql_insert_edr(wfd_id,edr_id,status))
    conn.commit()
    conn.close()
    return 'OK'
  except Exception as e:
    return None

if __name__ == "__main__":
  print(db_insert_edr(1,'edr_id','status'))
  print(db_get_edr(1))
