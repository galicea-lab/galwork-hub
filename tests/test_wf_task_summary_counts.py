# tests/test_wf_task_summary_counts.py
# 2026-11-25: liczniki dla dashboardu w jednym żądaniu: GET /tasks/my/summary,
# GET /process-instances/counts, klucz modelu i filtr obiegów w GET /tasks/my.
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser


def _user(user, groups=()) -> CurrentUser:
  return CurrentUser(id=user.id, sub=user.keycloak_sub, username=user.name, email=user.email,
                     display_name=user.name, groups=frozenset(groups))


class TaskSummaryAndCountsTest(ApiTestCase):

  def prepare(self) -> None:
    self.user = self.create_user()
    self.current_user = _user(self.user, {'recenzenci'})
    self.create_process_model(key='proces-a')
    self.create_process_model(key='proces-b')
    self._start('proces-a')
    self._start('proces-a')
    self._start('proces-b')

  def _start(self, key: str) -> str:
    response = self.client.post('/v1/wf/process-instances', json={
      'process_model_key': key, 'title': 'Sprawa', 'data': {}})
    self.assertEqual(response.status_code, 201, response.text)
    return response.json()['id']

  def test_my_tasks_have_model_key_and_filter(self):
    items = self.client.get('/v1/wf/tasks/my').json()['items']
    self.assertEqual(sorted(i['process_model_key'] for i in items), ['proces-a', 'proces-a', 'proces-b'])
    page = self.client.get('/v1/wf/tasks/my', params={'process_model_key': 'proces-b'}).json()
    self.assertEqual(page['total'], 1)
    both = self.client.get('/v1/wf/tasks/my',
                           params=[('process_model_key', 'proces-a'), ('process_model_key', 'proces-b'),
                                   ('limit', 1)]).json()
    self.assertEqual((both['total'], len(both['items'])), (3, 1))

  def test_summary(self):
    from wf.models import HumanTask
    task = self.db.query(HumanTask).first()
    task.due_at = datetime.now(UTC) - timedelta(days=1)
    self.db.commit()
    summary = self.client.get('/v1/wf/tasks/my/summary').json()
    self.assertEqual(summary['total'], 3)
    self.assertEqual(summary['overdue'], 1)
    self.assertEqual(summary['by_process_model'], {'proces-a': 2, 'proces-b': 1})
    # zgodność z listą
    self.assertEqual(self.client.get('/v1/wf/tasks/my').json()['total'], summary['total'])
    self.assertEqual(self.client.get('/v1/wf/tasks/my', params={'overdue': True}).json()['total'], 1)

  def test_summary_respects_task_access(self):
    other = self.create_user(email='inny@example.com', name='inny', keycloak_sub='sub-2')
    self.current_user = _user(other)
    self.assertEqual(self.client.get('/v1/wf/tasks/my/summary').json(),
                     {'total': 0, 'overdue': 0, 'by_process_model': {}})

  def test_counts(self):
    params = [('process_model_key', 'proces-a'), ('process_model_key', 'proces-b'),
              ('process_model_key', 'nieznany')]
    counts = self.client.get('/v1/wf/process-instances/counts', params=params).json()['counts']
    self.assertEqual(counts, {'proces-a': 2, 'proces-b': 1, 'nieznany': 0})
    # inny użytkownik nie widzi cudzych spraw
    other = self.create_user(email='inny@example.com', name='inny', keycloak_sub='sub-2')
    self.current_user = _user(other)
    counts = self.client.get('/v1/wf/process-instances/counts', params=params).json()['counts']
    self.assertEqual(counts, {'proces-a': 0, 'proces-b': 0, 'nieznany': 0})

  def test_counts_requires_keys_and_limits_them(self):
    self.assertEqual(self.client.get('/v1/wf/process-instances/counts').status_code, 422)
    params = [('process_model_key', f'k-{i}') for i in range(51)]
    self.assertEqual(self.client.get('/v1/wf/process-instances/counts', params=params).status_code, 422)
