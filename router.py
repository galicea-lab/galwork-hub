#!/usr/bin/env python3
# coding: utf-8
# authors: Jerzy Wawro
# (C) 2026

from fastapi import FastAPI, APIRouter
from fastapi import Depends
from api.auth import router as router_auth

from config.config import settings

from pm.routers.calc import router as router_calc
from pm.routers.dashboard import router as router_dashboard
from pm.routers.forms import router as router_forms
from pm.routers.projects import router as router_projects


from wf.routers import (
    router_comments as router_wf_comments,
    router_contacts as router_wf_contacts,
    router_dictionaries as router_wf_dictionaries,
    router_dictionary_entries as router_wf_dictionary_entries,
    router_edr_paperless as router_wf_edr_paperless,
    router_erpnext_invoices as router_wf_erpnext_invoices,
    router_notifications as router_wf_notifications,
    router_paperless_documents as router_wf_paperless_documents,
    router_process_instances as router_wf_process_instances,
    router_process_models as router_wf_process_models,
    router_purchase_requests as router_wf_purchase_requests,
    router_signius as router_wf_signius,
    router_tasks as router_wf_tasks,
    router_users as router_wf_users,
)
from wf.routers.calendar_api import router_calendar as router_wf_calendar


# 2026-11-05: moduł edu (Frappe Education) pod /v1/edu.
# 2026-11-08: plan zajęć, oceny, frekwencja, opłaty i legitymacja.
from edu.routers import (
    router_frekwencja,
    router_ja,
    router_korespondencja,
    router_legitymacja,
    router_oceny,
    router_oplaty,
    router_plan,
    router_slowniki,
    router_studia,
    router_studenci,
    router_zajecia,
)
from edu.services.case_sync import register as register_edu_case_sync
# wywołania Frappe w imieniu użytkownika (edu/dependencies.py).
from edu.dependencies import frappe_as_user
from connector.edoreczenia.edr_reply import register as register_edr_reply

from bcs.routers import router_cycle, router_disputes, router_members, router_me, router_networks, router_obligations, \
                        router_organizations, router_settlement, router_source_systems, router_statements

api_description = """
API  ....
"""

api_tags_metadata = [
    {
        "name": "Dasboard",
        "description": "Operacje API",
        "externalDocs": {
         "description": "zob. /v1/galwork/docs",
         "url": settings.app.docs_url,
       },
    },

]

v1router = FastAPI(openapi_tags=api_tags_metadata,
                       description=api_description,
                       version="1.0.0",
                       terms_of_service="#",
                       )
app_galwork = FastAPI()
app_galwork.include_router(router_auth)
v1router.mount("/galwork", app_galwork)

app_pm = FastAPI()
app_pm.include_router(router_calc)
app_pm.include_router(router_dashboard)
app_pm.include_router(router_projects)
app_pm.include_router(router_auth)
app_pm.include_router(router_forms)
v1router.mount("/pm", app_pm)

app_wf = FastAPI()
app_wf.include_router(router_wf_process_models)
app_wf.include_router(router_wf_process_instances)
app_wf.include_router(router_wf_tasks)
app_wf.include_router(router_wf_comments)
app_wf.include_router(router_wf_notifications)
app_wf.include_router(router_wf_calendar)
app_wf.include_router(router_wf_signius)
app_wf.include_router(router_wf_edr_paperless)
app_wf.include_router(router_wf_dictionaries)
app_wf.include_router(router_wf_users)
app_wf.include_router(router_wf_erpnext_invoices)
app_wf.include_router(router_wf_paperless_documents)
app_wf.include_router(router_wf_dictionary_entries)
app_wf.include_router(router_wf_contacts)
app_wf.include_router(router_wf_purchase_requests)
v1router.mount("/wf", app_wf)

app_bcs = FastAPI()
app_bcs.include_router(router_cycle)
app_bcs.include_router(router_disputes)
app_bcs.include_router(router_members)
app_bcs.include_router(router_me)
app_bcs.include_router(router_networks)
app_bcs.include_router(router_obligations)
app_bcs.include_router(router_organizations)
app_bcs.include_router(router_settlement)
app_bcs.include_router(router_source_systems)
app_bcs.include_router(router_statements)
v1router.mount("/bcs", app_bcs)

# Moduł edu - korespondencja z dziekanatem, dane studenta,
# słowniki i zajęcia (Frappe Education).
# Plan, oceny, frekwencja, opłaty i legitymacja - patrz
# edu/routers/*_api.py oraz connector/frappe_education/*.
# Wywołania Frappe w imieniu użytkownika (przełącznik
# [frappe_education] forward_user_token, domyślnie wyłączony).
app_edu = FastAPI(dependencies=[Depends(frappe_as_user)])
# role użytkownika (student/wykładowca/dziekanat/administrator) -
app_edu.include_router(router_ja)
app_edu.include_router(router_korespondencja)
app_edu.include_router(router_studenci)
app_edu.include_router(router_slowniki)
app_edu.include_router(router_zajecia)
app_edu.include_router(router_studia)
app_edu.include_router(router_plan)
app_edu.include_router(router_oceny)
app_edu.include_router(router_frekwencja)
app_edu.include_router(router_oplaty)
app_edu.include_router(router_legitymacja)
v1router.mount("/edu", app_edu)
register_edu_case_sync()
register_edr_reply()

# Usługa kodów jednorazowych (konto/) w głównej
# aplikacji tylko przy [konto] mount_in_hub=true; zalecane osobne
# wdrożenie (uvicorn konto.app:app).
import logging

from konto.config import mount_in_hub

if mount_in_hub():
  from konto.router import router as router_konto

  app_konto = FastAPI()
  app_konto.include_router(router_konto)
  v1router.mount('/konto', app_konto)
else:
  # 2026-11-10: bez tego brak /v1/konto (404) nie zostawiał śladu w logu.
  logging.getLogger(__name__).warning(
    '/v1/konto nie jest zamontowane w hubie - ustaw [konto] mount_in_hub=true '
    '(albo KONTO_MOUNT_IN_HUB=true) lub uruchom osobno: uvicorn konto.app:app')
# /

# 2026-11-10: kalendarz użytkownika - agregator terminów
# (zajęcia, kalendarz akademicki, terminy zadań, kalendarz uczelni z ICS).
# Wywołania Frappe jak w /v1/edu (frappe_as_user).
from kalendarz.router import router as router_kalendarz

app_kalendarz = FastAPI(dependencies=[Depends(frappe_as_user)])
app_kalendarz.include_router(router_kalendarz)
v1router.mount('/kalendarz', app_kalendarz)
# /

# portal kandydata (realm kandydatów, admissions_poland).
from rekrutacja.router import router as router_rekrutacja

app_rekrutacja = FastAPI()
app_rekrutacja.include_router(router_rekrutacja)
v1router.mount('/rekrutacja', app_rekrutacja)
# /
