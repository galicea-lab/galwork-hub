# wf/services/paperless_document_service.py
# coding: utf-8
"""
Kolejka dokumentów z Paperless-ngx filtrowana po tagu - widget na pulpicie
galwork-dashboard (2026-10-18).

Kontekst: PDF-y faktur zakupu dostają się dziś do skrzynki mailowej (od
dostawcy, z KSeF nie da się pociągnąć PDF-a - tylko XML). Zamiast robić
jakąkolwiek analizę treści maila w galwork, ląduje to w n8n (trigger IMAP/
Gmail -> wyciągnięcie załącznika -> upload do Paperless z ustalonym tagiem)
- ten moduł tylko odpytuje Paperless o dokumenty z tym tagiem, nic nie
wgrywa ani nie analizuje. Dopasowanie konkretnego PDF-a do konkretnej
faktury w ERPNext zostaje manualne (człowiek otwiera dokument w Paperless
i podłącza go po stronie ERPNext) - decyzja 2026-10-18: świadomie prostsze
i bez ryzyka złego dopasowania, niż próba automatycznego matchowania po
temacie/nadawcy maila.

Tag jest parametrem PER WIDGET (konfiguracja na dashboardzie), nie
globalnym ustawieniem w config.ini - w przeciwieństwie do stanów workflow
ERPNext (tamte są właściwością jednej, konkretnej instalacji; tagi w
Paperless mogą być tworzone swobodnie, różne widgety = różne tagi/kategorie
dokumentów).
"""

from __future__ import annotations

from connector.base import translate_network_errors
from connector.paperless import get_connector
from wf.schemas.wf_schemas import PaperlessDocumentPage, PaperlessDocumentSummary


def list_by_tag(tag: str, limit: int = 25) -> PaperlessDocumentPage:
    """Dokumenty oznaczone tagiem `tag` w Paperless-ngx, najnowsze pierwsze.

    Podnosi `connector.base.ConnectorConfigError` (sekcja [paperless] bez
    `base_url`/`token`) albo `ConnectorError` (Paperless nieosiągalny/
    odpowiedział błędem) - router tłumaczy to na 503/502, tak jak reszta
    modułu wf.

    Nieistniejący tag (jeszcze nikt/nic go nie użyło) to pusta lista, NIE
    błąd - dopóki n8n nie wgra pierwszego dokumentu z tym tagiem, to jest
    prawidłowy, "jeszcze nic tu nie ma" stan widgetu.
    """
    connector = get_connector()
    base_url = connector.config.require("base_url").rstrip("/")

    with translate_network_errors("Paperless"):
        tag_id = connector.find_tag_id(tag)
        if tag_id is None:
            return PaperlessDocumentPage(items=[], total=0)
        body = connector.list_documents(tag_id=tag_id, limit=limit)

    rows = body.get("results", []) if isinstance(body, dict) else body
    total = body.get("count", len(rows)) if isinstance(body, dict) else len(rows)
    items = [_to_summary(row, base_url) for row in rows]
    return PaperlessDocumentPage(items=items, total=total)


def _to_summary(row: dict, base_url: str) -> PaperlessDocumentSummary:
    document_id = row["id"]
    return PaperlessDocumentSummary(
        id=document_id,
        title=row.get("title") or f"Dokument {document_id}",
        created=row.get("created"),
        original_file_name=row.get("original_file_name"),
        url=f"{base_url}/documents/{document_id}",
    )
