# tests/test_form_permissions.py
"""Testy jednostkowe wf/services/form_permissions.py (bez bazy/HTTP)."""
from __future__ import annotations

import unittest

from wf.services.form_permissions import filter_form_for_step, has_x_permissions


class HasXPermissionsTest(unittest.TestCase):

    def test_true_for_direct_field(self):
        self.assertTrue(has_x_permissions({
            'type': 'object',
            'properties': {'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}}},
        }))

    def test_true_for_nested_group(self):
        self.assertTrue(has_x_permissions({
            'type': 'object',
            'properties': {
                'grupa': {'type': 'object', 'properties': {
                    'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}}}},
            },
        }))

    def test_true_for_array_items(self):
        self.assertTrue(has_x_permissions({
            'type': 'array',
            'items': {'type': 'object', 'properties': {
                'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}}}},
        }))

    def test_false_without_any_permissions(self):
        self.assertFalse(has_x_permissions({
            'type': 'object', 'properties': {'a': {'type': 'string'}},
        }))


class FilterFormForStepTest(unittest.TestCase):

    def test_none_permission_drops_field(self):
        schema = {
            'type': 'object',
            'properties': {
                'a': {'type': 'string', 'x-permissions': {'S1': 'editable', 'S2': 'none'}},
                'b': {'type': 'string', 'x-permissions': {'S1': 'readonly', 'S2': 'editable'}},
            },
        }
        filtered, _ui = filter_form_for_step(schema, {}, 'S2')
        self.assertEqual(set(filtered['properties']), {'b'})

    def test_readonly_permission_sets_read_only_flag(self):
        schema = {'type': 'object', 'properties': {
            'a': {'type': 'string', 'x-permissions': {'S1': 'readonly'}}}}
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertTrue(filtered['properties']['a']['readOnly'])

    def test_x_permissions_and_x_required_in_steps_are_stripped(self):
        schema = {'type': 'object', 'properties': {
            'a': {'type': 'string', 'x-permissions': {'S1': 'editable'},
                  'x-required-in-steps': ['S1']}}}
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        prop = filtered['properties']['a']
        self.assertNotIn('x-permissions', prop)
        self.assertNotIn('x-required-in-steps', prop)

    def test_required_is_union_of_global_and_step_required(self):
        schema = {
            'type': 'object',
            'required': ['a'],
            'properties': {
                'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}},
                'b': {'type': 'string', 'x-permissions': {'S1': 'editable'},
                      'x-required-in-steps': ['S1']},
                'c': {'type': 'string', 'x-permissions': {'S1': 'readonly'},
                      'x-required-in-steps': ['S2']},  # inny krok - nie liczy się tu
            },
        }
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertEqual(filtered['required'], ['a', 'b'])

    def test_required_omitted_when_empty(self):
        schema = {'type': 'object', 'required': ['a'], 'properties': {
            'a': {'type': 'string', 'x-permissions': {'S1': 'none'}}}}
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertNotIn('required', filtered)

    def test_required_field_dropped_by_permission_is_excluded_from_required(self):
        schema = {
            'type': 'object',
            'required': ['a', 'b'],
            'properties': {
                'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}},
                'b': {'type': 'string', 'x-permissions': {'S1': 'none'}},
            },
        }
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertEqual(filtered['required'], ['a'])

    def test_group_hidden_for_step_drops_all_children(self):
        schema = {
            'type': 'object',
            'properties': {
                'grupa': {
                    'type': 'object',
                    'x-permissions': {'S1': 'none'},
                    'properties': {'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}}},
                },
            },
        }
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertEqual(filtered['properties'], {})

    def test_array_items_are_filtered_recursively(self):
        schema = {
            'type': 'object',
            'properties': {
                'lista': {
                    'type': 'array',
                    'x-permissions': {'S1': 'editable'},
                    'items': {
                        'type': 'object',
                        'properties': {
                            'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}},
                            'b': {'type': 'string', 'x-permissions': {'S1': 'none'}},
                        },
                    },
                },
            },
        }
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertEqual(set(filtered['properties']['lista']['items']['properties']), {'a'})

    def test_field_without_x_permissions_is_kept_unchanged(self):
        schema = {'type': 'object', 'properties': {'a': {'type': 'string'}}}
        filtered, _ui = filter_form_for_step(schema, {}, 'S1')
        self.assertEqual(filtered['properties']['a'], {'type': 'string'})

    def test_ui_schema_is_filtered_in_parallel(self):
        schema = {
            'type': 'object',
            'properties': {
                'a': {'type': 'string', 'x-permissions': {'S1': 'editable'}},
                'b': {'type': 'string', 'x-permissions': {'S1': 'none'}},
            },
        }
        ui_schema = {
            'ui:order': ['a', 'b'],
            'a': {'ui:widget': 'textarea'},
            'b': {'ui:help': 'nieważne, bo pole zniknie'},
        }
        _schema, ui = filter_form_for_step(schema, ui_schema, 'S1')
        self.assertEqual(ui['ui:order'], ['a'])
        self.assertEqual(ui['a']['ui:widget'], 'textarea')
        self.assertNotIn('b', ui)

    def test_missing_step_in_permissions_defaults_to_none(self):
        schema = {'type': 'object', 'properties': {
            'a': {'type': 'string', 'x-permissions': {'OtherStep': 'editable'}}}}
        filtered, _ui = filter_form_for_step(schema, {}, 'ThisStep')
        self.assertEqual(filtered['properties'], {})


if __name__ == '__main__':
    unittest.main()
