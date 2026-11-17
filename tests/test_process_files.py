# tests/test_process_files.py
# 2026-11-15: testy API plików obiegów (wf/services/process_files.py,
# wf/routers/process_file_router.py) - następcy bpmn-files.mjs areny.
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser
from wf.services import process_files

BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'id="defs_a" targetNamespace="http://bpmn">'
    '<bpmn:process id="Process_A" isExecutable="true">'
    '<bpmn:startEvent id="Start" /><bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="End" />'
    '<bpmn:endEvent id="End" />'
    '</bpmn:process></bpmn:definitions>'
)

URL = '/v1/wf/process-files'


class ProcessFilesApiTest(ApiTestCase):

    def prepare(self) -> None:
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        patcher = patch.object(process_files, '_config', return_value={
            'process_files_dir': self.root, 'process_files_git_commit': 'true'})
        patcher.start()
        self.addCleanup(patcher.stop)

        user = self.create_user()
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
            display_name='Jurek Testowy', groups=frozenset({'wf-admin'}))

        self._write('obieg/obieg.bpmn', BPMN)
        self._write('obieg/obieg-schema.json', '{"type": "object"}')
        self._write('obieg/obieg-uischema.json', '{}')
        self._write('obieg/process_model.json', '{"display_name": "Obieg"}')
        self._write('obieg/wydruk.html.j2', '<p>{{ nazwa }}</p>')
        self._write('obieg/.ukryty.bpmn', BPMN)

    def _write(self, rel: str, content: str) -> None:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _read(self, rel: str) -> str:
        with open(os.path.join(self.root, rel), encoding='utf-8') as f:
            return f.read()

    def _get(self, rel: str) -> dict:
        response = self.client.get(f'{URL}/content', params={'path': rel})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _put(self, rel: str, content: str, base: str | None):
        return self.client.put(f'{URL}/content',
                               json={'path': rel, 'content': content, 'base_sha256': base})

    # --- uprawnienia ------------------------------------------------------

    def test_requires_admin_group(self):
        self.current_user = self.current_user.model_copy(update={'groups': frozenset()})
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.get(f'{URL}/content', params={'path': 'obieg/obieg.bpmn'}).status_code, 403)
        self.assertEqual(self._put('obieg/obieg.bpmn', BPMN, None).status_code, 403)

    # --- listy ------------------------------------------------------------

    def test_lists_files_by_kind_without_hidden_and_metadata(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.text)
        paths = {f['path']: f['kind'] for f in response.json()['files']}
        self.assertEqual(paths, {
            'obieg/obieg.bpmn': 'bpmn',
            'obieg/obieg-schema.json': 'form',
            'obieg/obieg-uischema.json': 'form',
            'obieg/wydruk.html.j2': 'print',
        })
        bpmn_only = self.client.get(URL, params={'kind': 'bpmn'}).json()['files']
        self.assertEqual([f['path'] for f in bpmn_only], ['obieg/obieg.bpmn'])
        self.assertEqual(self.client.get(URL, params={'kind': 'exe'}).status_code, 422)

    def test_form_pairs_and_orphan_pairing(self):
        self._write('inny/proces-schema.json', '{"type": "object"}')
        self._write('inny/proces-uiscema.json', '{}')  # literówka: nie jest formularzem
        self._write('inny/ioschema.json', '{}')
        forms = self.client.get(f'{URL}/forms').json()['forms']
        self.assertIn({'dir': 'obieg', 'base': 'obieg', 'schemaPath': 'obieg/obieg-schema.json',
                       'uiSchemaPath': 'obieg/obieg-uischema.json'}, forms)
        # jeden schema bez pary + jeden ui bez pary w katalogu = para
        self.assertIn({'dir': 'inny', 'base': 'proces', 'schemaPath': 'inny/proces-schema.json',
                       'uiSchemaPath': 'inny/ioschema.json'}, forms)

    # --- ścieżki ----------------------------------------------------------

    def test_rejects_paths_outside_root_and_wrong_types(self):
        for bad in ('../poza.bpmn', 'obieg/../../poza.bpmn', 'obieg/process_model.json',
                    'obieg/plik.txt', 'obieg/.ukryty.bpmn', '.git/config.bpmn'):
            with self.subTest(path=bad):
                self.assertEqual(self.client.get(f'{URL}/content', params={'path': bad}).status_code,
                                 400)
                self.assertEqual(self._put(bad, BPMN, None).status_code, 400)

    def test_rejects_symlink_escape(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.symlink(outside, os.path.join(self.root, 'link'))
        self.assertEqual(self._put('link/x.bpmn', BPMN, None).status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(outside, 'x.bpmn')))

    def test_missing_file_is_404(self):
        response = self.client.get(f'{URL}/content', params={'path': 'obieg/brak.bpmn'})
        self.assertEqual(response.status_code, 404)

    # --- zapis i konflikty ------------------------------------------------

    def test_save_with_matching_sha_and_conflict_on_stale_sha(self):
        current = self._get('obieg/obieg.bpmn')
        changed = BPMN.replace('isExecutable="true"', 'isExecutable="true" name="Nowy"')

        response = self._put('obieg/obieg.bpmn', changed, current['sha256'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['created'])
        self.assertEqual(self._read('obieg/obieg.bpmn'), changed)

        stale = self._put('obieg/obieg.bpmn', BPMN, current['sha256'])
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self._read('obieg/obieg.bpmn'), changed)

    def test_existing_file_requires_base_sha(self):
        self.assertEqual(self._put('obieg/obieg.bpmn', BPMN, None).status_code, 409)

    def test_create_new_file_and_conflict_when_it_appears(self):
        response = self._put('nowy/nowy-schema.json', '{"type": "object"}', None)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['created'])
        self.assertEqual(self._put('nowy/nowy-schema.json', '{}', None).status_code, 409)
        self.assertEqual(self._put('nowy/usuniety.bpmn', BPMN, 'abc').status_code, 409)

    def test_no_temporary_files_left(self):
        current = self._get('obieg/obieg-uischema.json')
        self._put('obieg/obieg-uischema.json', '{"a": 1}', current['sha256'])
        leftovers = [n for n in os.listdir(os.path.join(self.root, 'obieg')) if n.startswith('.tmp-')]
        self.assertEqual(leftovers, [])

    # --- walidacja --------------------------------------------------------

    def test_validation_blocks_broken_content(self):
        cases = {
            'obieg/obieg.bpmn': '<bpmn:definitions',
            'obieg/obieg-schema.json': '{"type": ',
            'obieg/wydruk.html.j2': '{% if %}',
        }
        for rel, content in cases.items():
            with self.subTest(path=rel):
                base = self._get(rel)['sha256']
                self.assertEqual(self._put(rel, content, base).status_code, 422)
        # BPMN bez procesu też nie nadaje się do użycia
        base = self._get('obieg/obieg.bpmn')['sha256']
        no_process = ('<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
                      'id="d" targetNamespace="x"/>')
        self.assertEqual(self._put('obieg/obieg.bpmn', no_process, base).status_code, 422)

    def test_empty_ui_schema_allowed_but_not_empty_schema(self):
        base = self._get('obieg/obieg-uischema.json')['sha256']
        self.assertEqual(self._put('obieg/obieg-uischema.json', '', base).status_code, 200)
        base = self._get('obieg/obieg-schema.json')['sha256']
        self.assertEqual(self._put('obieg/obieg-schema.json', '  ', base).status_code, 422)

    def test_invalid_json_schema_is_saved_with_warning(self):
        base = self._get('obieg/obieg-schema.json')['sha256']
        response = self._put('obieg/obieg-schema.json', '{"type": 12}', base)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['warnings'])

    # --- git --------------------------------------------------------------

    def test_no_commit_when_root_is_not_git_repository(self):
        base = self._get('obieg/obieg-uischema.json')['sha256']
        body = self._put('obieg/obieg-uischema.json', '{"b": 1}', base).json()
        self.assertFalse(body['committed'])
        self.assertIsNone(body['git_error'])

    @unittest.skipUnless(shutil.which('git'), 'brak gita')
    def test_commit_with_user_as_author_when_root_is_git_repository(self):
        def git(*args):
            return subprocess.run(['git', '-C', self.root, *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'start')

        base = self._get('obieg/obieg-uischema.json')['sha256']
        body = self._put('obieg/obieg-uischema.json', '{"c": 1}', base).json()

        self.assertTrue(body['committed'], body)
        self.assertEqual(git('log', '-1', '--format=%an <%ae>'), 'Jurek Testowy <jurek@example.com>')
        self.assertEqual(git('log', '-1', '--format=%s'), 'Zmiana obieg/obieg-uischema.json (galwork-arena)')
        self.assertEqual(git('status', '--porcelain'), '')

    @unittest.skipUnless(shutil.which('git'), 'brak gita')
    def test_no_commit_when_root_is_subdirectory_of_repository(self):
        repo = os.path.dirname(self.root)
        sub = os.path.realpath(tempfile.mkdtemp(dir=repo))
        try:
            subprocess.run(['git', '-C', sub, 'init', '-q'], check=True)
            nested = os.path.join(sub, 'spiffworkflow')
            os.makedirs(nested)
            with patch.object(process_files, '_config',
                              return_value={'process_files_dir': nested}):
                body = self._put('a/a.bpmn', BPMN, None).json()
            self.assertFalse(body['committed'])
        finally:
            shutil.rmtree(sub, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
