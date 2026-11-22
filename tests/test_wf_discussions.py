# tests/test_wf_discussions.py
# 2026-11-17: linki do dyskusji o sprawie w komunikatorze (wf/services/discussions.py).
from __future__ import annotations

import unittest
from unittest import mock

from tests.test_wf_instance_documents import REPO, _Base


class DiscussionTest(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.chat = {'provider': 'Mattermost',
                     'start_url': 'https://chat.example.pl/uczelnia/channels/sprawy?s={business_key}'}
        patcher = mock.patch('wf.services.discussions._config', side_effect=lambda: self.chat)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.publish('dokumenty', 'Process_Dokumenty', REPO)
        self.instance = self.client.post('/v1/wf/process-instances', json={
            'process_model_key': 'dokumenty', 'title': 'Zarządzenie 12', 'business_key': 'ZR/12 2026'}).json()
        self.base = f"/v1/wf/process-instances/{self.instance['id']}/discussions"

    def test_start_url_and_links(self):
        listed = self.client.get(self.base).json()
        self.assertEqual(listed['provider'], 'Mattermost')
        self.assertEqual(listed['start_url'],
                         'https://chat.example.pl/uczelnia/channels/sprawy?s=ZR%2F12%202026')
        self.assertEqual(listed['items'], [])

        added = self.client.post(self.base, json={
            'url': 'https://chat.example.pl/uczelnia/pl/abc123', 'title': 'Konsultacja z IOD'})
        self.assertEqual(added.status_code, 201, added.text)
        items = self.client.get(self.base).json()['items']
        self.assertEqual([(i['title'], i['url']) for i in items],
                         [('Konsultacja z IOD', 'https://chat.example.pl/uczelnia/pl/abc123')])

    def test_rejects_foreign_or_insecure_links(self):
        for url in ('http://chat.example.pl/x', 'https://evil.example.com/x', 'javascript:alert(1)'):
            self.assertEqual(self.client.post(self.base, json={'url': url}).status_code, 422, url)

    def test_without_start_url_any_https_host(self):
        self.chat = {}
        listed = self.client.get(self.base).json()
        self.assertEqual((listed['provider'], listed['start_url']), ('Mattermost', None))
        self.assertEqual(self.client.post(self.base, json={'url': 'https://teams.example/x'}).status_code, 201)

    def test_outsider_has_no_access(self):
        self.as_other()
        self.assertEqual(self.client.get(self.base).status_code, 403)


if __name__ == '__main__':
    unittest.main()
