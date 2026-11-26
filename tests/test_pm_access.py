# tests/test_pm_access.py
# 2026-11-24: API projektów PM wymaga logowania, zapis - administratora PM
# (pm/access.py). Wcześniej projects/forms/dashboard/calc działały bez tokenu.
from unittest import mock

from tests.base import ApiTestCase
from wf.schemas.wf_schemas import CurrentUser

PM = '/v1/pm/api'


def _user(groups=()):
  return CurrentUser(id=1, sub='sub-1', username='jurek', email='jurek@example.com',
                     display_name='jurek', groups=frozenset(groups))


class PmAccessTest(ApiTestCase):

  def prepare(self):
    self.create_user()
    patcher = mock.patch('wf.dependencies.ADMIN_GROUPS', frozenset({'galwork-admin'}))
    patcher.start()
    self.addCleanup(patcher.stop)

  def _anonymous(self):
    from router import app_pm
    from wf.dependencies import get_current_wf_user
    app_pm.dependency_overrides.pop(get_current_wf_user, None)

  def test_anonymous_is_rejected_everywhere(self):
    self._anonymous()
    calls = [
      ('get', f'{PM}/projects', None),
      ('post', f'{PM}/projects', {'name': 'X'}),
      ('patch', f'{PM}/projects/1', {'name': 'Y'}),
      ('delete', f'{PM}/projects/1', None),
      ('get', f'{PM}/forms', None),
      ('put', f'{PM}/projects/1/forms/karta-projektu', {'data': {}}),
      ('get', f'{PM}/projects/1/dashboard', None),
      ('post', f'{PM}/calc/pert', {'optimistic': 1, 'most_likely': 2, 'pessimistic': 3}),
    ]
    for method, url, body in calls:
      with self.subTest(method=method, url=url):
        kwargs = {'json': body} if body is not None else {}
        response = getattr(self.client, method)(url, **kwargs)
        self.assertIn(response.status_code, (401, 403), response.text)

  def test_logged_in_user_reads_but_cannot_write(self):
    self.current_user = _user()
    self.assertEqual(self.client.get(f'{PM}/projects').status_code, 200)
    self.assertEqual(self.client.get(f'{PM}/forms').status_code, 200)
    self.assertEqual(self.client.post(f'{PM}/projects', json={'name': 'X'}).status_code, 403)
    self.assertEqual(self.client.patch(f'{PM}/projects/1', json={'name': 'Y'}).status_code, 403)
    self.assertEqual(self.client.delete(f'{PM}/projects/1').status_code, 403)
    self.assertEqual(
      self.client.put(f'{PM}/projects/1/forms/karta-projektu', json={'data': {}}).status_code, 403)

  def test_pm_admin_creates_updates_and_deletes(self):
    self.current_user = _user(groups=('galwork-admin',))
    created = self.client.post(f'{PM}/projects', json={'name': 'Projekt A'})
    self.assertEqual(created.status_code, 201, created.text)
    pid = created.json()['id']
    self.assertEqual(self.client.patch(f'{PM}/projects/{pid}', json={'name': 'B'}).json()['name'], 'B')
    self.assertEqual(self.client.delete(f'{PM}/projects/{pid}').status_code, 204)

  def test_taiga_admin_groups_also_grant_pm_editor(self):
    self.current_user = _user(groups=('pm-admin',))
    with mock.patch('pm.access.section', return_value={'admin_groups': 'pm-admin'}):
      self.assertEqual(self.client.post(f'{PM}/projects', json={'name': 'P'}).status_code, 201)


class SelfRegistrationTest(ApiTestCase):

  def test_register_disabled_by_default(self):
    response = self.client.post('/v1/galwork/auth/register',
                                json={'email': 'x@example.com', 'password': 'Haslo-123456', 'name': 'X'})
    self.assertEqual(response.status_code, 403, response.text)
    response = self.client.post('/v1/pm/auth/register',
                                json={'email': 'x@example.com', 'password': 'Haslo-123456', 'name': 'X'})
    self.assertEqual(response.status_code, 403, response.text)
