# tests/test_wf_immutability.py
# 2026-11-17: wyzwalacze niezmienności (wf/models/immutability.py) - baza
# odrzuca zmianę opublikowanych wersji modeli, treści wersji dokumentów,
# relacji aktów i dziennika zdarzeń, niezależnie od kodu, który ją wykonuje.
from __future__ import annotations

import unittest
import uuid
from datetime import date

from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from tests.test_wf_instance_documents import REPO, _Base
from wf.models import (
    Document,
    DocumentRelation,
    DocumentRelationType,
    DocumentVersion,
    ProcessEvent,
    ProcessInstanceDocument,
    ProcessModelFile,
    ProcessModelVersion,
)
from wf.schemas.wf_schemas import ProcessModelVersionStatus


class ImmutabilityTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.instance = self.start('dokumenty')
        self.document = uuid.UUID(self.upload(self.instance['id'], role='main').json()['id'])
        self.db.expire_all()

    def rejected(self, statement) -> None:
        with self.assertRaises(DBAPIError) as ctx:
            self.db.execute(statement)
            self.db.flush()
        self.assertIn('galwork:', str(ctx.exception))
        self.db.rollback()

    def allowed(self, statement) -> None:
        self.db.execute(statement)
        self.db.commit()

    # --- dziennik ---------------------------------------------------------

    def test_event_log_is_append_only(self):
        self.rejected(update(ProcessEvent).values(event_type='podmienione'))
        self.rejected(delete(ProcessEvent))

    # --- modele procesów --------------------------------------------------

    def test_published_model_version_is_protected(self):
        version = self.db.query(ProcessModelVersion).one()
        self.assertEqual(version.status, ProcessModelVersionStatus.PUBLISHED)
        where = ProcessModelVersion.id == version.id
        self.rejected(update(ProcessModelVersion).where(where).values(content_hash='0' * 64))
        self.rejected(update(ProcessModelVersion).where(where).values(bpmn_process_id='Inny'))
        self.rejected(update(ProcessModelVersion).where(where).values(status='draft'))
        self.rejected(update(ProcessModelVersion).where(where).values(
            status='retired', published_by=None))
        self.rejected(delete(ProcessModelVersion).where(where))
        self.rejected(update(ProcessModelFile).where(ProcessModelFile.version_id == version.id)
                      .values(content='<podmieniony/>'))
        self.rejected(delete(ProcessModelFile).where(ProcessModelFile.version_id == version.id))
        # przejścia statusu są dozwolone
        self.allowed(update(ProcessModelVersion).where(where).values(status='retired'))
        self.allowed(update(ProcessModelVersion).where(where).values(status='published'))

    def test_draft_version_can_change_and_be_deleted(self):
        model_id = self.db.query(ProcessModelVersion).one().process_model_id
        draft = self.client.post(f'/v1/wf/process-models/{model_id}/versions', json={
            'bpmn_process_id': 'Process_Dokumenty',
            'files': [{'file_name': 'd.bpmn', 'file_type': 'bpmn', 'content': REPO}]}).json()
        draft_id = uuid.UUID(draft['id'])
        self.allowed(update(ProcessModelFile).where(ProcessModelFile.version_id == draft_id)
                     .values(content=REPO + ' '))
        self.allowed(delete(ProcessModelFile).where(ProcessModelFile.version_id == draft_id))
        self.allowed(delete(ProcessModelVersion).where(ProcessModelVersion.id == draft_id))

    def test_republishing_current_version_is_allowed(self):
        version = self.db.query(ProcessModelVersion).one()
        response = self.client.post(
            f'/v1/wf/process-models/{version.process_model_id}/versions/{version.id}/publish')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'published')

    # --- dokumenty --------------------------------------------------------

    def test_document_version_content_is_protected(self):
        where = DocumentVersion.document_id == self.document
        for values in ({'sha256': '0' * 64}, {'file_name': 'inny.pdf'}, {'size_bytes': 1},
                       {'version_no': 9}, {'label': 'podmieniona'}):
            self.rejected(update(DocumentVersion).where(where).values(**values))
        self.rejected(delete(DocumentVersion).where(where))
        self.rejected(delete(ProcessInstanceDocument).where(
            ProcessInstanceDocument.document_id == self.document))
        self.rejected(delete(Document).where(Document.id == self.document))
        # stan wysyłki do Paperless i jednorazowe ustawienie daty - dozwolone
        self.allowed(update(DocumentVersion).where(where).values(ingest_status='done',
                                                                 paperless_version_id=7))
        self.allowed(update(DocumentVersion).where(where).values(effective_from=date(2026, 1, 1)))
        self.rejected(update(DocumentVersion).where(where).values(effective_from=date(2026, 2, 1)))
        # dokument (tytuł, bieżąca wersja) można aktualizować
        self.allowed(update(Document).where(Document.id == self.document).values(title='Nowy tytuł'))

    def test_relation_is_protected(self):
        other = uuid.UUID(self.upload(self.instance['id']).json()['id'])
        relation = DocumentRelation(source_document_id=other, target_document_id=self.document,
                                    relation_type=DocumentRelationType.AMENDS,
                                    effective_from=date(2026, 7, 1))
        self.db.add(relation)
        self.db.commit()
        where = DocumentRelation.id == relation.id
        self.allowed(update(DocumentRelation).where(where).values(target_version_no=2))
        self.rejected(update(DocumentRelation).where(where).values(target_version_no=3))
        self.rejected(update(DocumentRelation).where(where).values(effective_from=date(2026, 1, 1)))
        self.rejected(delete(DocumentRelation).where(where))


if __name__ == '__main__':
    unittest.main()
