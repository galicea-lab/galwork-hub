# (ZAK.1): informacja o fakturze zakupu z ERPNext uruchamia obieg
# `faktura-zakupu`; akceptacja zapisuje opis w ERPNext i wykonuje akcję workflow.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from unittest import mock

from connector.base import ConnectorError
from tests.fixture_paths import fixture_model
from tests.base import ApiTestCase
from wf.models import HumanTask, ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser, ProcessModelVersionStatus
from wf.services import purchase_invoice_flow

MODEL_DIR = fixture_model('zakupy', 'faktura-zakupu')
SECRET = 'sekret-testowy'


class FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeErp:
    def __init__(self):
        self.config = FakeConfig(base_url='https://erp.test', approver_groups='faktury_zakupu',
                                 invoice_webhook_secret=SECRET, invoice_reject_action='Odrzuć')
        self.docs = {}
        self.calls = []
        self.fail = False

    def add(self, name, docstatus=0, **fields):
        self.docs[name] = {'name': name, 'docstatus': docstatus, 'supplier': 'SUP-1', 'supplier_name': 'DigitalOcean',
                           'bill_no': f'FV/{name}', 'bill_date': '2026-11-20', 'due_date': '2026-12-04',
                           'net_total': 100.0, 'grand_total': 123.0, 'currency': 'PLN', **fields}

    def get_doc(self, doctype, name):
        if self.fail:
            raise ConnectorError('ERPNext -> 502')
        return dict(self.docs[name])

    def get_list(self, doctype, *, filters=None, fields=None, limit=20):
        return [dict(d) for d in self.docs.values() if d['docstatus'] == 0][:limit]

    def update_doc(self, doctype, name, data):
        self.calls.append(('update', name, data))
        self.docs[name].update(data)
        return self.docs[name]

    def apply_workflow_action(self, doctype, name, action):
        self.calls.append(('workflow', name, action))
        self.docs[name]['docstatus'] = 1 if action == 'Zatwierdź' else 0
        return self.docs[name]

    def submit_doc(self, doctype, name):
        self.calls.append(('submit', name))


