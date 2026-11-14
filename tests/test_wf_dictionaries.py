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


# BEGIN 2026-11-13
class _FakeErpNext:
    """Konektor ERPNext z danymi w pamięci (get_list jak w connector/erpnext)."""

    def __init__(self, rows=None, error=None):
        self.rows = rows or {}
        self.error = error
        self.calls = []

    def get_list(self, doctype, *, filters=None, fields=None, limit=20):
        self.calls.append((doctype, filters, fields, limit))
        if self.error:
            raise self.error
        result = []
        for row in self.rows.get(doctype, []):
            ok = True
            for field, op, value in filters or []:
                if op == '=' and row.get(field, 0) != value:
                    ok = False
                if op == 'like' and value.strip('%').lower() not in str(row.get(field, '')).lower():
                    ok = False
                if op == 'not in' and row.get(field) in value:
                    ok = False
            if ok:
                result.append({k: row.get(k) for k in fields})
        return result


class ErpNextDictionaryTest(ApiTestCase):

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset())

    def _get(self, fake, params=None):
        from unittest import mock
        with mock.patch('wf.services.dictionary_service.get_erpnext_connector', return_value=fake):
            return self.client.get('/v1/wf/dictionaries/kontrahenci', params=params)

    def test_suppliers_and_customers_are_merged(self):
        fake = _FakeErpNext({
            'Supplier': [{'name': 'ACME', 'supplier_name': 'ACME sp. z o.o.'},
                         {'name': 'OLD', 'supplier_name': 'Stary dostawca', 'disabled': 1}],
            'Customer': [{'name': 'ACME', 'customer_name': 'ACME sp. z o.o.'},
                         {'name': 'UM', 'customer_name': 'Urząd Miasta'}],
        })
        response = self._get(fake)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [
            {'value': 'ACME', 'label': 'ACME sp. z o.o. (dostawca, odbiorca)'},
            {'value': 'UM', 'label': 'Urząd Miasta (odbiorca)'},
        ])

    def test_query_filters_by_label(self):
        fake = _FakeErpNext({'Supplier': [{'name': 'ACME', 'supplier_name': 'ACME'},
                                          {'name': 'BETA', 'supplier_name': 'Beta'}]})
        response = self._get(fake, {'q': 'bet'})
        self.assertEqual([o['value'] for o in response.json()], ['BETA'])
        self.assertIn(['supplier_name', 'like', '%bet%'], fake.calls[0][1])

    def test_missing_configuration_is_503(self):
        from connector.base import ConnectorConfigError
        response = self._get(_FakeErpNext(error=ConnectorConfigError('brak [erpnext]')))
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('ERPNext', response.json()['detail'])

    def test_k_kontrahenci_is_no_longer_not_connected(self):
        response = self.client.get('/v1/wf/dictionaries/k-kontrahenci')
        self.assertEqual(response.status_code, 404, response.text)
# -END


# BEGIN 2026-11-13
class InternalDictionaryOpenTaskTest(ApiTestCase):
    """Słownik wewnętrzny z filtrem kroku (`open_task_name`)."""

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset())

    def test_only_instances_waiting_in_step(self):
        from wf.models import HumanTask
        from wf.schemas.wf_schemas import HumanTaskStatus
        self.create_process_model(key='realizacja-procedury-skreslenia')
        ids = {}
        for title in ('Czeka - Kowalski', 'Inny krok - Nowak', 'Zakończone - Wiśniewska'):
            response = self.client.post('/v1/wf/process-instances', json={
                'process_model_key': 'realizacja-procedury-skreslenia', 'title': title, 'data': {}})
            self.assertEqual(response.status_code, 201, response.text)
            ids[title] = response.json()['id']
        import uuid
        for title, name, status in (
                ('Czeka - Kowalski', 'Czekanie na odwołanie', HumanTaskStatus.OPEN),
                ('Inny krok - Nowak', 'Doręczanie', HumanTaskStatus.OPEN),
                ('Zakończone - Wiśniewska', 'Czekanie na odwołanie', HumanTaskStatus.COMPLETED)):
            self.db.add(HumanTask(id=uuid.uuid4(), process_instance_id=uuid.UUID(ids[title]), bpmn_task_id='T',
                                  name=name, status=status))
        self.db.commit()
        response = self.client.get('/v1/wf/dictionaries/procedury-skreslenia-czeka-na-odwolanie')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([o['label'] for o in response.json()], ['Czeka - Kowalski'])
# -END


# BEGIN 2026-11-13
class ErpNextStructureDictionaryTest(ApiTestCase):
    """Pracownicy i środki trwałe z ERPNext (filtry aktywności i parametry adresu)."""

    def prepare(self) -> None:
        self.user = self.create_user()
        self.current_user = CurrentUser(
            id=self.user.id, sub=self.user.keycloak_sub, username=self.user.name,
            email=self.user.email, display_name=self.user.name, groups=frozenset())

    def _get(self, fake, slug, params=None):
        from unittest import mock
        with mock.patch('wf.services.dictionary_service.get_erpnext_connector', return_value=fake):
            return self.client.get(f'/v1/wf/dictionaries/{slug}', params=params)

    def test_employees_active_and_filtered_by_designation(self):
        fake = _FakeErpNext({'Employee': [
            {'name': 'HR-EMP-1', 'employee_name': 'Anna Prawnik', 'status': 'Active', 'designation': 'Radca prawny'},
            {'name': 'HR-EMP-2', 'employee_name': 'Jan Kowalski', 'status': 'Active', 'designation': 'Specjalista'},
            {'name': 'HR-EMP-3', 'employee_name': 'Ewa Była', 'status': 'Left', 'designation': 'Radca prawny'}]})
        response = self._get(fake, 'pracownicy', {'designation': 'radca prawny'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [{'value': 'HR-EMP-1', 'label': 'Anna Prawnik'}])
        self.assertNotIn(['disabled', '=', 0], fake.calls[0][1])

    def test_it_equipment_only_active_it_assets(self):
        fake = _FakeErpNext({'Asset': [
            {'name': 'AST-1', 'asset_name': 'Laptop Dell', 'asset_category': 'IT - laptopy', 'docstatus': 1, 'status': 'In Location'},
            {'name': 'AST-2', 'asset_name': 'Biurko', 'asset_category': 'Meble', 'docstatus': 1, 'status': 'In Location'},
            {'name': 'AST-3', 'asset_name': 'Stary serwer', 'asset_category': 'IT - serwery', 'docstatus': 1, 'status': 'Scrapped'}]})
        response = self._get(fake, 'sprzet-it')
        self.assertEqual(response.status_code, 200, response.text)
        filters = fake.calls[0][1]
        self.assertIn(['asset_category', 'like', 'IT%'], filters)
        self.assertIn(['status', 'not in', ['Scrapped', 'Sold']], filters)

    def test_locations_have_no_disabled_filter(self):
        fake = _FakeErpNext({'Location': [{'name': 'B1', 'location_name': 'Budynek A'}]})
        response = self._get(fake, 'lokalizacje')
        self.assertEqual(response.json(), [{'value': 'B1', 'label': 'Budynek A'}])
        self.assertEqual(fake.calls[0][1], [])
# -END
