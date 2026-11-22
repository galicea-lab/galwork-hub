# 2026-11-22: scripts/seed_process_model.py - synchronizacja jak „Synchronizuj
# z dysku” (process_model.json w wersji) i publikacja przez model_publication.
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

from tests.fixture_paths import FIXTURE_MODELS
from tests.base import ApiTestCase
from wf.models import ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import ProcessModelVersionStatus

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = FIXTURE_MODELS   # profil galicea
SPIFF = FIXTURE_MODELS    # profil cyfryzacja

spec = importlib.util.spec_from_file_location('seed_process_model', os.path.join(HUB, 'scripts', 'seed_process_model.py'))
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SeedProcessModelTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        for target, value in (('SessionLocal', self.session_factory),):
            patcher = mock.patch.object(seed, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        tags = mock.patch('wf.services.model_git.tag_release', return_value=(None, None))
        tags.start()
        self.addCleanup(tags.stop)

    def run_script(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['seed_process_model.py', *argv]), contextlib.redirect_stdout(out):
            try:
                code = seed.main()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def versions(self, key: str) -> list[ProcessModelVersion]:
        self.db.expire_all()
        model = self.db.query(ProcessModel).filter_by(key=key).one()
        return sorted(model.versions, key=lambda v: v.version)

    def test_sync_all_publish_profile(self):
        code, out = self.run_script('--sync-all', '--root', MODELS, '--profile', 'galicea',
                                    '--publish', '--user', self.user.name)
        self.assertEqual(code, 0, out)
        for key in ('rejestr-systemow', 'domeny', 'dokumenty', 'pm-przeglad-tygodnia', 'faktura-zakupu'):
            [version] = self.versions(key)
            self.assertEqual(version.status, ProcessModelVersionStatus.PUBLISHED, key)
            self.assertEqual(version.published_by, self.user.id)
        [systems] = self.versions('rejestr-systemow')
        self.assertIn('process_model.json', [f.file_name for f in systems.files])
        self.assertEqual(systems.spec_json['galwork']['dictionary']['slug'], 'rejestr-systemow')
        # ponowne uruchomienie - nic nowego do publikacji
        code, out = self.run_script('--sync-all', '--root', MODELS, '--profile', 'galicea',
                                    '--publish', '--user', self.user.name)
        self.assertEqual(code, 0, out)
        self.assertNotIn('opublikowany', out)
        self.assertEqual(len(self.versions('domeny')), 1)

    def test_sync_all_without_publish_leaves_drafts_then_publish_picks_them(self):
        code, _ = self.run_script('--sync-all', '--root', SPIFF, '--profile', 'cyfryzacja')
        self.assertEqual(code, 0)
        self.assertTrue(all(v.status == ProcessModelVersionStatus.DRAFT for v in self.versions('dokumenty')))
        code, out = self.run_script('--sync-all', '--root', SPIFF, '--profile', 'cyfryzacja',
                                    '--publish', '--user', self.user.name)
        self.assertEqual(code, 0, out)       # modele z callActivity czekają na proces wywoływany
        self.assertNotIn('BŁĄD', out)
        published = self.db.query(ProcessModelVersion).filter_by(status=ProcessModelVersionStatus.PUBLISHED).count()
        self.assertEqual(published, self.db.query(ProcessModel).count())

    def test_single_folder_uses_model_key(self):
        folder = os.path.join(SPIFF, 'regulacje', 'dokumenty')
        code, out = self.run_script(folder, '--key', 'dokumenty_zle', '--user', self.user.name)
        self.assertEqual(code, 2)
        self.assertEqual(self.db.query(ProcessModel).count(), 0)
        code, out = self.run_script(folder, '--user', self.user.name)
        self.assertEqual(code, 0, out)
        [version] = self.versions('dokumenty')
        self.assertEqual(version.status, ProcessModelVersionStatus.PUBLISHED)
        self.assertEqual(version.spec_json['galwork']['record_step'], 'Repozytorium dokumentów')

    def test_dry_run_writes_nothing(self):
        code, out = self.run_script('--sync-all', '--root', SPIFF, '--profile', 'cyfryzacja', '--publish', '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.db.query(ProcessModel).count(), 0)


if __name__ == '__main__':
    unittest.main()
