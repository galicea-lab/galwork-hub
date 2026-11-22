# 2026-11-21: uprawnienia do dodawania aktu w repozytorium `dokumenty`
# ([wf] act_editor_groups oraz grupa Keycloak `act_editor`) i endpoint
# `/v1/wf/me/permissions` używany przez dashboard do pokazania przycisku.
from __future__ import annotations

import os
from unittest import mock

from tests.test_wf_instance_documents import REPO, _Base
from wf.schemas.wf_schemas import CurrentUser


class ActEditorPermissionsTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        # 2026-11-22: tylko na czas testu (wcześniej zmienna zostawała w procesie)
        env = mock.patch.dict(os.environ, {'WF_ACT_EDITOR_GROUPS': 'keycloak_admin'})
        env.start()
        self.addCleanup(env.stop)
        self.publish('dokumenty', 'Process_Dokumenty', REPO)

    def as_user(self, user, groups) -> None:
        self.current_user = CurrentUser(
            id=user.id, sub=user.keycloak_sub, username=user.name,
            email=user.email, display_name=user.name, groups=frozenset(groups))

    def start_dokumenty(self):
        return self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'dokumenty', 'title': 'Akt 1/2026'})

    def test_start_dokumenty_is_forbidden_for_user_outside_act_editor_groups(self):
        self.as_user(self.other, {'obcy'})

        response = self.start_dokumenty()

        self.assertEqual(response.status_code, 403, response.text)

    def test_start_dokumenty_is_allowed_for_configured_group(self):
        self.as_user(self.user, {'keycloak_admin'})

        response = self.start_dokumenty()

        self.assertEqual(response.status_code, 201, response.text)

    def test_start_dokumenty_is_allowed_for_keycloak_act_editor_group(self):
        self.as_user(self.other, {'act_editor'})

        response = self.start_dokumenty()

        self.assertEqual(response.status_code, 201, response.text)

    def test_other_process_model_start_is_not_affected(self):
        self.as_user(self.other, {'obcy'})
        self.create_process_model()

        response = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'test-proces', 'title': 'Inny proces'})

        self.assertEqual(response.status_code, 201, response.text)

    def test_permissions_endpoint_reflects_act_editor_membership(self):
        self.as_user(self.other, {'obcy'})

        denied = self.client.get('/v1/wf/me/permissions')

        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertFalse(denied.json()['can_create_act'])

        self.as_user(self.other, {'keycloak_admin'})

        allowed = self.client.get('/v1/wf/me/permissions')

        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertTrue(allowed.json()['can_create_act'])
