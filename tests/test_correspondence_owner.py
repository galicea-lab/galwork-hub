# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# właściciel sprawy pobierany bez treści sprawy.

import unittest
from unittest.mock import MagicMock, patch

from connector.frappe_education import correspondence

MODULE = 'connector.frappe_education.correspondence'


class TestCorrespondenceOwner(unittest.TestCase):
  @patch(f'{MODULE}.get_connector')
  def test_only_owner_fields_are_requested(self, get_connector):
    connector = MagicMock()
    connector.get_value.return_value = {'email': 'jan@example.com', 'student': 'STU-0001'}
    get_connector.return_value = connector
    owner = correspondence.db_get_correspondence_owner('EDU-COR-2026-00001')
    self.assertEqual(owner, {'email': 'jan@example.com', 'student': 'STU-0001'})
    connector.get_value.assert_called_once_with(
      correspondence.CORRESPONDENCE_DOCTYPE, {'name': 'EDU-COR-2026-00001'}, ['email', 'student'])
    connector.get_doc.assert_not_called()

  @patch(f'{MODULE}.get_connector')
  def test_missing_case_is_none(self, get_connector):
    for missing in (None, {}):
      get_connector.return_value = MagicMock(**{'get_value.return_value': missing})
      self.assertIsNone(correspondence.db_get_correspondence_owner('EDU-COR-2026-99999'))


if __name__ == '__main__':
  unittest.main()
