# tests/test_print.py
# 2026-11-16: wydruki PDF z formularzy obiegów (wf/services/print_service.py,
# wf/services/instance_print.py, wf/routers/print_router.py).
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser, ProcessFileType
from wf.services import print_service as ps
from wf.services import process_files

BPMN = '''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:flowable="http://flowable.org/bpmn"
  xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"
  id="defs_druk" targetNamespace="urn:galwork:test">
  <bpmn:process id="Process_Druk" isExecutable="true">
    <bpmn:startEvent id="start_1"/>
    <bpmn:sequenceFlow id="f1" sourceRef="start_1" targetRef="Task_01"/>
    <bpmn:userTask id="Task_01" name="Wniosek" flowable:candidateGroups="wnioskodawcy">
      <bpmn:extensionElements><spiffworkflow:properties>
        <spiffworkflow:property name="formJsonSchemaFilename" value="druk-schema.json"/>
        <spiffworkflow:property name="formUiSchemaFilename" value="druk-uischema.json"/>
      </spiffworkflow:properties></bpmn:extensionElements>
    </bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
'''

SCHEMA = {
  'type': 'object',
  'x-print': [
    {'template': 'druk_Task_01_A.html.j2', 'label': 'Decyzja A', 'tasks': ['Task_01'],
     'title': 'Decyzja', 'when': {'Rodzaj': 'A'}},
    {'template': 'druk_Task_01_B.html.j2', 'label': 'Decyzja B', 'tasks': ['Task_01'],
     'when': {'Rodzaj': 'B'}},
    {'template': 'druk_Task_09.html.j2', 'tasks': ['Task_09']},
  ],
  'properties': {
    'Rodzaj': {'type': 'string', 'title': 'Rodzaj',
               'oneOf': [{'const': 'A', 'title': 'Rodzaj A'}, {'const': 'B', 'title': 'Rodzaj B'}]},
    'Dane': {'type': 'object', 'title': 'Dane wnioskodawcy', 'properties': {
      'Imię': {'type': 'string'},
      'Zgoda': {'type': 'boolean', 'title': 'Zgoda RODO'},
      'Ukryte': {'type': 'string'},
    }},
    'Pozycje': {'type': 'array', 'items': {'type': 'object', 'properties': {
      'Nazwa': {'type': 'string'}, 'Kwota': {'type': 'number'}}}},
  },
}
UISCHEMA = {'ui:order': ['Dane', '*'], 'Dane': {'Ukryte': {'ui:widget': 'hidden'}}}

