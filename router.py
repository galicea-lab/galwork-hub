#!/usr/bin/env python3
# coding: utf-8
# authors: Jerzy Wawro
# (C) 2026

from fastapi import FastAPI, APIRouter
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
