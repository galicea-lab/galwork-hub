# tests/test_wf_decisions.py
# testy generycznej warstwy decyzji gatewayów - parser BPMN,
# wstrzykiwanie pola wyboru do formularza (z domyślną opcją) i normalizacja
# wartości `chosen_path` przed uruchomieniem Spiffa.
from __future__ import annotations

import unittest

from tests.base import ApiTestCase

from wf.decisions import (
    DEFAULT_VALUE,
    Decision,
    DecisionOption,
    default_value,
    inject_decision,
    normalize_choice,
    read_decisions,
)
from wf.schemas.wf_schemas import CurrentUser, ProcessFileType

BPMN_DECISION = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:flowable="http://flowable.org/bpmn"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  id="def_1" targetNamespace="urn:galwork:test">
  <bpmn:process id="proc_test" isExecutable="true">
    <bpmn:startEvent id="start_1"/>
    <bpmn:sequenceFlow id="flow_1" sourceRef="start_1" targetRef="task_recenzja"/>
    <bpmn:userTask id="task_recenzja" name="Recenzja" flowable:candidateGroups="recenzenci"/>
    <bpmn:sequenceFlow id="flow_2" sourceRef="task_recenzja" targetRef="gateway_1"/>
    <bpmn:exclusiveGateway id="gateway_1" name="Wybór ścieżki (Recenzja)"
                           default="flow_default"/>
    <bpmn:sequenceFlow id="flow_ok" name="Zatwierdź" sourceRef="gateway_1"
                       targetRef="task_ok">
      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">chosen_path == 'flow_ok'</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="flow_default" name="Domyślnie" sourceRef="gateway_1"
                       targetRef="task_default"/>
    <bpmn:userTask id="task_ok" name="Zatwierdzenie" flowable:assignee="jurek"/>
    <bpmn:userTask id="task_default" name="Domyślna" flowable:assignee="jurek"/>
    <bpmn:sequenceFlow id="flow_ok_end" sourceRef="task_ok" targetRef="end_1"/>
    <bpmn:sequenceFlow id="flow_default_end" sourceRef="task_default" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


class DecisionParserTest(unittest.TestCase):
  def test_reads_simple_flow_id_decision_with_default(self):
    decisions = read_decisions(BPMN_DECISION)
    self.assertIn('task_recenzja', decisions)
    decision = decisions['task_recenzja']
    self.assertFalse(decision.blocked)
    self.assertEqual(decision.field, 'chosen_path')
    self.assertEqual([o.value for o in decision.options], [DEFAULT_VALUE, 'flow_ok'])
    self.assertEqual([o.label for o in decision.options], ['Domyślnie', 'Zatwierdź'])
    self.assertTrue(decision.options[0].is_default)
    self.assertEqual(default_value(decision.as_dict()), DEFAULT_VALUE)

  def test_compound_condition_is_blocked(self):
    guarded = BPMN_DECISION.replace(
      "chosen_path == 'flow_ok'",
      "(chosen_path == 'flow_ok') and (ISADMINMODE)")
    decision = read_decisions(guarded)['task_recenzja']
    self.assertTrue(decision.blocked)
    self.assertIn('ISADMINMODE', decision.block_reason or '')

  def test_condition_without_chosen_path_is_blocked(self):
    guarded = BPMN_DECISION.replace("chosen_path == 'flow_ok'", "kwota > 100")
    decision = read_decisions(guarded)['task_recenzja']
    self.assertTrue(decision.blocked)
    self.assertIn('chosen_path', decision.block_reason or '')

  def test_gateway_without_conditions_is_ignored(self):
    xml = BPMN_DECISION.replace(
      "<bpmn:conditionExpression xsi:type=\"bpmn:tFormalExpression\">"
      "chosen_path == 'flow_ok'</bpmn:conditionExpression>", '')
    self.assertEqual(read_decisions(xml), {})


