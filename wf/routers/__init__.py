
"""
Agregacja routerów aplikacji wf (obieg dokumentów). 

Nie zawiera routerów z wf/routers/bpmn/ - to odrębny, niekompletny prototyp
(importuje nieistniejące moduły api.schemas/db.models i inną async bazę
deklaratywną niż reszta huba). (2026-10-04).
"""

from .comment_router import router as router_comments
from .contact_api import router as router_contacts
from .dictionary_api import router as router_dictionaries
from .dictionary_entry_api import router as router_dictionary_entries
from .edr_paperless_api import router as router_edr_paperless
from .erpnext_invoice_api import router as router_erpnext_invoices
from .human_task_router import router as router_tasks
from .notification_router import router as router_notifications
from .paperless_document_api import router as router_paperless_documents
from .process_instance_router import router as router_process_instances
from .process_model_router import router as router_process_models
from .purchase_request_api import router as router_purchase_requests
from .signius_api import router as router_signius
from .user_directory_api import router as router_users

__all__ = [
    "router_process_models",
    "router_process_instances",
    "router_tasks",
    "router_comments",
    "router_notifications",
    "router_signius",
    "router_edr_paperless",
    "router_dictionaries",
    "router_users",
    "router_erpnext_invoices",
    "router_paperless_documents",
    "router_dictionary_entries",
    "router_contacts",
    "router_purchase_requests",
]
