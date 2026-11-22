
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
from .instance_document_router import router as router_instance_documents
from .legal_act_router import router as router_legal_acts
from .rollout_router import router as router_rollouts
from .discussion_router import router as router_discussions
from .notification_router import router as router_notifications
from .permissions_router import router as router_permissions
from .paperless_document_api import router as router_paperless_documents
from .process_file_router import router as router_process_files  
from .process_instance_router import router as router_process_instances
from .print_router import router as router_print 
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
    "router_process_files",
    "router_print",
    "router_instance_documents",
    "router_legal_acts",
    "router_permissions",
    "router_rollouts",
    "router_discussions",
]