class DecisionInjectionTest(unittest.TestCase):
  def setUp(self):
    self.decision = read_decisions(BPMN_DECISION)['task_recenzja'].as_dict()

  def test_injects_radio_with_default_selected(self):
    schema, ui = inject_decision({'type': 'object', 'properties': {
      'ocena': {'type': 'integer'}}}, {}, self.decision)
    field = schema['properties']['chosen_path']
    self.assertEqual(field['type'], 'string')
    self.assertEqual(field['default'], DEFAULT_VALUE)
    self.assertEqual([o['const'] for o in field['oneOf']], [DEFAULT_VALUE, 'flow_ok'])
    self.assertIn('(domyślna)', field['oneOf'][0]['title'])
    self.assertEqual(ui['chosen_path'], {'ui:widget': 'radio'})
    self.assertEqual(ui['ui:order'][0], 'chosen_path')

  def test_existing_field_is_not_overwritten(self):
    original = {'type': 'object', 'properties': {
      'chosen_path': {'type': 'integer', 'title': 'Biznesowe'}}}
    schema, ui = inject_decision(original, {}, self.decision)
    self.assertEqual(schema['properties']['chosen_path']['title'], 'Biznesowe')
    self.assertEqual(ui, {})

  def test_blocked_decision_is_not_injected(self):
    blocked = dict(self.decision, blocked=True)
    schema, ui = inject_decision({'type': 'object'}, {}, blocked)
    self.assertNotIn('chosen_path', schema.get('properties', {}))
    self.assertEqual(ui, {})


class NormalizeChoiceTest(unittest.TestCase):
  def test_values(self):
    self.assertIsNone(normalize_choice(DEFAULT_VALUE))
    self.assertIsNone(normalize_choice(None))
    self.assertEqual(normalize_choice('421'), 421)
    self.assertEqual(normalize_choice('Flow_5'), 'Flow_5')
    self.assertEqual(normalize_choice(''), None)


class DecisionIntegrationTest(ApiTestCase):
  """Pełny obieg: formularz z wyborem -> Spiff -> właściwa gałąź."""

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = CurrentUser(
      id=self.user.id,
      sub=self.user.keycloak_sub,
      username=self.user.name,
      email=self.user.email,
      display_name=self.user.name,
      groups=frozenset({'recenzenci'}),
    )
    self.create_process_model(bpmn=BPMN_DECISION)

  def _start_and_get_form(self):
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': 'test-proces',
      'title': 'Decyzja testowa',
      'data': {'kwota': 100},
    })
    self.assertEqual(response.status_code, 201, response.text)
    tasks = self.client.get('/v1/wf/tasks/my').json()
    self.assertEqual(tasks['total'], 1, tasks)
    task = tasks['items'][0]
    form = self.client.get(f"/v1/wf/tasks/{task['id']}/form").json()
    return task, form

  def _complete(self, task, form, chosen_path):
    response = self.client.post(f"/v1/wf/tasks/{task['id']}/complete", json={
      'data': {'ocena': 5, 'chosen_path': chosen_path},
      'expected_lock_version': form['process_lock_version'],
    })
    self.assertEqual(response.status_code, 200, response.text)

  def _next_task_name(self):
    tasks = self.client.get('/v1/wf/tasks/my').json()
    self.assertEqual(tasks['total'], 1, tasks)
    return tasks['items'][0]['name']

  def test_form_has_decision_with_default_selected(self):
    task, form = self._start_and_get_form()
    field = form['json_schema']['properties']['chosen_path']
    self.assertEqual(field['default'], DEFAULT_VALUE)
    self.assertEqual(field['oneOf'][0]['const'], DEFAULT_VALUE)
    self.assertEqual(form['form_data']['chosen_path'], DEFAULT_VALUE)
    self.assertEqual(form['ui_schema']['chosen_path'], {'ui:widget': 'radio'})

  def test_choosing_conditional_flow_routes_to_its_target(self):
    task, form = self._start_and_get_form()
    self._complete(task, form, 'flow_ok')
    self.assertEqual(self._next_task_name(), 'Zatwierdzenie')

  def test_default_choice_routes_to_default_target(self):
    task, form = self._start_and_get_form()
    self._complete(task, form, DEFAULT_VALUE)
    self.assertEqual(self._next_task_name(), 'Domyślna')


class BuildSpecDecisionsTest(unittest.TestCase):
  def test_build_spec_json_includes_decisions(self):
    from wf.services.process_service import build_spec_json

    spec = build_spec_json([('proc.bpmn', ProcessFileType.BPMN, BPMN_DECISION)])
    self.assertIn('decisions', spec)
    self.assertIn('task_recenzja', spec['decisions'])
    self.assertEqual(spec['decisions']['task_recenzja']['options'][1]['value'], 'flow_ok')