class PurchaseInvoiceFlowTest(ApiTestCase):

    def prepare(self) -> None:
        from wf.services.filesystem_sync import sync_folder
        self.users = {n: self.create_user(email=f'{n}@example.com', name=n, keycloak_sub=f'sub-{n}')
                      for n in ('system', 'opis', 'akc')}
        result = sync_folder(self.db, MODEL_DIR)
        self.assertEqual(result.status, 'draft-created', result.detail)
        for version in self.db.query(ProcessModelVersion).all():
            version.status = ProcessModelVersionStatus.PUBLISHED
        self.db.commit()
        self.erp = FakeErp()
        self.system = self.actor('system')
        for target, kwargs in (('wf.services.purchase_invoice_flow._erp', {'return_value': self.erp}),
                               ('wf.routers.erpnext_invoice_api.get_connector', {'return_value': self.erp}),
                               ('wf.services.system_actor.get_system_actor', {'return_value': self.system})):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.current_user = self.system

    def actor(self, name, *groups):
        user = self.users[name]
        return CurrentUser(id=user.id, sub=user.keycloak_sub, username=name, email=user.email,
                           display_name=name, groups=frozenset(groups))

    def notify(self, name, *, token=SECRET, signature=None, body=None):
        raw = json.dumps(body if body is not None else {'name': name}).encode()
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Galwork-Webhook-Token'] = token
        if signature:
            headers['X-Frappe-Webhook-Signature'] = signature
        return self.client.post('/v1/wf/erpnext/invoices/notify', content=raw, headers=headers)

    def open_task(self, instance_id):
        self.db.expire_all()
        tasks = [t for t in self.db.query(HumanTask).all()
                 if str(t.process_instance_id) == instance_id and t.status.value == 'open']
        return tasks[0] if tasks else None

    def complete(self, instance_id, user, data):
        self.current_user = user
        task = self.open_task(instance_id)
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete',
                                    json={'data': data, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 200, response.text)
        return task.name

    def instance(self, instance_id):
        self.current_user = self.system.model_copy(update={'groups': frozenset({'wf-admin'})})
        return self.client.get(f'/v1/wf/process-instances/{instance_id}').json()

    OPIS = {'projekt': 'PROJ-1', 'rodzaj_kosztu': '402 Usługi obce', 'opis_merytoryczny': 'Hosting galwork-hub',
            'zgodnosc': 'zgodna z zamówieniem lub umową'}

    def test_notify_starts_once_and_requires_secret(self):
        self.erp.add('PINV-1')
        response = self.notify('PINV-1')
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'started')
        detail = self.instance(body['process_instance_id'])
        self.assertEqual(detail['business_key'], 'PINV-1')
        self.assertEqual(detail['title'], 'Faktura FV/PINV-1 – DigitalOcean')
        self.assertEqual(detail['metadata']['kwota_brutto'], 123.0)
        self.assertEqual(self.notify('PINV-1').json()['status'], 'exists')
        self.assertEqual(self.notify('PINV-1', token='zly').status_code, 403)
        self.assertEqual(self.notify('', body={}).status_code, 400)
        # webhook ERPNext: podpis HMAC-SHA256 treści, bez tokenu
        self.erp.add('PINV-2')
        raw = json.dumps({'name': 'PINV-2'}).encode()
        signature = base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()
        self.assertEqual(self.notify('PINV-2', token=None, signature=signature).json()['status'], 'started')
        self.assertEqual(self.notify('PINV-2', token=None, signature='zly').status_code, 403)
        # faktura już zatwierdzona w ERPNext - bez obiegu
        self.erp.add('PINV-3', docstatus=1)
        self.assertEqual(self.notify('PINV-3').json()['status'], 'skipped')

    def test_accept_writes_description_and_runs_workflow(self):
        self.erp.add('PINV-1')
        instance_id = self.notify('PINV-1').json()['process_instance_id']
        self.assertEqual(self.complete(instance_id, self.actor('opis', 'faktury_opis'), self.OPIS), 'Opis merytoryczny')
        self.assertEqual(self.complete(instance_id, self.actor('akc', 'faktury_zakupu'), {}), 'Akceptacja')
        self.assertEqual(self.erp.calls, [
            ('update', 'PINV-1', {'remarks': 'Hosting galwork-hub', 'project': 'PROJ-1'}),
            ('workflow', 'PINV-1', 'Zatwierdź')])
        detail = self.instance(instance_id)
        self.assertEqual(detail['status'], 'completed')
        self.assertEqual(detail['metadata']['status_erp'], 'zaakceptowana')
        self.assertEqual(detail['metadata']['projekt'], 'PROJ-1')

    def test_correction_loop_and_rejection(self):
        self.erp.add('PINV-1')
        instance_id = self.notify('PINV-1').json()['process_instance_id']
        describer, approver = self.actor('opis', 'faktury_opis'), self.actor('akc', 'faktury_zakupu')
        self.complete(instance_id, describer, self.OPIS)
        self.complete(instance_id, approver, {'chosen_path': 'Flow_Poprawa', 'uwagi_akceptacji': 'inny projekt'})
        self.assertEqual(self.open_task(instance_id).name, 'Opis merytoryczny')
        self.complete(instance_id, describer, {**self.OPIS, 'projekt': 'PROJ-2'})
        self.complete(instance_id, approver, {'chosen_path': 'Flow_Odrzuc', 'uwagi_akceptacji': 'duplikat'})
        self.assertEqual(self.erp.calls, [('workflow', 'PINV-1', 'Odrzuć')])
        detail = self.instance(instance_id)
        self.assertEqual((detail['status'], detail['metadata']['status_erp']), ('completed', 'odrzucona'))

    def test_describer_cannot_accept(self):
        self.erp.add('PINV-1')
        instance_id = self.notify('PINV-1').json()['process_instance_id']
        self.complete(instance_id, self.actor('opis', 'faktury_opis'), self.OPIS)
        self.current_user = self.actor('opis', 'faktury_opis')
        task = self.open_task(instance_id)
        lock = self.client.get(f'/v1/wf/process-instances/{instance_id}').json()['lock_version']
        response = self.client.post(f'/v1/wf/tasks/{task.id}/complete', json={'data': {}, 'expected_lock_version': lock})
        self.assertEqual(response.status_code, 403, response.text)

    def test_erp_failure_retries_accept_step(self):
        self.erp.add('PINV-1')
        instance_id = self.notify('PINV-1').json()['process_instance_id']
        self.complete(instance_id, self.actor('opis', 'faktury_opis'), self.OPIS)
        self.erp.fail = True
        self.complete(instance_id, self.actor('akc', 'faktury_zakupu'), {})
        detail = self.instance(instance_id)
        self.assertIn('ERPNext', detail['error_message'] or '')
        self.assertIsNotNone(detail['next_wakeup_at'])
        self.erp.fail = False
        woken = self.client.post(f'/v1/wf/process-instances/{instance_id}/wake').json()
        self.assertEqual(woken['status'], 'completed')

    def test_sync_drafts_starts_missing(self):
        self.erp.add('PINV-1')
        self.notify('PINV-1')
        self.erp.add('PINV-2')
        self.erp.add('PINV-3', docstatus=1)
        results = purchase_invoice_flow.sync_drafts(self.db, self.system, dry_run=True)
        self.assertEqual({(r.invoice, r.status) for r in results}, {('PINV-1', 'exists'), ('PINV-2', 'would-start')})
        results = purchase_invoice_flow.sync_drafts(self.db, self.system)
        self.assertEqual({(r.invoice, r.status) for r in results}, {('PINV-1', 'exists'), ('PINV-2', 'started')})


if __name__ == '__main__':
    unittest.main()
