# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# tests/test_form_data_permissions.py

# Dane formularza wg `x-permissions` - pola ukryte w kroku
# nie trafiają do przeglądarki, a pola ukryte i tylko do odczytu nie dają
# się nadpisać wysłanym formularzem.
"""Testy jednostkowe prune_data_for_step / protect_submitted_data."""
from __future__ import annotations

import unittest

from wf.services.form_permissions import (filter_form_for_step, protect_submitted_data,
                                          prune_data_for_step)

SCHEMA = {
    'type': 'object',
    'x-permission-profiles': {'dziekanat': {'Wniosek': 'none', 'Decyzja': 'editable'}},
    '$defs': {
        'adres': {'type': 'object', 'x-permissions': {'Wniosek': 'editable', 'Decyzja': 'none'},
                  'properties': {'ulica': {'type': 'string'}}},
    },
    'properties': {
        'imie': {'type': 'string', 'x-permissions': {'Wniosek': 'editable', 'Decyzja': 'readonly'}},
        'pesel': {'type': 'string', 'x-permissions': {'Wniosek': 'editable', 'Decyzja': 'none'}},
        'uwagi': {'type': 'string', 'x-permissions': 'dziekanat'},
        'suma': {'type': 'number', 'readOnly': True},
        'adres': {'$ref': '#/$defs/adres'},
        'student': {
            'type': 'object',
            'properties': {
                'nr_albumu': {'type': 'string', 'x-permissions': {'Wniosek': 'editable',
                                                                  'Decyzja': 'readonly'}},
                'ocena': {'type': 'string', 'x-permissions': {'Wniosek': 'none',
                                                              'Decyzja': 'editable'}},
            },
        },
        'pozycje': {
            'type': 'array',
            'items': {'type': 'object', 'properties': {
                'nazwa': {'type': 'string'},
                'cena': {'type': 'number', 'x-permissions': {'Wniosek': 'editable',
                                                             'Decyzja': 'none'}},
            }},
        },
    },
    'allOf': [{
        'if': {'properties': {'imie': {'const': 'X'}}},
        'then': {'properties': {
            'powod': {'type': 'string', 'x-permissions': {'Wniosek': 'editable',
                                                          'Decyzja': 'none'}}}},
    }],
}

DATA = {
    'imie': 'Anna', 'pesel': '90010112345', 'uwagi': 'tajne', 'suma': 3,
    'adres': {'ulica': 'Polna'}, 'powod': 'zdrowie',
    'student': {'nr_albumu': '123', 'ocena': '5'},
    'pozycje': [{'nazwa': 'a', 'cena': 1}, {'nazwa': 'b', 'cena': 2}],
    'chosen_path': 'Flow_1', 'dokument_id': 7,
}


def _step(name: str) -> dict:
    return filter_form_for_step(SCHEMA, {}, name)[0]


class PruneDataForStepTest(unittest.TestCase):

    def test_hidden_fields_are_removed_for_step(self):
        result = prune_data_for_step(SCHEMA, _step('Decyzja'), DATA)
        self.assertNotIn('pesel', result)
        self.assertNotIn('adres', result)         # ukryta definicja z $defs
        self.assertNotIn('powod', result)         # pole z gałęzi warunkowej
        self.assertEqual(result['student'], {'nr_albumu': '123', 'ocena': '5'})
        self.assertEqual(result['pozycje'], [{'nazwa': 'a'}, {'nazwa': 'b'}])
        self.assertEqual(result['imie'], 'Anna')  # readonly zostaje
        self.assertEqual(result['uwagi'], 'tajne')

    def test_profile_and_nested_none_in_other_step(self):
        result = prune_data_for_step(SCHEMA, _step('Wniosek'), DATA)
        self.assertNotIn('uwagi', result)
        self.assertEqual(result['student'], {'nr_albumu': '123'})
        self.assertEqual(result['pesel'], '90010112345')

    def test_undeclared_keys_pass_through(self):
        result = prune_data_for_step(SCHEMA, _step('Decyzja'), DATA)
        self.assertEqual(result['chosen_path'], 'Flow_1')
        self.assertEqual(result['dokument_id'], 7)

    def test_input_is_not_mutated(self):
        data = {'student': {'nr_albumu': '1', 'ocena': '2'}}
        prune_data_for_step(SCHEMA, _step('Wniosek'), data)
        self.assertEqual(data, {'student': {'nr_albumu': '1', 'ocena': '2'}})


class ProtectSubmittedDataTest(unittest.TestCase):

    def test_top_level_hidden_and_readonly_are_dropped(self):
        submitted = {'imie': 'Zmienione', 'pesel': 'podrobiony', 'suma': 99, 'chosen_path': 'Flow_2'}
        result = protect_submitted_data(SCHEMA, _step('Decyzja'), submitted, DATA)
        self.assertNotIn('imie', result)
        self.assertNotIn('pesel', result)
        # `readOnly` wpisany w schemat na stałe - nie z `x-permissions`
        self.assertEqual(result['suma'], 99)
        self.assertEqual(result['chosen_path'], 'Flow_2')

    def test_nested_section_keeps_hidden_and_readonly_values(self):
        # przeglądarka dostała sekcję bez pola ukrytego - wysyła ją bez niego
        submitted = {'student': {'nr_albumu': 'zmieniony'}}
        current = {'student': {'nr_albumu': '123', 'ocena': '5'}}
        result = protect_submitted_data(SCHEMA, _step('Wniosek'), submitted, current)
        self.assertEqual(result['student'], {'nr_albumu': 'zmieniony', 'ocena': '5'})
        result = protect_submitted_data(SCHEMA, _step('Decyzja'),
                                        {'student': {'nr_albumu': 'X', 'ocena': '4'}}, current)
        self.assertEqual(result['student'], {'nr_albumu': '123', 'ocena': '4'})

    def test_array_items_restore_hidden_values_by_index(self):
        submitted = {'pozycje': [{'nazwa': 'A'}, {'nazwa': 'B', 'cena': 100}, {'nazwa': 'C'}]}
        result = protect_submitted_data(SCHEMA, _step('Decyzja'), submitted, DATA)
        self.assertEqual(result['pozycje'],
                         [{'nazwa': 'A', 'cena': 1}, {'nazwa': 'B', 'cena': 2}, {'nazwa': 'C'}])

    def test_editable_fields_are_kept(self):
        submitted = {'uwagi': 'nowe', 'student': {'ocena': '3'}}
        result = protect_submitted_data(SCHEMA, _step('Decyzja'), submitted, DATA)
        self.assertEqual(result['uwagi'], 'nowe')
        self.assertEqual(result['student'], {'ocena': '3', 'nr_albumu': '123'})


if __name__ == '__main__':
    unittest.main()
