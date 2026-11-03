# tests/test_filesystem_sync.py
"""
Testy wf/services/filesystem_sync.py (synchronizacja ProcessModel z
plikami w drzewie spiffworkflow/) i endpointu
POST /process-models/sync-from-disk (wf/routers/process_model_router.py).
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tests.base import ApiTestCase

from wf.models import ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser, ProcessModelVersionStatus
from wf.services.filesystem_sync import SyncResult, discover_process_folders, sync_all, sync_folder


def _write_folder(root: str, rel_path: str, *, process_id: str, name: str,
                  key: str | None = None, with_form: bool = False) -> str:
    folder = os.path.join(root, rel_path)
    os.makedirs(folder, exist_ok=True)
    basename = os.path.basename(folder)
    bpmn = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        f'id="defs_{process_id}" targetNamespace="http://bpmn">'
        f'<bpmn:process id="{process_id}" name="{name}" isExecutable="true">'
        '<bpmn:startEvent id="Start" />'
        '<bpmn:userTask id="Task_1" name="Krok" />'
        '</bpmn:process></bpmn:definitions>'
    )
    with open(os.path.join(folder, f'{basename}.bpmn'), 'w', encoding='utf-8') as f:
        f.write(bpmn)
    if with_form:
        with open(os.path.join(folder, f'{basename}-schema.json'), 'w', encoding='utf-8') as f:
            f.write('{"type":"object","properties":{}}')
        with open(os.path.join(folder, f'{basename}-uischema.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
    meta = {'display_name': name}
    if key:
        meta['key'] = key
    with open(os.path.join(folder, 'process_model.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    return folder


class DiscoverProcessFoldersTest(unittest.TestCase):

    def test_finds_leaf_folders_only(self):
        with tempfile.TemporaryDirectory() as root:
            _write_folder(root, 'dept/proces-a', process_id='Process_A', name='A')
            os.makedirs(os.path.join(root, 'dept/empty-group'), exist_ok=True)

            found = discover_process_folders(root)

            self.assertEqual(found, [os.path.join(root, 'dept/proces-a')])


class SyncFolderTest(ApiTestCase):
    """Operacje na bazie (ProcessModel/ProcessModelVersion) - w pamięci (SQLite)."""

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset(),
        )

    def test_new_folder_creates_draft_model(self):
        with tempfile.TemporaryDirectory() as root:
            folder = _write_folder(root, 'proces-a', process_id='Process_A', name='Proces A')
            result = sync_folder(self.db, folder)
            self.db.commit()

            self.assertEqual(result.status, 'draft-created')
            model = self.db.query(ProcessModel).filter_by(key='proces-a').one()
            version = self.db.query(ProcessModelVersion).filter_by(process_model_id=model.id).one()
            self.assertEqual(version.status, ProcessModelVersionStatus.DRAFT)
            self.assertEqual(version.version, 1)

    def test_key_override_from_process_model_json(self):
        with tempfile.TemporaryDirectory() as root:
            folder = _write_folder(root, 'adres', process_id='Process_Adres', name='Adres',
                                   key='adres-e-doreczen')
            result = sync_folder(self.db, folder)
            self.db.commit()

            self.assertEqual(result.key, 'adres-e-doreczen')
            self.assertIsNotNone(
                self.db.query(ProcessModel).filter_by(key='adres-e-doreczen').one_or_none())

    def test_unchanged_content_is_a_noop(self):
        with tempfile.TemporaryDirectory() as root:
            folder = _write_folder(root, 'proces-a', process_id='Process_A', name='Proces A')
            sync_folder(self.db, folder)
            self.db.commit()

            result = sync_folder(self.db, folder)

            self.assertEqual(result.status, 'unchanged')
            model = self.db.query(ProcessModel).filter_by(key='proces-a').one()
            self.assertEqual(
                self.db.query(ProcessModelVersion).filter_by(process_model_id=model.id).count(), 1)

    def test_changed_content_creates_new_draft_version_without_touching_published(self):
        with tempfile.TemporaryDirectory() as root:
            folder = _write_folder(root, 'proces-a', process_id='Process_A', name='Proces A')
            sync_folder(self.db, folder)
            self.db.commit()
            model = self.db.query(ProcessModel).filter_by(key='proces-a').one()
            v1 = self.db.query(ProcessModelVersion).filter_by(process_model_id=model.id).one()
            v1.status = ProcessModelVersionStatus.PUBLISHED
            self.db.commit()

            # Zmiana pliku .bpmn na dysku (inna nazwa zadania -> inny content_hash).
            with open(os.path.join(folder, 'proces-a.bpmn'), 'r+', encoding='utf-8') as f:
                content = f.read().replace('name="Krok"', 'name="Krok zmieniony"')
                f.seek(0)
                f.write(content)
                f.truncate()

            result = sync_folder(self.db, folder)
            self.db.commit()

            self.assertEqual(result.status, 'draft-created')
            versions = (self.db.query(ProcessModelVersion)
                       .filter_by(process_model_id=model.id).order_by(ProcessModelVersion.version).all())
            self.assertEqual([v.version for v in versions], [1, 2])
            self.assertEqual(versions[0].status, ProcessModelVersionStatus.PUBLISHED)  # nietknięta
            self.assertEqual(versions[1].status, ProcessModelVersionStatus.DRAFT)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            folder = _write_folder(root, 'proces-a', process_id='Process_A', name='Proces A')
            result = sync_folder(self.db, folder, dry_run=True)

            self.assertEqual(result.status, 'would-create-model')
            self.assertIsNone(
                self.db.query(ProcessModel).filter_by(key='proces-a').one_or_none())

    def test_folder_without_bpmn_is_reported_as_error_not_raised(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'broken')
            os.makedirs(folder)
            with open(os.path.join(folder, 'process_model.json'), 'w', encoding='utf-8') as f:
                f.write('{}')

            result = sync_folder(self.db, folder)

            self.assertEqual(result.status, 'error')

    def test_sync_all_walks_whole_tree(self):
        with tempfile.TemporaryDirectory() as root:
            _write_folder(root, 'dept1/proces-a', process_id='Process_A', name='A')
            _write_folder(root, 'dept2/proces-b', process_id='Process_B', name='B', with_form=True)

            results = sync_all(self.db, root)

            self.assertEqual({r.key for r in results}, {'proces-a', 'proces-b'})
            self.assertEqual({r.status for r in results}, {'draft-created'})


class SyncFromDiskEndpointTest(ApiTestCase):
    """Router: autoryzacja (admin-only) i przekazanie `dry_run` - logika
    synchronizacji jest testowana osobno (SyncFolderTest), więc tu stubujemy
    `sync_process_models`, żeby nie dotykać prawdziwego spiffworkflow/."""

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset(),
        )

    def test_requires_admin_group(self):
        response = self.client.post('/v1/wf/process-models/sync-from-disk')
        self.assertEqual(response.status_code, 403, response.text)

    def test_admin_can_sync_and_dry_run_is_passed_through(self):
        self.current_user = self.current_user.model_copy(
            update={'groups': frozenset({'wf-admin'})})
        fake_result = [SyncResult(folder='x', key='proces-a', status='draft-created')]

        with patch('wf.routers.process_model_router.sync_process_models',
                  return_value=fake_result) as mocked:
            response = self.client.post('/v1/wf/process-models/sync-from-disk',
                                        json={'dry_run': True})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [{'folder': 'x', 'key': 'proces-a',
                                            'status': 'draft-created', 'detail': ''}])
        self.assertEqual(mocked.call_args.kwargs.get('dry_run'), True)

    def test_admin_can_sync_without_body(self):
        self.current_user = self.current_user.model_copy(
            update={'groups': frozenset({'wf-admin'})})
        with patch('wf.routers.process_model_router.sync_process_models', return_value=[]) as mocked:
            response = self.client.post('/v1/wf/process-models/sync-from-disk')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mocked.call_args.kwargs.get('dry_run'), False)
