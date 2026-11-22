# tests/test_wf_document_ingest.py
# 2026-11-17: ponowna wysyłka dokumentów huba do Paperless-ngx
# (wf/services/document_ingest.py) i rozdział od wysyłki e-Doręczeń.
from __future__ import annotations

import unittest
import uuid

from tests.test_wf_instance_documents import REPO, _Base
from wf.models import DocumentVersion
from wf.services import document_ingest


class FlakyUploads:
    """Paperless, który odrzuca wysyłkę, dopóki `down` jest ustawione."""

    def __init__(self, paperless):
        self.paperless = paperless
        self.down = True

    def __getattr__(self, name):
        return getattr(self.paperless, name)

    def upload_document(self, content, filename, **kwargs):
        if self.down:
            raise ConnectionError('Paperless niedostępny')
        return self.paperless.upload_document(content, filename, **kwargs)

    def get_or_create_document_type(self, name):
        return 2


class DocumentIngestTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.flaky = FlakyUploads(self.paperless)
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.instance = self.start('dokumenty')

    def version(self, document_id: str) -> DocumentVersion:
        self.db.expire_all()
        return self.db.query(DocumentVersion).filter_by(document_id=uuid.UUID(document_id)).one()

    def upload_while_down(self) -> str:
        from unittest import mock
        with mock.patch('connector.paperless.get_connector', return_value=self.flaky):
            response = self.upload(self.instance['id'], role='main')
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def test_failed_upload_is_retried(self):
        document_id = self.upload_while_down()
        version = self.version(document_id)
        self.assertEqual(version.ingest_status.value, 'staged')
        self.assertIn('niedostępny', version.ingest_error)

        result = document_ingest.submit_pending(self.db, self.flaky)  # nadal niedostępny
        self.assertEqual((result.submitted, result.retried_later), (0, 1))
        self.assertEqual(self.version(document_id).ingest_status.value, 'staged')

        self.flaky.down = False
        result = document_ingest.submit_pending(self.db, self.flaky)
        self.assertEqual(result.submitted, 1)
        version = self.version(document_id)
        self.assertEqual(version.ingest_status.value, 'submitted')
        self.assertIsNone(version.ingest_error)
        self.assertEqual(self.paperless.uploads, ['zarzadzenie.pdf'])

    def test_tampered_staging_file_is_not_archived(self):
        document_id = self.upload_while_down()
        with open(self.version(document_id).staging_path, 'wb') as handle:
            handle.write(b'%PDF-1.7\npodmieniony\n%%EOF')
        self.flaky.down = False
        result = document_ingest.submit_pending(self.db, self.flaky)
        self.assertEqual((result.submitted, result.rejected), (0, 1))
        version = self.version(document_id)
        self.assertEqual(version.ingest_status.value, 'failed')
        self.assertIn('inny skrót', version.ingest_error)
        self.assertEqual(self.paperless.uploads, [])
        # odrzucona wersja nie wraca do kolejki
        self.assertEqual(document_ingest.pending_versions(self.db, 50), [])

    def test_edr_job_leaves_hub_documents_alone(self):
        from connector.edoreczenia.edr_paperless import submit_staged_documents
        document_id = self.upload_while_down()
        self.flaky.down = False
        self.assertEqual(submit_staged_documents(self.db, self.flaky), 0)
        self.assertEqual(self.version(document_id).ingest_status.value, 'staged')


if __name__ == '__main__':
    unittest.main()
