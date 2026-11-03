# tests/test_wf_task_form_cache.py
"""
Testy wf/services/process_service.py::task_form - filtrowanie formularza
wg `x-permissions` (wf/services/form_permissions.py) i buforowanie wyniku
w Redis (core/redis_cache.py), serwowane przez `GET /tasks/{id}/form`
(wf/routers/human_task_router.py).

Redis nie jest tu żywą usługą - `cache_get_json`/`cache_set_json`
(core.redis_cache) są podmieniane na prosty słownik w pamięci (atrapa), bo
jedyne, co testujemy, to KONTRAKT (klucz czytany przed liczeniem, wynik
liczenia zapisywany pod tym kluczem), nie konkretny klient Redis.
"""
from __future__ import annotations

from unittest.mock import patch

from tests.base import ApiTestCase

from wf.models import ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser, ProcessModelVersionStatus

FORM_SCHEMA = {
    'title': 'Test',
    'type': 'object',
    'properties': {
        'widoczne_zawsze': {
            'type': 'string',
            'title': 'Widoczne zawsze',
            'x-permissions': {'Recenzja': 'editable', 'Podpis': 'readonly'},
        },
        'tylko_recenzja': {
            'type': 'string',
            'title': 'Tylko recenzja',
            'x-permissions': {'Recenzja': 'editable', 'Podpis': 'none'},
            'x-required-in-steps': ['Recenzja'],
        },
    },
}

FORM_UI_SCHEMA = {
    'ui:order': ['widoczne_zawsze', 'tylko_recenzja'],
    'tylko_recenzja': {'ui:widget': 'textarea'},
}


