# tests/test_wf_legal_acts.py
# 2026-11-17: akty wewnętrzne - redakcje z datą wejścia w życie, relacje
# "zmienia"/"uchyla" i status prawny na dzień (wf/services/legal_acts.py,
# wf/routers/legal_act_router.py). Modele są uproszczonymi wersjami
# `regulacje/przygotuj-zarzadzenie` i `regulacje/dokumenty`.
from __future__ import annotations

import unittest
import uuid

from tests.test_wf_instance_documents import NS, PDF, PDF2, _Base
from wf.models import DocumentRelation, DocumentVersion, ProcessEvent


def _service(element_id: str, handler: str, **props: str) -> str:
    extra = ''.join(f'<spiffworkflow:property name="{k}" value="{v}" />' for k, v in props.items())
    return (f'<bpmn:serviceTask id="{element_id}" name="{element_id}"><bpmn:extensionElements>'
            f'<spiffworkflow:properties><spiffworkflow:property name="handler" value="{handler}" />'
            f'{extra}</spiffworkflow:properties></bpmn:extensionElements></bpmn:serviceTask>')


REPO = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_r" targetNamespace="x">
  <bpmn:process id="Process_Dokumenty" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Doc" />
    {_service('Doc', 'dokumenty.glowny')}
    <bpmn:sequenceFlow id="f2" sourceRef="Doc" targetRef="Od" />
    {_service('Od', 'akty.obowiazuje_od', dateField='Obowiązuje od')}
    <bpmn:sequenceFlow id="f3" sourceRef="Od" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Repozytorium dokumentów" />
    <bpmn:sequenceFlow id="f4" sourceRef="Task_02" targetRef="g" />
    <bpmn:exclusiveGateway id="g" default="f5" />
    <bpmn:sequenceFlow id="f5" sourceRef="g" targetRef="Task_02" />
    <bpmn:sequenceFlow id="f6" name="Akceptacja zmian" sourceRef="g" targetRef="Od">
      <bpmn:conditionExpression>chosen_path == 'f6'</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
  </bpmn:process>
