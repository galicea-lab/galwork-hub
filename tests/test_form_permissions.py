# tests/test_form_permissions.py
"""Testy jednostkowe wf/services/form_permissions.py (bez bazy/HTTP)."""
from __future__ import annotations

import json
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


# BEGIN 2026-11-12
def _conditional_schema() -> dict:
    """Formularz z wariantem wybieranym polem `rodzaj` (allOf + if/then)."""
    return {
        'type': 'object',
        'properties': {
            'rodzaj': {'type': 'string', 'x-permissions': {'S1': 'editable', 'S2': 'readonly'}},
        },
        'allOf': [{
            'if': {'properties': {'rodzaj': {'const': 'A'}}, 'required': ['rodzaj']},
            'then': {'properties': {
                'pismo_a': {'type': 'string', 'x-permissions': {'S1': 'editable', 'S2': 'none'},
                            'x-required-in-steps': ['S1']},
                'decyzja': {'type': 'string', 'x-permissions': {'S1': 'none', 'S2': 'editable'}},
            }},
        }],
    }


class ConditionalSectionsTest(unittest.TestCase):

    def test_has_x_permissions_only_in_branch(self):
        self.assertTrue(has_x_permissions({'type': 'object', 'allOf': [
            {'if': {}, 'then': {'properties': {'a': {'x-permissions': {'S1': 'editable'}}}}}]}))

    def test_branch_fields_are_filtered_per_step(self):
        filtered, _ui = filter_form_for_step(_conditional_schema(), {}, 'S1')
        then = filtered['allOf'][0]['then']
        self.assertEqual(set(then['properties']), {'pismo_a'})
        self.assertEqual(then['required'], ['pismo_a'])
        self.assertNotIn('x-permissions', then['properties']['pismo_a'])

        filtered, _ui = filter_form_for_step(_conditional_schema(), {}, 'S2')
        then = filtered['allOf'][0]['then']
        self.assertEqual(set(then['properties']), {'decyzja'})
        self.assertNotIn('required', then)
        self.assertTrue(filtered['properties']['rodzaj']['readOnly'])

    def test_condition_is_kept_unchanged(self):
        filtered, _ui = filter_form_for_step(_conditional_schema(), {}, 'S1')
        self.assertEqual(filtered['allOf'][0]['if'],
                         {'properties': {'rodzaj': {'const': 'A'}}, 'required': ['rodzaj']})

    def test_branch_ui_and_wildcard_order_are_kept(self):
        ui_schema = {
            'ui:order': ['rodzaj', '*', 'decyzja'],
            'pismo_a': {'ui:widget': 'textarea'},
            'decyzja': {'ui:widget': 'textarea'},
        }
        _schema, ui = filter_form_for_step(_conditional_schema(), ui_schema, 'S1')
        self.assertEqual(ui['ui:order'], ['rodzaj', '*'])
        self.assertEqual(ui['pismo_a'], {'ui:widget': 'textarea'})
        self.assertNotIn('decyzja', ui)

    def test_same_section_in_many_branches_merges_ui(self):
        def branch(code, fields):
            return {'if': {'properties': {'rodzaj': {'const': code}}},
                    'then': {'properties': {'pismo': {'type': 'object', 'properties': {
                        f: {'type': 'string', 'x-permissions': {'S1': 'editable'}}
                        for f in fields}}}}}
        schema = {'type': 'object', 'properties': {}, 'allOf': [
            branch('A', ['x', 'y']), branch('B', ['z'])]}
        ui_schema = {'pismo': {'ui:order': ['x', 'y', 'z'],
                               'x': {'ui:widget': 'textarea'}, 'z': {'ui:widget': 'richtext'}}}
        _schema, ui = filter_form_for_step(schema, ui_schema, 'S1')
        self.assertEqual(ui['pismo']['x'], {'ui:widget': 'textarea'})
        self.assertEqual(ui['pismo']['z'], {'ui:widget': 'richtext'})
        # różne zestawy pól w gałęziach - wspólny ui:order wywołałby błąd RJSF
        self.assertNotIn('ui:order', ui['pismo'])

    def test_filtered_schema_still_validates_by_variant(self):
        import jsonschema
        filtered, _ui = filter_form_for_step(_conditional_schema(), {}, 'S1')
        jsonschema.validate({'rodzaj': 'B'}, filtered)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({'rodzaj': 'A'}, filtered)
# -END


# BEGIN 2026-11-13
def _profiles_schema() -> dict:
    """Formularz z profilami uprawnień (`x-permissions` = nazwa profilu)."""
    return {
        'type': 'object',
        'x-permission-profiles': {
            'edycja': {'S1': 'editable', 'S2': 'readonly'},
            'tylko_s2': {'S2': 'editable'},
        },
        'properties': {
            'a': {'type': 'string', 'x-permissions': 'edycja', 'x-required-in-steps': ['S1']},
            'b': {'type': 'string', 'x-permissions': 'tylko_s2'},
            'c': {'type': 'string', 'x-permissions': 'brak_takiego'},
            'd': {'type': 'string', 'x-permissions': {'S1': 'editable'}},
        },
        'allOf': [{'if': {'properties': {'a': {'const': 'X'}}},
                   'then': {'properties': {'e': {'type': 'string', 'x-permissions': 'tylko_s2'}}}}],
    }


