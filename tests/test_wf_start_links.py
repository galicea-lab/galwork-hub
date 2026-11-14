# tests/test_wf_start_links.py
# 2026-11-13: osobna instancja uruchamiana z procesu - sendTask
# `startProcess` (automatycznie) i `quickAction` zadania (przycisk).
"""Postępowanie zakłada teczkę sprawy jako osobną instancję (nie czeka na nią)."""
from __future__ import annotations

import unittest

from tests.base import ApiTestCase
from wf.models import HumanTask, ProcessEvent, ProcessInstance
from wf.schemas.wf_schemas import CurrentUser

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
      'xmlns:camunda="http://camunda.org/schema/1.0/bpmn" '
      'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"')


def _props(**props: str) -> str:
    items = ''.join(f'<spiffworkflow:property name="{k}" value="{v}" />' for k, v in props.items())
    return f'<bpmn:extensionElements><spiffworkflow:properties>{items}</spiffworkflow:properties></bpmn:extensionElements>'


def parent_bpmn(send_first: bool = False, quick: dict | None = None) -> str:
    send = ('<bpmn:sendTask id="Send_Teczka" name="Załóż teczkę">'
            + _props(startProcess='teczka', startInput='opis->Krótki opis sprawy, dane.nr->Kogo dotyczy.Numer',
                     startTitle="Teczka: {{ data['Krótki opis sprawy'] }}")
            + '</bpmn:sendTask>')
    task1 = f'<bpmn:userTask id="Task_01" name="Wszczęcie" camunda:candidateGroups="dziekanat">{_props(**quick) if quick else ""}</bpmn:userTask>'
    if send_first:
        flow = ('<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Send_Teczka" />' + send +
                '<bpmn:sequenceFlow id="f2" sourceRef="Send_Teczka" targetRef="Task_01" />' + task1 +
                '<bpmn:sequenceFlow id="f3" sourceRef="Task_01" targetRef="Task_02" />')
    else:
        flow = ('<bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />' + task1 +
                '<bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="Send_Teczka" />' + send +
                '<bpmn:sequenceFlow id="f3" sourceRef="Send_Teczka" targetRef="Task_02" />')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_p" targetNamespace="x">
  <bpmn:process id="Process_Postepowanie" isExecutable="true">
    <bpmn:startEvent id="s" />{flow}
    <bpmn:userTask id="Task_02" name="Decyzja" />
    <bpmn:sequenceFlow id="f4" sourceRef="Task_02" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


CHILD = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_t" targetNamespace="x">
  <bpmn:process id="Process_Teczka" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_T" />
    <bpmn:userTask id="Task_T" name="Teczka otwarta" />
    <bpmn:sequenceFlow id="f2" sourceRef="Task_T" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