</bpmn:definitions>'''

D = 'Szczegóły dokumentu'
ZARZADZENIE = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_z" targetNamespace="x">
  <bpmn:process id="Process_Zarzadzenie" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_06" />
    <bpmn:userTask id="Task_06" name="Publikacja" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_06" targetRef="Doc" />
    {_service('Doc', 'dokumenty.glowny')}
    <bpmn:sequenceFlow id="f3" sourceRef="Doc" targetRef="Od" />
    {_service('Od', 'akty.obowiazuje_od', dateField=f'{D}.Od kiedy obowiązuje')}
    <bpmn:sequenceFlow id="f4" sourceRef="Od" targetRef="Rel" />
    {_service('Rel', 'akty.relacje', dateField=f'{D}.Od kiedy obowiązuje',
              amendsField=f'{D}.Zmienia akty', repealsField=f'{D}.Uchyla akty')}
    <bpmn:sequenceFlow id="f5" sourceRef="Rel" targetRef="Send" />
    <bpmn:sendTask id="Send" name="Rejestracja"><bpmn:extensionElements><spiffworkflow:properties>
      <spiffworkflow:property name="startProcess" value="dokumenty" />
      <spiffworkflow:property name="startOnce" value="true" />
      <spiffworkflow:property name="startInput" value="dokument_id-&gt;dokument_id, {D}.Tytuł-&gt;Tytuł dokumentu, {D}.Nr dokumentu-&gt;Nr dokumentu, {D}.Typ dokumentu-&gt;Typ dokumentu, {D}.Od kiedy obowiązuje-&gt;Obowiązuje od" />
    </spiffworkflow:properties></bpmn:extensionElements></bpmn:sendTask>
    <bpmn:sequenceFlow id="f6" sourceRef="Send" targetRef="Task_07" />
    <bpmn:userTask id="Task_07" name="Archiwum" />
    <bpmn:sequenceFlow id="f7" sourceRef="Task_07" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class LegalActsTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.publish('zarzadzenie', 'Process_Zarzadzenie', ZARZADZENIE)

    # --- pomocnicze -------------------------------------------------------

    def publish_act(self, number: str, title: str, effective: str, *, amends=(), repeals=(),
                    content: bytes = PDF) -> tuple[str, str]:
        """Publikacja aktu: (id dokumentu, id instancji repozytorium)."""
        parent = self.start('zarzadzenie')
        document = self.upload(parent['id'], content=content, role='main').json()
        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task, {D: {
            'Tytuł': title, 'Nr dokumentu': number, 'Typ dokumentu': 'Zarządzenie',
            'Od kiedy obowiązuje': effective,
            'Zmienia akty': list(amends), 'Uchyla akty': list(repeals)}})
        state = self.client.get(f"/v1/wf/process-instances/{parent['id']}").json()
        self.assertIsNone(state['error_message'])
        children = self.client.get('/v1/wf/process-instances',
                                   params={'parent_instance_id': parent['id']}).json()['items']
        self.assertEqual(len(children), 1)
        return document['id'], children[0]['id']

    def state(self, document_id: str, on: str) -> dict:
        response = self.client.get(f'/v1/wf/acts/{document_id}', params={'on': on})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # treść redakcji aktu do podglądu w statusie sprawy.
    def act_content(self, document_id: str, **params):
        response = self.client.get(f'/v1/wf/acts/{document_id}/content', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    # --- testy ------------------------------------------------------------

    def test_status_follows_effective_date(self):
        act, repo = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        before = self.state(act, '2025-12-31')
        self.assertEqual((before['status_code'], before['status']), ('0', 'Nieustalony'))
        self.assertIsNone(before['valid_version_no'])

        now = self.state(act, '2026-06-01')
        self.assertEqual((now['status_code'], now['status']), ('1', 'Obowiązujący'))
        self.assertEqual(now['valid_version_no'], 1)
        self.assertEqual(now['valid_from'], '2026-01-01')
        self.assertEqual(now['number'], 'Zarządzenie 12/2026')
        self.assertEqual(now['repository_instance_id'], repo)
        self.assertEqual(now['versions'][0]['effective_from'], '2026-01-01')

    def test_amendment_consolidated_text_and_repeal(self):
        act, repo = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        amending, _ = self.publish_act('15/2026', 'Zmieniające zarządzenie 12/2026', '2026-07-01',
                                       amends=[act])

        changed = self.state(act, '2026-08-01')
        self.assertEqual((changed['status_code'], changed['status']), ('5', 'Zmieniony'))
        self.assertEqual(changed['valid_version_no'], 1)  # tekst jednolity jeszcze nie dodany
        self.assertEqual([(a['document_id'], a['effective_from'], a['target_version_no'])
                          for a in changed['amended_by']], [(amending, '2026-07-01', None)])
        self.assertEqual(self.state(act, '2026-06-30')['status_code'], '1')
        self.assertEqual([a['document_id'] for a in self.state(amending, '2026-08-01')['amends']], [act])
        # dziennik repozytorium zmienianego aktu
        self.db.expire_all()
        notes = [e for e in self.db.query(ProcessEvent).all()
                 if str(e.process_instance_id) == repo and e.event_type == 'document_relation_added']
        self.assertEqual(notes[0].payload['source_document_id'], amending)

        # tekst jednolity w repozytorium aktu zmienianego: nowa wersja + "Akceptacja zmian"
        self.upload(repo, content=PDF2, name='tekst-jednolity.pdf', document_id=act)
        [task] = self.open_tasks(repo)
        self.complete(repo, task, {'chosen_path': 'f6'})
        self.assertIsNone(self.client.get(f'/v1/wf/process-instances/{repo}').json()['error_message'])

        consolidated = self.state(act, '2026-08-01')
        self.assertEqual(consolidated['valid_version_no'], 2)
        self.assertEqual(consolidated['valid_from'], '2026-07-01')
        self.assertEqual(consolidated['amended_by'][0]['target_version_no'], 2)
        old = self.state(act, '2026-03-01')
        self.assertEqual((old['valid_version_no'], old['valid_to']), (1, '2026-07-01'))
        self.assertEqual([(v['version_no'], v['effective_from'], v['valid_to'])
                          for v in consolidated['versions']],
                         [(1, '2026-01-01', '2026-07-01'), (2, '2026-07-01', None)])

        repealing, _ = self.publish_act('30/2026', 'Uchylające', '2027-01-01', repeals=[act])
        repealed = self.state(act, '2027-02-01')
        self.assertEqual((repealed['status_code'], repealed['status']), ('3', 'Uchylony'))
        self.assertIsNone(repealed['valid_version_no'])
        self.assertEqual(repealed['repealed_by']['document_id'], repealing)
        self.assertEqual(self.state(act, '2026-12-31')['valid_to'], '2027-01-01')

    def test_new_wording_must_be_later(self):
        act, repo = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        self.upload(repo, content=PDF2, name='poprawka.pdf', document_id=act)
        [task] = self.open_tasks(repo)
        self.complete(repo, task, {'chosen_path': 'f6'})  # data z formularza = 2026-01-01
        error = self.client.get(f'/v1/wf/process-instances/{repo}').json()['error_message']
        self.assertIn('później niż poprzednia', error)
        self.db.expire_all()
        v2 = self.db.query(DocumentVersion).filter_by(document_id=uuid.UUID(act), version_no=2).one()
        self.assertIsNone(v2.effective_from)

    def test_relation_to_unregistered_document_is_rejected(self):
        parent = self.start('zarzadzenie')
        self.upload(parent['id'], role='main')
        stranger = self.start('zarzadzenie')
        foreign = self.upload(stranger['id'], role='main').json()
        [task] = self.open_tasks(parent['id'])
        self.complete(parent['id'], task, {D: {'Tytuł': 'Zmieniające', 'Od kiedy obowiązuje': '2026-07-01',
                                               'Zmienia akty': [foreign['id']]}})
        error = self.client.get(f"/v1/wf/process-instances/{parent['id']}").json()['error_message']
        self.assertIn('nie jest zarejestrowany w repozytorium', error)
        self.assertEqual(self.db.query(DocumentRelation).count(), 0)

    def test_manual_status_overrides_derived(self):
        act, repo = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        [task] = self.open_tasks(repo)
        self.complete(repo, task, {'Status': '2'})  # Unieważniony (ścieżka domyślna - zapis)
        state = self.state(act, '2026-06-01')
        self.assertEqual((state['status_code'], state['status'], state['status_manual']),
                         ('2', 'Unieważniony', True))

    # domyślnie treść obowiązująca w dniu `on`, historia przez `version_no`.
    def test_act_content_returns_effective_and_explicit_versions(self):
        act, repo = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        self.publish_act('15/2026', 'Zmieniające zarządzenie 12/2026', '2026-07-01', amends=[act])
        self.upload(repo, content=PDF2, name='tekst-jednolity.pdf', document_id=act)
        [task] = self.open_tasks(repo)
        self.complete(repo, task, {'chosen_path': 'f6'})

        old = self.act_content(act, on='2026-03-01')
        self.assertEqual(old.content, PDF)
        self.assertEqual(old.headers['X-Version-No'], '1')
        self.assertIn('inline', old.headers['Content-Disposition'])

        current = self.act_content(act, on='2026-08-01')
        self.assertEqual(current.content, PDF2)
        self.assertEqual(current.headers['X-Version-No'], '2')
        self.assertEqual(current.headers['X-Effective-From'], '2026-07-01')

        explicit = self.act_content(act, version_no=1, download=True)
        self.assertEqual(explicit.content, PDF)
        self.assertIn('attachment', explicit.headers['Content-Disposition'])
        self.assertEqual(explicit.headers['X-Content-SHA256'], old.headers['X-Content-SHA256'])

    # treść aktu podlega tym samym ograniczeniom co status.
    def test_act_content_respects_restricted_access(self):
        act, _ = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        secret, _ = self.publish_act('13/2026', 'Plan ochrony', '2026-01-01')
        self.assertEqual(self.act_content(secret).content, PDF)

        secret_repo = self.client.get(f'/v1/wf/acts/{secret}').json()['repository_instance_id']
        [task] = self.open_tasks(secret_repo)
        self.complete(secret_repo, task, {'Ograniczenia dostępu': {'do osób': 'jurek'}})

        self.as_other()
        self.assertEqual(self.client.get(f'/v1/wf/acts/{secret}/content').status_code, 404)
        self.assertEqual(self.act_content(act).content, PDF)

    def test_options_and_restricted_acts(self):
        act, _ = self.publish_act('12/2026', 'O cyfryzacji', '2026-01-01')
        secret, secret_repo = self.publish_act('13/2026', 'Plan ochrony', '2026-01-01')
        [task] = self.open_tasks(secret_repo)
        self.complete(secret_repo, task, {'Ograniczenia dostępu': {'do osób': 'jurek'}})

        options = self.client.get('/v1/wf/acts', params={'q': 'cyfryz'}).json()
        self.assertEqual(options, [{'value': act, 'label': 'Zarządzenie 12/2026 - O cyfryzacji'}])
        self.assertEqual(len(self.client.get('/v1/wf/acts').json()), 2)

        self.as_other()
        self.assertEqual([o['value'] for o in self.client.get('/v1/wf/acts').json()], [act])
        self.assertEqual(self.client.get(f'/v1/wf/acts/{secret}').status_code, 404)
        self.assertEqual(self.client.get(f'/v1/wf/acts/{act}').status_code, 200)


if __name__ == '__main__':
    unittest.main()
