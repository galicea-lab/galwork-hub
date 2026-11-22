# tests/test_wf_rollouts.py
# 2026-11-17:  wdrożenia obiegów - tryb testowy / pomocniczy / produkcyjny
# w jednostkach organizacyjnych na podstawie aktu (wf/services/rollouts.py).
from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from tests.test_edu_sealing import FakeSealValkey, make_pki
from tests.test_wf_instance_documents import NS, _Base
from tests.test_wf_seal_document import FakeSealConnector, repo_bpmn
from wf.models import OrgMembership, OrgUnit, ProcessEvent, ProcessInstance, ProcessRollout



def _service(element_id: str, handler: str, **props: str) -> str:
    extra = ''.join(f'<spiffworkflow:property name="{k}" value="{v}" />' for k, v in props.items())
    return (f'<bpmn:serviceTask id="{element_id}" name="{element_id}"><bpmn:extensionElements>'
            f'<spiffworkflow:properties><spiffworkflow:property name="handler" value="{handler}" />'
            f'{extra}</spiffworkflow:properties></bpmn:extensionElements></bpmn:serviceTask>')


# uproszczony obieg "Wdrożenie obiegów": decyzja -> dokument -> wdrożenie
WDROZENIE = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_w" targetNamespace="x">
  <bpmn:process id="Process_Wdrozenie" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_D" />
    <bpmn:userTask id="Task_D" name="Decyzja" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_D" targetRef="Doc" />
    {_service('Doc', 'dokumenty.glowny')}
    <bpmn:sequenceFlow id="f3" sourceRef="Doc" targetRef="Wdr" />
    {_service('Wdr', 'obiegi.wdrozenie', modelsField='Obiegi', modeField='Tryb', unitsField='Jednostki',
              fromField='Od', toField='Do', procedureField='Procedura dotychczasowa',
              actionField='Działanie')}
    <bpmn:sequenceFlow id="f4" sourceRef="Wdr" targetRef="Task_K" />
    <bpmn:userTask id="Task_K" name="Archiwum" />
    <bpmn:sequenceFlow id="f5" sourceRef="Task_K" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''

