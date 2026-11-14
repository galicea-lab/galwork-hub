# tests/test_wf_call_activity.py
# 2026-11-13: callActivity wywołujący proces z innego modelu
# (wf/services/process_service.py::called_versions/pin_called_versions,
# wf/engine.py::build_spec).
"""Proces nadrzędny „Umowa” wywołuje proces „Podpis” z osobnego modelu."""
from __future__ import annotations

import json
import unittest

from tests.base import ApiTestCase
from wf.models import HumanTask
from wf.schemas.wf_schemas import CurrentUser

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
      'xmlns:camunda="http://camunda.org/schema/1.0/bpmn" '
      'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"')


def _form_props(prefix: str) -> str:
    return (f'<bpmn:extensionElements><spiffworkflow:properties>'
            f'<spiffworkflow:property name="formJsonSchemaFilename" value="{prefix}-schema.json" />'
            f'<spiffworkflow:property name="formUiSchemaFilename" value="{prefix}-uischema.json" />'
            f'</spiffworkflow:properties></bpmn:extensionElements>')


PARENT = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_umowa" targetNamespace="x">
  <bpmn:process id="Process_Umowa" isExecutable="true">
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />
    <bpmn:userTask id="Task_01" name="Przygotuj umowę">{_form_props('umowa')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="Call_Podpis" />
    <bpmn:callActivity id="Call_Podpis" name="Podpis" calledElement="Process_Podpis" />
    <bpmn:sequenceFlow id="f3" sourceRef="Call_Podpis" targetRef="Task_02" />
    <bpmn:userTask id="Task_02" name="Archiwizuj" />
    <bpmn:sequenceFlow id="f4" sourceRef="Task_02" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


def child_bpmn(io: bool = True) -> str:
    io_spec = ('<bpmn:ioSpecification>'
               '<bpmn:dataInput id="dokument" name="dokument" />'
               '<bpmn:dataOutput id="wynik_podpisu" name="wynik_podpisu" />'
               '<bpmn:inputSet><bpmn:dataInputRefs>dokument</bpmn:dataInputRefs></bpmn:inputSet>'
               '<bpmn:outputSet><bpmn:dataOutputRefs>wynik_podpisu</bpmn:dataOutputRefs></bpmn:outputSet>'
               '</bpmn:ioSpecification>') if io else ''
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {NS} id="d_podpis" targetNamespace="x">
  <bpmn:process id="Process_Podpis" isExecutable="true">
    {io_spec}
    <bpmn:startEvent id="s" />
    <bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="Task_01" />
    <bpmn:userTask id="Task_01" name="Podpisz dokument" camunda:candidateGroups="kancelaria">{_form_props('podpis')}</bpmn:userTask>
    <bpmn:sequenceFlow id="f2" sourceRef="Task_01" targetRef="e" />
    <bpmn:endEvent id="e" />
  </bpmn:process>
</bpmn:definitions>'''


def _schema(title: str, field: str, steps: list[str]) -> str:
    return json.dumps({'title': title, 'type': 'object', 'required': [field], 'properties': {
        field: {'type': 'string', 'title': field,
                'x-permissions': {step: 'editable' for step in steps}}}})


class CallActivityTest(ApiTestCase):

    def prepare(self) -> None:
        user = self.create_user()
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
            display_name=user.name, groups=frozenset({'wf-admin', 'kancelaria'}))

    # --- pomocnicze -----------------------------------------------------

    def _model(self, key: str) -> str:
        response = self.client.post('/v1/wf/process-models', json={'key': key, 'name': key})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def _version(self, model_id: str, process_id: str, bpmn: str, forms: dict[str, str]) -> str:
        files = [{'file_name': f'{process_id}.bpmn', 'file_type': 'bpmn', 'content': bpmn}]
        for prefix, schema in forms.items():
            files += [{'file_name': f'{prefix}-schema.json', 'file_type': 'json_schema', 'content': schema},
                      {'file_name': f'{prefix}-uischema.json', 'file_type': 'ui_schema', 'content': '{}'}]
        response = self.client.post(f'/v1/wf/process-models/{model_id}/versions',
                                    json={'bpmn_process_id': process_id, 'files': files})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def _publish(self, model_id: str, version_id: str):
        return self.client.post(f'/v1/wf/process-models/{model_id}/versions/{version_id}/publish')

    def _child(self, model_id: str, title: str = 'Podpis v1', io: bool = True) -> str:
        version_id = self._version(model_id, 'Process_Podpis', child_bpmn(io),
                                   {'podpis': _schema(title, 'wynik_podpisu', ['Podpisz dokument'])})
        self.assertEqual(self._publish(model_id, version_id).status_code, 200)
        return version_id

    def _parent(self, model_id: str):
        version_id = self._version(model_id, 'Process_Umowa', PARENT,
                                   {'umowa': _schema('Umowa', 'dokument', ['Przygotuj umowę'])})
        return version_id, self._publish(model_id, version_id)

    def _open_task(self, instance_id: str) -> HumanTask:
        self.db.expire_all()
        tasks = [t for t in self.db.query(HumanTask).all()
                 if str(t.process_instance_id) == instance_id and t.status.value in ('open', 'claimed')]
        self.assertEqual(len(tasks), 1, [t.name for t in tasks])
        return tasks[0]

    def _complete(self, instance_id: str, task: HumanTask, data: dict):
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        return self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                json={'data': data, 'expected_lock_version': lock})

    def _start(self) -> str:
        response = self.client.post('/v1/wf/process-instances',
                                    json={'process_model_key': 'umowa', 'title': 'Umowa 1'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    # --- testy ------------------------------------------------------------

    def test_publishing_parent_requires_published_child(self):
        _version, response = self._parent(self._model('umowa'))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Process_Podpis', response.json()['detail'])

    def test_publish_warns_when_child_has_no_io_specification(self):
        self._child(self._model('podpis'), io=False)
        _version, response = self._parent(self._model('umowa'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['warnings']), 1)
        self.assertIn('ioSpecification', response.json()['warnings'][0])

    def test_flow_through_child_model(self):
        child_version = self._child(self._model('podpis'))
        _version, response = self._parent(self._model('umowa'))
        self.assertEqual(response.json()['warnings'], [])
        instance_id = self._start()

        task = self._open_task(instance_id)
        self.assertEqual(task.name, 'Przygotuj umowę')
        self.assertIsNone(task.bpmn_process_id)
        done = self._complete(instance_id, task, {'dokument': 'umowa.pdf', 'pesel': '90010112345'})
        self.assertEqual(done.status_code, 200, done.text)

        # zadanie podprocesu: proces, wersja modelu i formularz z modelu "podpis"
        sub = self._open_task(instance_id)
        self.assertEqual((sub.name, sub.bpmn_task_id, sub.bpmn_process_id),
                         ('Podpisz dokument', 'Task_01', 'Process_Podpis'))
        self.assertEqual(str(sub.form_version_id), child_version)
        form = self.client.get(f'/v1/wf/tasks/{sub.id}/form')
        self.assertEqual(form.status_code, 200, form.text)
        self.assertEqual(form.json()['json_schema']['title'], 'Podpis v1')
        self.assertIn('wynik_podpisu', form.json()['json_schema']['properties'])
        # ioSpecification: podproces widzi tylko `dokument`, bez danych rodzica
        self.assertEqual(form.json()['form_data'].get('dokument'), 'umowa.pdf')
        self.assertNotIn('pesel', form.json()['form_data'])

        # walidacja wg schematu podprocesu (wymagane `wynik_podpisu`)
        self.assertEqual(self._complete(instance_id, sub, {}).status_code, 422)
        done = self._complete(instance_id, sub, {'wynik_podpisu': 'podpisany', 'smieci': 1})
        self.assertEqual(done.status_code, 200, done.text)

        back = self._open_task(instance_id)
        self.assertEqual(back.name, 'Archiwizuj')
        self.assertIsNone(back.form_version_id)
        data = self.client.get(f'/v1/wf/tasks/{back.id}/form').json()['form_data']
        self.assertEqual(data.get('wynik_podpisu'), 'podpisany')
        self.assertNotIn('smieci', data)

    def test_child_task_uses_child_assignment(self):
        self._child(self._model('podpis'))
        self._parent(self._model('umowa'))
        instance_id = self._start()
        self._complete(instance_id, self._open_task(instance_id), {'dokument': 'x'})
        sub = self._open_task(instance_id)
        # Task_01 rodzica jest dla wszystkich, Task_01 podprocesu - dla "kancelaria"
        self.current_user = self.current_user.model_copy(update={'groups': frozenset()})
        response = self._complete(instance_id, sub, {'wynik_podpisu': 'podpisany'})
        self.assertEqual(response.status_code, 403, response.text)

    def test_called_version_is_pinned_at_publish(self):
        child_model = self._model('podpis')
        v1 = self._child(child_model, 'Podpis v1')
        parent_model = self._model('umowa')
        self._parent(parent_model)
        v2 = self._child(child_model, 'Podpis v2')  # nowa wersja modelu wywoływanego

        def child_title() -> tuple[str, str]:
            instance_id = self._start()
            self._complete(instance_id, self._open_task(instance_id), {'dokument': 'x'})
            sub = self._open_task(instance_id)
            title = self.client.get(f'/v1/wf/tasks/{sub.id}/form').json()['json_schema']['title']
            return str(sub.form_version_id), title

        self.assertEqual(child_title(), (v1, 'Podpis v1'))
        self._parent(parent_model)  # ponowna publikacja rodzica przypina v2
        self.assertEqual(child_title(), (v2, 'Podpis v2'))


if __name__ == '__main__':
    unittest.main()