class StartLinksTest(ApiTestCase):

    def prepare(self) -> None:
        user = self.create_user()
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
            display_name=user.name, groups=frozenset({'wf-admin', 'dziekanat'}))

    def _publish(self, key: str, process_id: str, bpmn: str) -> None:
        model = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key.title()})
        self.assertEqual(model.status_code, 201, model.text)
        version = self.client.post(f"/v1/wf/process-models/{model.json()['id']}/versions", json={
            'bpmn_process_id': process_id,
            'files': [{'file_name': f'{key}.bpmn', 'file_type': 'bpmn', 'content': bpmn}]})
        self.assertEqual(version.status_code, 201, version.text)
        published = self.client.post(
            f"/v1/wf/process-models/{model.json()['id']}/versions/{version.json()['id']}/publish")
        self.assertEqual(published.status_code, 200, published.text)

    def _start(self) -> str:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'postepowanie', 'title': 'Postępowanie 7', 'business_key': 'S-7'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def _open(self, instance_id: str) -> HumanTask:
        self.db.expire_all()
        tasks = [t for t in self.db.query(HumanTask).all()
                 if str(t.process_instance_id) == instance_id and t.status.value == 'open']
        self.assertEqual(len(tasks), 1)
        return tasks[0]

    def _complete(self, instance_id: str, data: dict):
        task = self._open(instance_id)
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                    json={'data': data, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 200, response.text)

    def _children(self, instance_id: str) -> list[dict]:
        response = self.client.get('/v1/wf/process-instances',
                                   params={'parent_instance_id': instance_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items']

    def _events(self, instance_id: str, event_type: str) -> list[ProcessEvent]:
        self.db.expire_all()
        return [e for e in self.db.query(ProcessEvent).all()
                if str(e.process_instance_id) == instance_id and e.event_type == event_type]

    # --- sendTask startProcess --------------------------------------------

    def test_send_task_starts_independent_instance(self):
        self._publish('teczka', 'Process_Teczka', CHILD)
        self._publish('postepowanie', 'Process_Postepowanie', parent_bpmn())
        parent_id = self._start()
        self._complete(parent_id, {'opis': 'Skreślenie', 'dane': {'nr': '12345'}, 'pesel': '9001'})

        # rodzic nie czeka na teczkę
        self.assertEqual(self._open(parent_id).name, 'Decyzja')
        children = self._children(parent_id)
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(child['title'], 'Teczka: Skreślenie')
        self.assertEqual(child['business_key'], 'S-7')
        self.assertEqual(child['parent_instance_id'], parent_id)
        # tylko dane z startInput (bez pesel)
        self.assertEqual(child['summary'], {'Krótki opis sprawy': 'Skreślenie',
                                            'Kogo dotyczy': {'Numer': '12345'}})
        self.assertEqual(self._open(child['id']).name, 'Teczka otwarta')
        self.assertEqual(len(self._events(parent_id, 'child_instance_started')), 1)

        # kolejny krok rodzica nie uruchamia teczki drugi raz
        self._complete(parent_id, {})
        self.assertEqual(len(self._children(parent_id)), 1)

    def test_send_task_at_start(self):
        self._publish('teczka', 'Process_Teczka', CHILD)
        self._publish('postepowanie', 'Process_Postepowanie', parent_bpmn(send_first=True))
        parent_id = self._start()
        self.assertEqual(len(self._children(parent_id)), 1)

    def test_unpublished_child_does_not_block_parent(self):
        self._publish('postepowanie', 'Process_Postepowanie', parent_bpmn())
        parent_id = self._start()
        self._complete(parent_id, {'opis': 'X'})
        self.assertEqual(self._open(parent_id).name, 'Decyzja')
        self.assertEqual(self._children(parent_id), [])
        failed = self._events(parent_id, 'child_instance_failed')
        self.assertEqual(len(failed), 1)
        self.assertIn('teczka', failed[0].payload['error'])

    # --- quickAction ------------------------------------------------------

    def test_quick_action_button(self):
        self._publish('teczka', 'Process_Teczka', CHILD)
        self._publish('postepowanie', 'Process_Postepowanie',
                      parent_bpmn(quick={'quickAction': 'teczka', 'startInput': 'opis'}))
        parent_id = self._start()
        task = self._open(parent_id)

        form = self.client.get(f'/v1/wf/tasks/{task.id}/form')
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(form.json()['quick_actions'],
                         [{'process_model_key': 'teczka', 'label': 'Teczka'}])

        self.assertEqual(self.client.post(f'/v1/wf/tasks/{task.id}/quick-actions/inny').status_code, 409)
        self.current_user = self.current_user.model_copy(update={'groups': frozenset()})
        self.assertEqual(self.client.post(f'/v1/wf/tasks/{task.id}/quick-actions/teczka').status_code, 403)
        self.current_user = self.current_user.model_copy(update={'groups': frozenset({'dziekanat'})})

        response = self.client.post(f'/v1/wf/tasks/{task.id}/quick-actions/teczka')
        self.assertEqual(response.status_code, 201, response.text)
        child = response.json()
        self.assertEqual(child['parent_instance_id'], parent_id)
        self.assertEqual(child['parent_task_id'], str(task.id))
        self.assertEqual(child['title'], 'Teczka: Postępowanie 7')
        # zadanie rodzica dalej otwarte - akcja nie wykonuje kroku
        self.assertEqual(self._open(parent_id).id, task.id)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(ProcessInstance, task.process_instance_id))

    def test_quick_action_gone_after_task_completed(self):
        self._publish('teczka', 'Process_Teczka', CHILD)
        self._publish('postepowanie', 'Process_Postepowanie',
                      parent_bpmn(quick={'quickAction': 'teczka'}))
        parent_id = self._start()
        task = self._open(parent_id)
        self._complete(parent_id, {})
        response = self.client.post(f'/v1/wf/tasks/{task.id}/quick-actions/teczka')
        self.assertEqual(response.status_code, 409, response.text)


if __name__ == '__main__':
    unittest.main()