class PermissionProfilesTest(unittest.TestCase):

    def test_profile_name_counts_as_permissions(self):
        self.assertTrue(has_x_permissions({'type': 'object', 'properties': {
            'a': {'type': 'string', 'x-permissions': 'p01'}}}))

    def test_profile_is_resolved_per_step(self):
        filtered, _ui = filter_form_for_step(_profiles_schema(), {}, 'S1')
        self.assertEqual(set(filtered['properties']), {'a', 'd'})
        self.assertEqual(filtered['required'], ['a'])
        self.assertNotIn('readOnly', filtered['properties']['a'])

        filtered, _ui = filter_form_for_step(_profiles_schema(), {}, 'S2')
        self.assertEqual(set(filtered['properties']), {'a', 'b'})
        self.assertTrue(filtered['properties']['a']['readOnly'])

    def test_unknown_profile_hides_field(self):
        for step in ('S1', 'S2'):
            filtered, _ui = filter_form_for_step(_profiles_schema(), {}, step)
            self.assertNotIn('c', filtered['properties'])

    def test_profiles_in_conditional_branches(self):
        filtered, _ui = filter_form_for_step(_profiles_schema(), {}, 'S1')
        self.assertEqual(filtered['allOf'][0]['then']['properties'], {})
        filtered, _ui = filter_form_for_step(_profiles_schema(), {}, 'S2')
        self.assertEqual(set(filtered['allOf'][0]['then']['properties']), {'e'})

    def test_profiles_are_stripped_from_result(self):
        filtered, _ui = filter_form_for_step(_profiles_schema(), {}, 'S2')
        self.assertNotIn('x-permission-profiles', filtered)
        self.assertNotIn('x-permissions', filtered['properties']['b'])
# -END



# BEGIN 2026-11-13
def _defs_schema() -> dict:
    """Wspólne sekcje w `$defs`, wskazywane z gałęzi przez `$ref`."""
    return {
        'type': 'object',
        'x-permission-profiles': {'s1': {'S1': 'editable'}, 's2': {'S2': 'editable'},
                                  'oba': {'S1': 'editable', 'S2': 'readonly'}},
        'properties': {'rodzaj': {'type': 'string', 'x-permissions': 'oba'}},
        '$defs': {
            'pismo-a': {'type': 'object', 'x-permissions': 'oba', 'properties': {
                'tresc': {'type': 'string', 'x-permissions': 's1', 'x-required-in-steps': ['S1']},
                'decyzja': {'type': 'string', 'x-permissions': 's2'}}},
            'tylko-s2': {'type': 'object', 'x-permissions': 's2', 'properties': {
                'x': {'type': 'string', 'x-permissions': 's2'}},
                'x-required-in-steps': ['S2']},
            'bez-macierzy': {'type': 'string', 'enum': ['a', 'b']},
        },
        'allOf': [
            {'if': {'properties': {'rodzaj': {'const': 'A'}}, 'required': ['rodzaj']},
             'then': {'properties': {'PISMO': {'$ref': '#/$defs/pismo-a'},
                                     'OPINIA': {'$ref': '#/$defs/tylko-s2'}}}},
            {'if': {'properties': {'rodzaj': {'const': 'B'}}, 'required': ['rodzaj']},
             'then': {'properties': {'PISMO': {'$ref': '#/$defs/pismo-a'}}}},
        ],
    }


class SharedDefinitionsTest(unittest.TestCase):

    def test_has_x_permissions_only_in_defs(self):
        self.assertTrue(has_x_permissions({'type': 'object', '$defs': {
            'a': {'type': 'string', 'x-permissions': 'p01'}}}))

    def test_ref_is_kept_and_definition_filtered(self):
        filtered, _ui = filter_form_for_step(_defs_schema(), {}, 'S1')
        then_a = filtered['allOf'][0]['then']
        self.assertEqual(then_a['properties'], {'PISMO': {'$ref': '#/$defs/pismo-a'}})
        self.assertEqual(set(filtered['$defs']['pismo-a']['properties']), {'tresc'})
        self.assertEqual(filtered['$defs']['pismo-a']['required'], ['tresc'])
        self.assertNotIn('x-permissions', json.dumps(filtered))

    def test_definition_hidden_in_step_drops_field_and_definition(self):
        filtered, _ui = filter_form_for_step(_defs_schema(), {}, 'S1')
        self.assertNotIn('OPINIA', filtered['allOf'][0]['then']['properties'])
        self.assertNotIn('tylko-s2', filtered['$defs'])
        self.assertIn('bez-macierzy', filtered['$defs'])

    def test_required_in_steps_of_definition_counts_for_field(self):
        filtered, _ui = filter_form_for_step(_defs_schema(), {}, 'S2')
        then_a = filtered['allOf'][0]['then']
        self.assertEqual(then_a['required'], ['OPINIA'])
        self.assertTrue(filtered['$defs']['pismo-a']['readOnly'])

    def test_field_ui_is_filtered_with_definition(self):
        ui_schema = {'PISMO': {'ui:order': ['tresc', 'decyzja'], 'tresc': {'ui:widget': 'textarea'},
                               'decyzja': {'ui:widget': 'radio'}}}
        _schema, ui = filter_form_for_step(_defs_schema(), ui_schema, 'S1')
        self.assertEqual(ui['PISMO'], {'ui:order': ['tresc'], 'tresc': {'ui:widget': 'textarea'}})

    def test_filtered_schema_validates_through_refs(self):
        import jsonschema
        filtered, _ui = filter_form_for_step(_defs_schema(), {}, 'S1')
        jsonschema.validate({'rodzaj': 'A', 'PISMO': {'tresc': 't'}}, filtered)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({'rodzaj': 'A', 'PISMO': {}}, filtered)
# -END


if __name__ == '__main__':
    unittest.main()