TEMPLATE_A = '''{% extends "_base/strona.html.j2" %}{% import "_base/makra.j2" as m %}
{% block tresc %}<p class="osoba">{{ m.w(f, "Dane", "Imię") }}</p><p>{{ meta.uczelnia }}</p>{% endblock %}'''
LIB_PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>{{ meta.tytul }}</title></head>
<body>{% block tresc %}{% endblock %}</body></html>'''
LIB_MACROS = '''{% macro w(dane, sekcja, pole) %}{{ (dane.get(sekcja) or {}).get(pole) or '' }}{% endmacro %}'''


def _fields_by_label(fields):
  return {f.label: f for f in fields}


class PrintServiceTest(unittest.TestCase):

  def test_parse_x_print_accepts_object_list_and_templates_key(self):
    self.assertEqual([e.template for e in ps.parse_x_print({'x-print': {'template': 'a.html.j2'}})],
                     ['a.html.j2'])
    self.assertEqual(ps.parse_x_print({'x-print': {'templates': ['b.html.j2']}})[0].label, 'b')
    self.assertEqual(len(ps.parse_x_print(SCHEMA)), 3)
    self.assertEqual(ps.parse_x_print({'x-print': 7}), [])

  def test_entry_matches_task_and_soft_when(self):
    a, b, other = ps.parse_x_print(SCHEMA)
    self.assertTrue(ps.entry_matches(a, 'Task_01', {'Rodzaj': 'A'}))
    self.assertFalse(ps.entry_matches(b, 'Task_01', {'Rodzaj': 'A'}))
    self.assertFalse(ps.entry_matches(other, 'Task_01', {}))
    # wariant nieustalony (puste pole) - oba wydruki do wyboru
    self.assertTrue(ps.entry_matches(a, 'Task_01', {}) and ps.entry_matches(b, 'Task_01', {'Rodzaj': ''}))
    nested = ps.PrintEntry(template='x', label='x', when=(('Systemowe/Kod', ('ZBB',)),))
    self.assertTrue(ps.entry_matches(nested, None, {'Systemowe': {'Kod': 'ZBB'}}))
    self.assertFalse(ps.entry_matches(nested, None, {'Systemowe': {'Kod': 'ZNO'}}))

  def test_version_entries_prefers_linked_schema_and_falls_back_to_all(self):
    forms = {'druk-schema': json.dumps(SCHEMA),
             'inny-schema': json.dumps({'x-print': [{'template': 'inny.html.j2'}]}),
             'jinja-schema': '{"x-print": {{ niepoprawny }}'}
    linked = ps.version_entries(forms, {'Task_01': {'schema': 'druk-schema.json'}}, 'Task_01')
    self.assertEqual([e.template for e in linked][:2], ['druk_Task_01_A.html.j2', 'druk_Task_01_B.html.j2'])
    self.assertNotIn('inny.html.j2', [e.template for e in linked])
    unlinked = ps.version_entries(forms, {}, 'Task_05')
    self.assertIn('inny.html.j2', [e.template for e in unlinked])

  def test_format_fields_order_labels_hidden_and_tables(self):
    data = {'Rodzaj': 'B', 'Dane': {'Imię': 'Anna', 'Zgoda': True, 'Ukryte': 'tajne'},
            'Pozycje': [{'Nazwa': 'Papier', 'Kwota': 12.5}]}
    fields = ps.format_fields(SCHEMA, UISCHEMA, data)
    self.assertEqual([f.key for f in fields], ['Dane', 'Rodzaj', 'Pozycje'])
    dane = _fields_by_label(fields)['Dane wnioskodawcy']
    self.assertEqual(dane.kind, 'section')
    inner = _fields_by_label(dane.fields)
    self.assertEqual(inner['Zgoda RODO'].value, 'Tak')
    self.assertNotIn('Ukryte', inner)
    self.assertEqual(_fields_by_label(fields)['Rodzaj'].value, 'Rodzaj B')
    table = _fields_by_label(fields)['Pozycje']
    self.assertEqual((table.kind, table.columns, table.rows), ('table', ['Nazwa', 'Kwota'], [['Papier', '12,5']]))

  def test_format_fields_follows_matching_if_then_branch(self):
    schema = {'type': 'object', 'properties': {'Rodzaj': {'type': 'string'}},
              '$defs': {'sekcja-a': {'type': 'object', 'title': 'Sekcja A',
                                     'properties': {'Data': {'type': 'string', 'title': 'Data'}}}},
              'allOf': [{'if': {'properties': {'Rodzaj': {'const': 'A'}}, 'required': ['Rodzaj']},
                         'then': {'properties': {'A': {'$ref': '#/$defs/sekcja-a'}}}},
                        {'if': {'properties': {'Rodzaj': {'const': 'B'}}},
                         'then': {'properties': {'B': {'type': 'string'}}}}]}
    fields = ps.format_fields(schema, {}, {'Rodzaj': 'A', 'A': {'Data': '2026-11-16'}})
    self.assertEqual([f.key for f in fields], ['Rodzaj', 'A'])
    self.assertEqual(fields[1].fields[0].value, '16.11.2026')

  def test_format_fields_without_schema_uses_data(self):
    fields = ps.format_fields(None, None, {'kwota': 100, '_wewn': 1, 'chosen_path': 'x'})
    self.assertEqual([(f.label, f.value) for f in fields], [('kwota', '100')])

  def test_render_escapes_data_and_uses_version_then_library(self):
    lib = tempfile.mkdtemp()
    self.addCleanup(shutil.rmtree, lib, ignore_errors=True)
    os.makedirs(os.path.join(lib, '_base'))
    for name, content in (('strona.html.j2', LIB_PAGE), ('makra.j2', LIB_MACROS)):
      with open(os.path.join(lib, '_base', name), 'w', encoding='utf-8') as f:
        f.write(content)
    sources = [ps.mapping_source({'a.html.j2': TEMPLATE_A}), ps.dir_source(lib)]
    rendered = ps.render_html(sources=sources, template='a.html.j2',
                              data={'Dane': {'Imię': '<script>x</script>'}},
                              meta={'uczelnia': 'Uczelnia', 'znak': 'Z/1', 'tytul': 'T'},
                              title='Decyzja')
    self.assertIn('&lt;script&gt;', rendered.html)
    self.assertNotIn('<script>', rendered.html)
    self.assertEqual((rendered.title, rendered.file_name), ('Decyzja Z/1', 'Decyzja_Z_1.pdf'))

  def test_missing_template_and_path_traversal(self):
    lib = tempfile.mkdtemp()
    self.addCleanup(shutil.rmtree, lib, ignore_errors=True)
    source = ps.dir_source(lib)
    self.assertIsNone(source('../../etc/passwd.j2'))
    self.assertIsNone(source('.ukryty.j2'))
    with self.assertRaises(ps.PrintError) as ctx:
      ps.render_html(sources=[source], template='brak.html.j2', data={}, meta={})
    self.assertEqual(ctx.exception.status, 404)

  def test_default_template_renders_fields(self):
    rendered = ps.render_html(sources=ps.template_sources(None, None), template=None,
                              data={'Rodzaj': 'A', 'Dane': {'Imię': 'Anna'}}, schema=SCHEMA,
                              ui_schema=UISCHEMA, meta={'uczelnia': 'Uczelnia Testowa', 'tytul': 'Obieg'})
    self.assertIn('Uczelnia Testowa', rendered.html)
    self.assertIn('Rodzaj A', rendered.html)
    self.assertIn('counter(pages)', rendered.html)

  def test_pdf_blocks_external_resources(self):
    try:
      import weasyprint  # noqa: F401
    except ImportError:  # pragma: no cover
      self.skipTest('WeasyPrint niedostępny')
    fetcher = ps._url_fetcher()
    fetch = getattr(fetcher, 'fetch', fetcher)
    with self.assertRaises(Exception):
      fetch('http://169.254.169.254/latest/meta-data')
    with self.assertRaises(Exception):
      fetch('file:///etc/passwd')
    pdf = ps.html_to_pdf('<p>Zażółć gęślą jaźń</p><img src="http://127.0.0.1:1/x.png">')
    self.assertTrue(pdf.startswith(b'%PDF'))


class _Base(ApiTestCase):
  """Model obiegu z szablonami wczytany z katalogu tymczasowego (jak
  synchronizacja z galwork-models) i opublikowany."""

  def prepare(self) -> None:
    self.root = os.path.realpath(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
    patcher = patch.object(process_files, '_config', return_value={
      'process_files_dir': self.root, 'process_files_git_commit': 'false'})
    patcher.start()
    self.addCleanup(patcher.stop)
    self._write('druk/druk.bpmn', BPMN)
    self._write('druk/druk-schema.json', json.dumps(SCHEMA, ensure_ascii=False))
    self._write('druk/druk-uischema.json', json.dumps(UISCHEMA))
    self._write('druk/druk_Task_01_A.html.j2', TEMPLATE_A)
    self._write('druk/druk_Task_01_B.html.j2', '<p>B</p>')
    self._write('druk/process_model.json', '{"display_name": "Obieg z wydrukiem"}')
    self._write('wspolne/druki/_base/strona.html.j2', LIB_PAGE)
    self._write('wspolne/druki/_base/makra.j2', LIB_MACROS)
    self._write('wspolne/druki/README.md', 'opis')

    self.user = self.create_user()
    self.other = self.create_user(email='obcy@example.com', name='obcy', keycloak_sub='sub-obcy')
    self.as_user()

  def _write(self, rel: str, content: str) -> None:
    path = os.path.join(self.root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
      f.write(content)

  def as_user(self, groups=frozenset({'wnioskodawcy'})):
    self.current_user = CurrentUser(id=self.user.id, sub='sub-1', username='jurek',
                                    email='jurek@example.com', display_name='Jurek', groups=groups)

  def as_other(self):
    self.current_user = CurrentUser(id=self.other.id, sub='sub-obcy', username='obcy',
                                    email='obcy@example.com', display_name='Obcy')

  def publish(self):
    from wf.models import ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessModelVersionStatus
    from wf.services.filesystem_sync import sync_all

    results = sync_all(self.db, self.root)
    self.assertEqual([r.status for r in results], ['draft-created'], results)
    version = self.db.query(ProcessModelVersion).one()
    version.status = ProcessModelVersionStatus.PUBLISHED
    self.db.commit()
    return version


class PrintSyncTest(_Base):

  def test_sync_stores_templates_and_library_in_version(self):
    version = self.publish()
    files = {f.file_name: f.file_type for f in version.files}
    self.assertEqual(files['druk_Task_01_A.html.j2'], ProcessFileType.OTHER)
    self.assertEqual(files['_base/makra.j2'], ProcessFileType.OTHER)
    self.assertNotIn('README.md', files)
    self.assertEqual(process_files.file_kind('makra.j2'), 'print')


class PrintApiTest(_Base):

  def start(self, rodzaj='A'):
    self.publish()
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'druk', 'title': 'Sprawa', 'business_key': 'SPR/7', 'data': {'Rodzaj': rodzaj}})
    self.assertEqual(response.status_code, 201, response.text)
    return response.json()['id']

  def test_list_and_download(self):
    instance_id = self.start('A')
    response = self.client.get(f'/v1/wf/process-instances/{instance_id}/prints')
    self.assertEqual(response.status_code, 200, response.text)
    items = response.json()['items']
    self.assertEqual([i['key'] for i in items], ['druk_Task_01_A.html.j2', '__default__'])
    self.assertEqual(items[0]['bpmn_task_id'], 'Task_01')

    pdf = self.client.get(f'/v1/wf/process-instances/{instance_id}/print.pdf',
                          params={'template': 'druk_Task_01_A.html.j2'})
    self.assertEqual(pdf.status_code, 200, pdf.text)
    self.assertEqual(pdf.headers['content-type'], 'application/pdf')
    self.assertIn('Decyzja_SPR_7.pdf', pdf.headers['content-disposition'])
    self.assertTrue(pdf.content.startswith(b'%PDF'))

    default = self.client.get(f'/v1/wf/process-instances/{instance_id}/print.pdf')
    self.assertEqual(default.status_code, 200, default.text)

    wrong = self.client.get(f'/v1/wf/process-instances/{instance_id}/print.pdf',
                            params={'template': 'druk_Task_01_B.html.j2'})
    self.assertEqual(wrong.status_code, 404)

  def test_task_form_with_x_print_still_loads(self):
    self.start('A')
    task = self.client.get('/v1/wf/tasks/my').json()['items'][0]
    form = self.client.get(f"/v1/wf/tasks/{task['id']}/form")
    self.assertEqual(form.status_code, 200, form.text)
    self.assertIn('Dane', form.json()['json_schema']['properties'])

  def test_print_requires_relation_to_instance(self):
    instance_id = self.start('A')
    self.as_other()
    self.assertEqual(self.client.get(f'/v1/wf/process-instances/{instance_id}/prints').status_code, 403)
    self.current_user = CurrentUser(id=self.other.id, sub='sub-obcy', username='obcy',
                                    display_name='Admin', groups=frozenset({'wf-admin'}))
    self.assertEqual(self.client.get(f'/v1/wf/process-instances/{instance_id}/prints').status_code, 200)

  def test_archive_creates_document_and_new_versions(self):
    from wf.models import Document, ProcessInstanceDocument

    instance_id = self.start('A')
    paperless = MagicMock()
    paperless.stage_bytes.return_value = os.path.join(self.root, 'staged.pdf')
    paperless.sha256_of.return_value = '0' * 64
    paperless.upload_document.return_value = 'task-1'
    with patch('connector.paperless.get_connector', return_value=paperless):
      first = self.client.post(f'/v1/wf/process-instances/{instance_id}/print',
                               json={'template': 'druk_Task_01_A.html.j2'})
      second = self.client.post(f'/v1/wf/process-instances/{instance_id}/print',
                                json={'template': 'druk_Task_01_A.html.j2'})
    self.assertEqual(first.status_code, 201, first.text)
    self.assertEqual(second.json()['id'], first.json()['id'])
    self.assertEqual(second.json()['current_version_no'], 2)
    self.assertEqual(second.json()['current_version']['ingest_status'], 'submitted')
    link = self.db.query(ProcessInstanceDocument).one()
    self.assertEqual(link.role.value, 'main')
    self.assertEqual(self.db.query(Document).one().doc_type, 'wydruk')

  def test_preview_requires_admin_and_renders_unsaved_content(self):
    body = {'path': 'druk/druk_Task_01_A.html.j2', 'data': {'Dane': {'Imię': 'Ola'}}, 'format': 'html'}
    self.assertEqual(self.client.post('/v1/wf/print/preview', json=body).status_code, 403)
    self.as_user(groups=frozenset({'wf-admin'}))
    response = self.client.post('/v1/wf/print/preview', json=body)
    self.assertEqual(response.status_code, 200, response.text)
    self.assertIn('Ola', response.text)
    self.assertIn("default-src 'none'", response.headers['content-security-policy'])

    edited = self.client.post('/v1/wf/print/preview', json={
      **body, 'content': '{% extends "_base/strona.html.j2" %}{% block tresc %}NOWE{% endblock %}'})
    self.assertIn('NOWE', edited.text)
    bad = self.client.post('/v1/wf/print/preview', json={**body, 'content': '{% if %}'})
    self.assertEqual(bad.status_code, 422)
    pdf = self.client.post('/v1/wf/print/preview', json={
      'schema_path': 'druk/druk-schema.json', 'uischema_path': 'druk/druk-uischema.json',
      'data': {'Rodzaj': 'B'}})
    self.assertEqual(pdf.status_code, 200, pdf.text)
    self.assertTrue(pdf.content.startswith(b'%PDF'))


if __name__ == '__main__':  # pragma: no cover
  unittest.main()
