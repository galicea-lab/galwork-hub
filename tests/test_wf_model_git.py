# tests/test_wf_model_git.py
# 2026-11-17: pochodzenie wersji modelu z gita i tag wydania przy
# publikacji (wf/services/model_git.py) - na tymczasowym repozytorium modeli.
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.base import ApiTestCase
from tests.test_wf_instance_documents import REPO
from wf.schemas.wf_schemas import CurrentUser
from wf.services.model_git import _tags_enabled as REAL_TAGS_ENABLED

GIT_ENV = {**os.environ, 'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 't@example.com'}


def git(root: str, *args: str) -> str:
    return subprocess.run(['git', '-C', root, *args], capture_output=True, text=True,
                          check=True, env=GIT_ENV).stdout.strip()


class ModelGitTest(ApiTestCase):

    def prepare(self) -> None:
        user = self.create_user()
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name, email='jurek@example.com',
            display_name='Jurek W', groups=frozenset({'wf-admin'}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.folder = os.path.join(self.root, 'regulacje', 'dokumenty')
        os.makedirs(self.folder)
        self.write(REPO)
        with open(os.path.join(self.folder, 'process_model.json'), 'w') as handle:
            handle.write('{"display_name": "Dokumenty", "primary_file_name": "dokumenty.bpmn"}')
        git(self.root, 'init', '-q')
        self.commit('pierwsza wersja')
        self.config = {'process_files_dir': self.root}
        patcher = mock.patch('wf.services.process_files._config', side_effect=lambda: self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        # tests/base.py wyłącza tagi - tu działają na repozytorium tymczasowym
        tags = mock.patch('wf.services.model_git._tags_enabled', side_effect=REAL_TAGS_ENABLED)
        tags.start()
        self.addCleanup(tags.stop)

    def write(self, content: str) -> None:
        with open(os.path.join(self.folder, 'dokumenty.bpmn'), 'w') as handle:
            handle.write(content)

    def commit(self, message: str) -> str:
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', message)
        return git(self.root, 'rev-parse', 'HEAD')

    def sync(self) -> dict:
        response = self.client.post('/v1/wf/process-models/sync-from-disk')
        self.assertEqual(response.status_code, 200, response.text)
        model = self.client.get('/v1/wf/process-models').json()[0]
        versions = self.client.get(f"/v1/wf/process-models/{model['id']}/versions").json()
        return max(versions, key=lambda v: v['version'])

    def publish(self, version: dict) -> dict:
        response = self.client.post(f"/v1/wf/process-models/{version['process_model_id']}"
                                    f"/versions/{version['id']}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_clean_version_is_tagged_on_publish(self):
        head = git(self.root, 'rev-parse', 'HEAD')
        version = self.sync()
        self.assertEqual((version['git_commit'], version['git_dirty']), (head, False))

        published = self.publish(version)
        self.assertEqual(published['git_tag'], 'model/dokumenty/v1')
        self.assertEqual(published['warnings'], [])
        self.assertEqual(git(self.root, 'rev-parse', 'model/dokumenty/v1^{commit}'), head)
        message = git(self.root, 'tag', '-l', '--format=%(contents)', 'model/dokumenty/v1')
        self.assertIn(f"Skrót treści: {version['content_hash']}", message)
        self.assertIn('Opublikował: Jurek W', message)
        self.assertEqual(git(self.root, 'tag', '-l', '--format=%(taggername)', 'model/dokumenty/v1'),
                         'Jurek W')
        # ponowna publikacja - ten sam tag
        self.assertEqual(self.publish(version)['git_tag'], 'model/dokumenty/v1')

    def test_uncommitted_changes_are_marked_and_not_tagged(self):
        self.write(REPO.replace('Repozytorium dokumentów', 'Repozytorium'))
        version = self.sync()
        self.assertTrue(version['git_dirty'])
        published = self.publish(version)
        self.assertIsNone(published['git_tag'])
        self.assertIn('niezacommitowane zmiany', published['warnings'][0])
        self.assertEqual(git(self.root, 'tag', '-l'), '')

        head = self.commit('druga wersja')
        version = self.sync()  # ta sama treść - bez nowej wersji
        self.assertTrue(version['git_dirty'])
        self.write(REPO.replace('Repozytorium dokumentów', 'Repozytorium 3'))
        head = self.commit('trzecia wersja')
        version = self.sync()
        self.assertEqual((version['version'], version['git_commit'], version['git_dirty']),
                         (2, head, False))
        self.assertEqual(self.publish(version)['git_tag'], 'model/dokumenty/v2')

    def test_root_inside_other_repository_is_not_tagged(self):
        self.config = {'process_files_dir': os.path.join(self.root, 'regulacje')}
        version = self.sync()
        self.assertEqual(version['git_commit'], git(self.root, 'rev-parse', 'HEAD'))
        published = self.publish(version)
        self.assertIsNone(published['git_tag'])
        self.assertEqual(published['warnings'], [])

    def test_tags_can_be_disabled(self):
        self.config = {'process_files_dir': self.root, 'process_release_tags': 'false'}
        published = self.publish(self.sync())
        self.assertIsNone(published['git_tag'])
        self.assertEqual(git(self.root, 'tag', '-l'), '')


if __name__ == '__main__':
    unittest.main()