class _FakeRedis:
    """Słownik w pamięci podstawiany pod core.redis_cache.cache_get_json/
    cache_set_json - zob. docstring modułu."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def get(self, key, *_args, **_kwargs):
        return self.store.get(key)

    def set(self, key, value, *_args, **_kwargs):
        self.store[key] = value


class TaskFormCacheTest(ApiTestCase):

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
        self.fake_redis = _FakeRedis()

    def _create_model(self) -> str:
        """Jak tests.base.ApiTestCase.create_process_model, ale ze schematem
        formularza niosącym `x-permissions` (BPMN_PROCESS z tests/base.py ma
        już kroki 'Recenzja' -> 'Podpis', z tymi nazwami kluczowana jest
        macierz `x-permissions` w FORM_SCHEMA powyżej)."""
        from tests.base import BPMN_PROCESS

        model = ProcessModel(key='test-proces', name='Proces testowy')
        self.db.add(model)
        self.db.flush()
        version = ProcessModelVersion(
            process_model_id=model.id,
            version=1,
            status=ProcessModelVersionStatus.PUBLISHED,
            bpmn_process_id='proc_test',
            content_hash='sha256:xperm-test',
            # ten sam "master schema" pod obydwoma krokami
            # (task_recenzja/task_podpis) - dokładnie jak w rzeczywistych
            # obiegach RJSF (jeden plik schema.json na
            # cały obieg, x-permissions decyduje co widać w danym kroku).
            spec_json={
                'forms': {'task_recenzja': FORM_SCHEMA, 'task_podpis': FORM_SCHEMA},
                'uischemas': {'task_recenzja': FORM_UI_SCHEMA, 'task_podpis': FORM_UI_SCHEMA},
            },
        )
        self.db.add(version)
        self.db.flush()
        from wf.models import ProcessModelFile
        from wf.schemas.wf_schemas import ProcessFileType
        self.db.add(ProcessModelFile(version_id=version.id, file_name='proc_test.bpmn',
                                     file_type=ProcessFileType.BPMN, content=BPMN_PROCESS,
                                     sha256='sha256:test'))
        self.db.commit()
        self.db.refresh(model)
        return model.id

    def _start_instance_and_get_task_id(self) -> tuple[str, str]:
        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'test-proces', 'title': 'Sprawa testowa', 'data': {},
        })
        self.assertEqual(response.status_code, 201, response.text)
        instance_id = response.json()['id']
        task_id = self.client.get('/v1/wf/tasks/my').json()['items'][0]['id']
        return instance_id, task_id

    def test_form_is_filtered_for_current_step(self):
        self._create_model()
        _instance_id, task_id = self._start_instance_and_get_task_id()

        with patch('wf.services.process_service.cache_get_json', self.fake_redis.get), \
             patch('wf.services.process_service.cache_set_json', self.fake_redis.set):
            response = self.client.get(f'/v1/wf/tasks/{task_id}/form')

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        properties = body['json_schema']['properties']

        # Krok "Recenzja": obie wartości widoczne, 'tylko_recenzja' wymagane
        # (x-required-in-steps), żadne x-permissions/x-required-in-steps nie
        # wyciekło do odpowiedzi.
        self.assertEqual(set(properties), {'widoczne_zawsze', 'tylko_recenzja'})
        self.assertEqual(body['json_schema']['required'], ['tylko_recenzja'])
        for prop in properties.values():
            self.assertNotIn('x-permissions', prop)
            self.assertNotIn('x-required-in-steps', prop)
        self.assertNotIn('readOnly', properties['widoczne_zawsze'])

        # ui_schema: 'ui:order' przefiltrowany do pól, które przeszły (tu:
        # oba przeszły), widget dla 'tylko_recenzja' zachowany.
        self.assertEqual(body['ui_schema']['ui:order'], ['widoczne_zawsze', 'tylko_recenzja'])
        self.assertEqual(body['ui_schema']['tylko_recenzja']['ui:widget'], 'textarea')

    def test_second_step_hides_field_and_marks_other_readonly(self):
        self._create_model()
        instance_id, task_id = self._start_instance_and_get_task_id()
        lock_version = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']

        with patch('wf.services.process_service.cache_get_json', self.fake_redis.get), \
             patch('wf.services.process_service.cache_set_json', self.fake_redis.set):
            # Wykonaj krok "Recenzja" -> zadanie "Podpis" staje się aktywne.
            complete = self.client.post(f'/v1/wf/tasks/{task_id}/complete', json={
                'data': {'widoczne_zawsze': 'x', 'tylko_recenzja': 'y'},
                'expected_lock_version': lock_version,
            })
            self.assertEqual(complete.status_code, 200, complete.text)
            next_task_id = self.client.get('/v1/wf/tasks/my').json()['items'][0]['id']

            response = self.client.get(f'/v1/wf/tasks/{next_task_id}/form')

        self.assertEqual(response.status_code, 200, response.text)
        properties = response.json()['json_schema']['properties']

        # Krok "Podpis": 'tylko_recenzja' ma x-permissions['Podpis']='none' ->
        # wycięte; 'widoczne_zawsze' ma 'readonly' -> readOnly=true.
        self.assertEqual(set(properties), {'widoczne_zawsze'})
        self.assertTrue(properties['widoczne_zawsze']['readOnly'])
        self.assertNotIn('required', response.json()['json_schema'])
        self.assertEqual(response.json()['ui_schema']['ui:order'], ['widoczne_zawsze'])

    def test_result_is_served_from_cache_on_second_call(self):
        """Druga wizyta w to samo zadanie nie przelicza filtra ponownie -
        dostaje wynik zapisany w (atrapie) Redis przy pierwszej wizycie."""
        self._create_model()
        _instance_id, task_id = self._start_instance_and_get_task_id()

        import wf.services.form_permissions as form_permissions_module
        with patch('wf.services.process_service.cache_get_json', self.fake_redis.get), \
             patch('wf.services.process_service.cache_set_json', self.fake_redis.set), \
             patch('wf.services.process_service.filter_form_for_step',
                   wraps=form_permissions_module.filter_form_for_step) as filter_spy:
            first = self.client.get(f'/v1/wf/tasks/{task_id}/form')
            second = self.client.get(f'/v1/wf/tasks/{task_id}/form')

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()['json_schema'], second.json()['json_schema'])
        filter_spy.assert_called_once()
        self.assertEqual(len(self.fake_redis.store), 1)
