# data „Obowiązuje od” wymagana w formularzu startowym modelu
# `dokumenty` (x-required-in-steps: Start). Hub odrzuca start bez daty (422),
# zanim powstanie sprawa - krok `akty.obowiazuje_od` nie ma na co czekać.
from __future__ import annotations

import datetime
import unittest

from tests.fixture_paths import fixture_model
from tests.test_wf_instance_documents import _Base
from wf.models import DocumentVersion

MODEL_DIR = fixture_model('regulacje', 'dokumenty')


class ActStartFormTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        from wf.models import ProcessModelVersion
        from wf.schemas.wf_schemas import ProcessModelVersionStatus
        from wf.services.filesystem_sync import sync_folder
        result = sync_folder(self.db, MODEL_DIR)
        self.assertEqual(result.status, 'draft-created', result.detail)
        for version in self.db.query(ProcessModelVersion).all():
            version.status = ProcessModelVersionStatus.PUBLISHED
        self.db.commit()

    def _start(self, data: dict):
        return self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'dokumenty', 'title': 'Zarządzenie 12/2026', 'data': data})

    def test_start_form_marks_effective_date_required(self):
        form = self.client.get('/v1/wf/process-models/dokumenty/start-form').json()
        self.assertIn('Obowiązuje od', form['json_schema'].get('required', []))

    def test_start_without_effective_date_is_rejected(self):
        response = self._start({'Tytuł dokumentu': 'O cyfryzacji'})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('Obowiązuje od', response.text)
        self.assertEqual(self.client.get('/v1/wf/process-instances').json()['total'], 0)

    def test_start_with_effective_date_reaches_repository_task(self):
        response = self._start({'Tytuł dokumentu': 'O cyfryzacji', 'Obowiązuje od': '2026-03-01',
                               'Czy opieczętować elektronicznie?': False})
        self.assertEqual(response.status_code, 201, response.text)
        instance = response.json()
        self.assertEqual(self.upload(instance['id'], role='main').status_code, 201)
        woken = self.client.post(f"/v1/wf/process-instances/{instance['id']}/wake").json()
        self.assertIsNone(woken['error_message'])
        self.assertEqual([t.name for t in self.open_tasks(instance['id'])], ['Repozytorium dokumentów'])
        version = self.db.query(DocumentVersion).one()
        self.assertEqual(version.effective_from, datetime.date(2026, 3, 1))


if __name__ == '__main__':
    unittest.main()
