# tests/test_wf_dictionaries.py
"""
Testy wf/routers/dictionary_api.py i wf/routers/user_directory_api.py -
backend pól `AsyncSelectWidget` .
"""
from __future__ import annotations

from tests.base import ApiTestCase

from wf.schemas.wf_schemas import CurrentUser


class DictionaryApiTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id,
            sub=self.user.keycloak_sub,
            username=self.user.name,
            email=self.user.email,
            display_name=self.user.name,
            groups=frozenset(),
        )

    def test_unknown_slug_is_404(self):
        response = self.client.get('/v1/wf/dictionaries/nie-ma-takiego')
        self.assertEqual(response.status_code, 404, response.text)

    def test_not_connected_slug_is_501(self):
        # 'companies' jest w NOT_CONNECTED - brak konektora w repo (zob.
        # zaleznosci.md, sekcja "odwołania zewnętrzne").
        response = self.client.get('/v1/wf/dictionaries/companies')
        self.assertEqual(response.status_code, 501, response.text)
        self.assertIn('konektora', response.json()['detail'])

    def test_internal_dictionary_not_seeded_is_503(self):
        # 'kierunki' jest zarejestrowany w INTERNAL_DICTIONARIES, ale żaden
        # ProcessModel o key='kierunki' nie istnieje w (testowej) bazie.
        response = self.client.get('/v1/wf/dictionaries/kierunki')
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('kierunki', response.json()['detail'])

    def test_internal_dictionary_lists_process_instance_titles(self):
        # Zasiewa ProcessModel o key='kierunki' (konwencja z
        # dictionary_service.INTERNAL_DICTIONARIES) i dwie instancje -
        # jedną z dopasowaniem `q`, jedną bez.
        self.create_process_model(key='kierunki')
        for title in ('Informatyka', 'Pedagogika'):
            response = self.client.post('/v1/wf/process-instances', json={
                'process_model_key': 'kierunki',
                'title': title,
                'data': {},
            })
            self.assertEqual(response.status_code, 201, response.text)

        response = self.client.get('/v1/wf/dictionaries/kierunki')
        self.assertEqual(response.status_code, 200, response.text)
        labels = {item['label'] for item in response.json()}
        self.assertEqual(labels, {'Informatyka', 'Pedagogika'})

        response = self.client.get('/v1/wf/dictionaries/kierunki', params={'q': 'info'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item['label'] for item in response.json()], ['Informatyka'])

    def test_instytuty_alias_shares_process_model_with_instytuty_uczelni(self):
        self.create_process_model(key='instytuty')
        self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'instytuty',
            'title': 'Instytut Nauk Technicznych',
            'data': {},
        })
        for slug in ('instytuty', 'instytuty-uczelni'):
            response = self.client.get(f'/v1/wf/dictionaries/{slug}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                [item['label'] for item in response.json()], ['Instytut Nauk Technicznych'])

    def test_requires_token(self):
        from wf.dependencies import get_current_wf_user
        for sub_app in self.sub_apps:
            sub_app.dependency_overrides.pop(get_current_wf_user, None)
        response = self.client.get('/v1/wf/dictionaries/kierunki')
        self.assertEqual(response.status_code, 401, response.text)


class UserDirectoryApiTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id,
            sub=self.user.keycloak_sub,
            username=self.user.name,
            email=self.user.email,
            display_name=self.user.name,
            groups=frozenset(),
        )
        self.create_user(email='anna@example.com', name='Anna Kowalska', keycloak_sub='sub-2')
        from wf.models import OrgUnit
        self.db.add(OrgUnit(code='dziekanat', name='Dziekanat'))
        self.db.commit()

    def test_lists_users_and_groups(self):
        response = self.client.get('/v1/wf/users')
        self.assertEqual(response.status_code, 200, response.text)
        values = {item['value'] for item in response.json()}
        self.assertTrue(any(v.startswith('user:') for v in values))
        self.assertTrue(any(v.startswith('group:') for v in values))

    def test_filters_by_query(self):
        response = self.client.get('/v1/wf/users', params={'q': 'anna'})
        self.assertEqual(response.status_code, 200, response.text)
        labels = [item['label'] for item in response.json()]
        self.assertEqual(labels, ['Anna Kowalska'])