ZAMOWIENIA = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_z" targetNamespace="x">
  <bpmn:process id="Process_Zamowienia" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />
    <bpmn:userTask id="Task_01" name="Zamówienie" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class RolloutTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.publish('wdrozenie', 'Process_Wdrozenie', WDROZENIE)
        self.publish('zamowienia', 'Process_Zamowienia', ZAMOWIENIA)
        self.unit_a = OrgUnit(code='DZP', name='Dział Zamówień')
        self.unit_b = OrgUnit(code='KW', name='Kwestura')
        self.db.add_all([self.unit_a, self.unit_b])
        self.db.flush()
        self.unit_a1 = OrgUnit(code='DZP-1', name='Sekcja zamówień', parent_id=self.unit_a.id)
        self.db.add(self.unit_a1)
        self.db.commit()
        self.config = {'rollout_enforcement': 'warn'}
        patcher = mock.patch('wf.services.process_files._config', side_effect=lambda: self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- pomocnicze -------------------------------------------------------

    def member(self, unit: OrgUnit, user=None) -> None:
        self.db.add(OrgMembership(user_id=(user or self.other).id, org_unit_id=unit.id))
        self.db.commit()

    def decide(self, **fields) -> dict:
        """Akt wdrożeniowy (administrator) - zwraca instancję decyzji."""
        self.as_owner()
        decision = self.start('wdrozenie')
        self.upload(decision['id'], role='main')
        [task] = self.open_tasks(decision['id'])
        data = {'Obiegi': ['zamowienia'], 'Jednostki': [f'group:{self.unit_a.id}'],
                'Działanie': 'wdrożenie', **fields}
        self.complete(decision['id'], task, data)
        return self.client.get(f"/v1/wf/process-instances/{decision['id']}").json()

    def start_as_other(self, key: str = 'zamowienia'):
        self.as_other()
        return self.client.post('/v1/wf/process-instances', json={
            'process_model_key': key, 'title': 'Zamówienie 1'})

    def events(self, instance_id: str, kind: str) -> list[ProcessEvent]:
        self.db.expire_all()
        return [e for e in self.db.query(ProcessEvent).all()
                if str(e.process_instance_id) == instance_id and e.event_type == kind]

    # --- testy ------------------------------------------------------------

    def test_test_rollout_marks_instance_and_print(self):
        decision = self.decide(Tryb='testowy', Od='2020-01-01', Do='2099-12-31',
                               **{'Procedura dotychczasowa': 'zamówienia papierowe (instrukcja DZP)'})
        self.assertIsNone(decision['error_message'])
        rollout = self.db.query(ProcessRollout).one()
        self.assertEqual((rollout.mode.value, rollout.org_unit_ids), ('test', [str(self.unit_a.id)]))
        self.assertEqual(rollout.act_version_no, 1)

        self.member(self.unit_a1)  # jednostka podległa jest objęta wdrożeniem
        started = self.start_as_other()
        self.assertEqual(started.status_code, 201, started.text)
        body = started.json()
        self.assertEqual(body['usage_mode'], 'test')
        self.assertEqual(body['rollout']['previous_procedure'], 'zamówienia papierowe (instrukcja DZP)')

        printed = self.client.get(f"/v1/wf/process-instances/{body['id']}/print.pdf")
        self.assertEqual(printed.status_code, 200, printed.text)
        from wf.services import instance_print
        instance = self.db.get(ProcessInstance, uuid.UUID(body['id']))
        html = instance_print.build_print(self.db, instance, self.current_user, pdf=False).rendered.html
        self.assertIn('WYDRUK TESTOWY', html)
        self.assertIn('zamówienia papierowe', html)

    def test_start_outside_rollout_warns_or_is_refused(self):
        self.decide(Tryb='produkcyjny', Od='2020-01-01')
        self.member(self.unit_b)
        started = self.start_as_other()
        self.assertEqual(started.status_code, 201, started.text)  # warn
        self.assertIsNone(started.json()['usage_mode'])
        self.assertEqual(len(self.events(started.json()['id'], 'rollout_missing')), 1)

        self.config = {'rollout_enforcement': 'on'}
        refused = self.start_as_other()
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn('nie jest wdrożony', refused.json()['detail'])
        self.as_owner()  # administrator procesów - tryb deweloperski
        self.assertEqual(self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'zamowienia', 'title': 'dev'}).status_code, 201)

    def test_production_replaces_test_and_close(self):
        self.decide(Tryb='testowy', Od='2020-01-01', Do='2099-12-31',
                    **{'Procedura dotychczasowa': 'papier'})
        self.decide(Tryb='produkcyjny', Od='2021-01-01')
        test, production = sorted(self.db.query(ProcessRollout).all(), key=lambda r: r.valid_from)
        self.assertEqual(str(test.valid_to), '2021-01-01')
        self.assertIsNotNone(test.closed_at)
        self.member(self.unit_a)
        self.assertEqual(self.start_as_other().json()['usage_mode'], 'production')

        self.decide(Działanie='zakończenie', Od='2022-01-01')
        self.db.expire_all()
        production = self.db.get(ProcessRollout, production.id)
        self.assertEqual(str(production.valid_to), '2022-01-01')
        listed = self.client.get('/v1/wf/process-rollouts', params={'active_on': '2021-06-01'}).json()
        self.assertEqual([r['mode'] for r in listed], ['production'])

    def test_test_rollout_requires_end_and_previous_procedure(self):
        decision = self.decide(Tryb='testowy', Od='2020-01-01')
        self.assertIn('datę zakończenia', decision['error_message'])
        decision = self.decide(Tryb='testowy', Od='2020-01-01', Do='2099-12-31')
        self.assertIn('procedurę dotychczasową', decision['error_message'])
        self.assertEqual(self.db.query(ProcessRollout).count(), 0)

    def test_rollout_and_instance_mode_are_protected(self):
        self.decide(Tryb='produkcyjny', Od='2020-01-01')
        self.member(self.unit_a)
        instance_id = uuid.UUID(self.start_as_other().json()['id'])
        for statement in (update(ProcessRollout).values(mode='test'),
                          update(ProcessRollout).values(org_unit_ids=[]),
                          update(ProcessInstance).where(ProcessInstance.id == instance_id)
                          .values(usage_mode='test')):
            with self.assertRaises(DBAPIError):
                self.db.execute(statement)
                self.db.flush()
            self.db.rollback()

    def test_publication_is_logged_in_decision(self):
        decision = self.decide(Tryb='produkcyjny', Od='2020-01-01')
        self.as_owner()
        self.publish_new_version()
        logged = self.events(decision['id'], 'rollout_version_published')
        self.assertEqual([(e.payload['process_model_key'], e.payload['version']) for e in logged],
                         [('zamowienia', 2)])

    def publish_new_version(self):
        models = {m['key']: m for m in self.client.get('/v1/wf/process-models').json()}
        model_id = models['zamowienia']['id']
        version = self.client.post(f'/v1/wf/process-models/{model_id}/versions', json={
            'bpmn_process_id': 'Process_Zamowienia',
            'files': [{'file_name': 'z.bpmn', 'file_type': 'bpmn',
                       'content': ZAMOWIENIA.replace('Zamówienie"', 'Zamówienie v2"')}]}).json()
        response = self.client.post(f"/v1/wf/process-models/{model_id}/versions/{version['id']}/publish")
        self.assertEqual(response.status_code, 200, response.text)

    def test_test_case_is_not_sealed(self):
        valkey = FakeSealValkey(make_pki())
        for patcher in (mock.patch.dict(os.environ, {'SEAL_ENABLED': 'true'}),
                        mock.patch('wf.services.seal_service_tasks._seal_connector',
                                   side_effect=lambda: FakeSealConnector(valkey))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.publish('dokumenty', 'Process_Dokumenty', repo_bpmn())
        self.decide(Obiegi=['dokumenty'], Tryb='testowy', Od='2020-01-01', Do='2099-12-31',
                    **{'Procedura dotychczasowa': 'rejestr papierowy'})
        self.member(self.unit_a)
        repo = self.start_as_other('dokumenty').json()
        self.assertEqual(repo['usage_mode'], 'test')
        self.upload(repo['id'], role='main')
        woken = self.client.post(f"/v1/wf/process-instances/{repo['id']}/wake").json()
        self.assertIsNone(woken['error_message'])
        self.assertEqual(valkey.requests, [])
        skipped, = self.events(repo['id'], 'signature_completed')
        self.assertTrue(skipped.payload['skipped'])





class MarkUsageTest(unittest.TestCase):

    def test_marks(self):
        from wf.services.print_service import mark_usage
        html = '<html><head></head><body class="dokument"><p>treść</p></body></html>'
        self.assertEqual(mark_usage(html, None, None), html)
        self.assertEqual(mark_usage(html, 'production', None), html)
        auxiliary = mark_usage(html, 'auxiliary', 'oryginał papierowy <archiwum>')
        self.assertIn('dokument źródłowy: oryginał papierowy &lt;archiwum&gt;', auxiliary)
        self.assertNotIn('TEST', auxiliary)
        self.assertLess(auxiliary.index('galwork-tryb'), auxiliary.index('treść'))


if __name__ == '__main__':
    unittest.main()
