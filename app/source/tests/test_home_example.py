"""Homepage example planning: source-bound, offline, and approval still required.

These checks exercise saved plans, never an optical result or a model response.
"""
import copy
import json
from pathlib import Path
import re

import pytest

from photonguard.core import digest
from photonguard.experiments import service, task_entry
from photonguard.workflow import WorkflowError
from test_experiment_service import store


@pytest.fixture
def home_text():
    asset = Path(service.__file__).parents[1] / 'workflow_assets' / 'experiments.js'
    match = re.search(r'const SINGLE_SLIT_EXAMPLE\s*=\s*("[^\n]+?");', asset.read_text(encoding='utf-8'))
    assert match, 'Read the actual public homepage example, not a duplicate fixture.'
    return json.loads(match.group(1))


@pytest.fixture(autouse=True)
def forbid_model_and_solver(store, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Local example preparation must not call a model or solver.')
    monkeypatch.setattr(store, '_model', forbidden)
    monkeypatch.setattr(service, 'execute_plan', forbidden)


def action(store, state, kind='plan', **kwargs):
    return store.action(state['id'], {
        'type': kind, 'expected_revision': state['revision'],
        'session_version': 2, 'mode': 'rules', **kwargs,
    })


def planned_example(store, text):
    started = task_entry.start(store, {'text': text})
    assert started['status'] == 'CREATED'
    state = started['case']
    assert state['status'] == 'DRAFT' and state['text'] == text
    assert state['plan'] is None and state['result'] is None
    assert started['model_calls'] == state['model_calls'] == 0
    return action(store, state)


def assert_unexecuted(store, state):
    assert state['model_calls'] == 0
    assert state['result'] is None
    assert not state.get('approved_plan_sha256')
    assert not store.running
    assert not (store.folder(state['id']) / 'execution').exists()
    ledger = state.get('exploration_ledger', {})
    assert ledger.get('reserved_fast', 0) == ledger.get('reserved_fdtd', 0) == 0
    assert ledger.get('stages', 0) == 0


def test_homepage_exact_example_prepares_source_bound_d01_without_execution(store, home_text):
    state = planned_example(store, home_text)
    assert state['status'] == 'PLANNED' and state['domain'] == 'diffraction'
    assert state['text'] == home_text and state['kind'] == 'specified'
    assert state['fact_state']['case_id'] == 'D01'
    assert not state['questions'] and not state.get('session_issue')
    expected = {
        'parameters.wavelength_nm': 633., 'parameters.focal_length_mm': 200.,
        'parameters.slit_height_um': 100., 'parameters.slit_width_um': 20.,
        'parameters.window_mm': .4, 'parameters.grid_size': 256,
        'recipe.values': [16., 20., 24.], 'goal.metric': 'first_zero_mm',
        'goal.operator': 'near', 'goal.target': 6.33, 'goal.tolerance': .05,
        'execution.fast_only': True,
    }
    facts = state['fact_state']['facts']
    messages = state['fact_state']['messages']
    for field, value in expected.items():
        fact = facts[field]
        assert fact['status'] == 'resolved' and fact['value'] == value, field
        assert fact['source_kind'] == 'user_text', field
        assert fact['evidence'], field
        for evidence in fact['evidence']:
            source = messages[evidence['message_id']]['text']
            assert source == home_text
            assert evidence['quote'] == source[evidence['start']:evidence['end']]
            assert evidence['quote'] and evidence['text_sha256'] == digest(source)
    assert facts['parameters.aperture']['value'] == 'single_slit'
    candidates = state['plan']['candidates']
    assert [c['parameters']['slit_width_um'] for c in candidates] == [16., 20., 24.]
    assert all(c['parameters']['wavelength_nm'] == 633. for c in candidates)
    assert all(c['parameters']['window_mm'] == .4 and c['parameters']['grid_size'] == 256 for c in candidates)
    assert state['plan']['resource_budget']['max_fdtd'] == 0
    assert state['plan']['resource_budget']['max_meep_validations'] == 0
    assert_unexecuted(store, state)


@pytest.mark.parametrize('old,new,field,value', [
    ('窗口0.4 mm', '窗口0.8 mm', 'window_mm', .8),
    ('网格256', '网格512', 'grid_size', 512),
    ('波长633 nm', '波长532 nm', 'wavelength_nm', 532.),
    ('缝宽16、20、24 μm', '缝宽18、22、26 μm', 'slit_width_um', [18., 22., 26.]),
])
def test_edited_homepage_input_never_silently_uses_original_values(store, home_text, old, new, field, value):
    assert old in home_text
    changed = home_text.replace(old, new)
    state = task_entry.start(store, {'text': changed, 'mode': 'forward'})['case']
    state = action(store, state)
    assert state['text'] == changed
    assert_unexecuted(store, state)
    if state.get('plan'):
        actual = [candidate['parameters'][field] for candidate in state['plan']['candidates']]
        assert actual == value if isinstance(value, list) else all(v == value for v in actual)
    else:
        assert state.get('questions') or state.get('session_issue')


@pytest.mark.parametrize('old,new', [
    ('透镜焦距200 mm，', ''),
    ('允许偏差0.05 mm。', ''),
    ('单缝衍射', '双缝衍射'),
    ('波长633 nm', '波长尚未知'),
    ('波长633 nm', '不要采用波长633 nm'),
])
def test_incomplete_or_different_homepage_text_does_not_gain_template_facts(store, home_text, old, new):
    assert old in home_text
    changed = home_text.replace(old, new)
    state = task_entry.start(store, {'text': changed, 'mode': 'forward'})['case']
    state = action(store, state)
    assert state['text'] == changed and state['plan'] is None
    assert state.get('questions') or state.get('session_issue')
    assert_unexecuted(store, state)


def test_saved_missing_case_draft_recovers_in_place_without_new_execution(store, home_text):
    state = store.create({'kind': 'specified', 'text': home_text})
    state['session_issue'] = {'kind': 'MISSING_CASE', 'text': '请从案例库选择本次实验类型。'}
    store.save(state)
    original_id = state['id']
    state = action(store, state)
    assert state['id'] == original_id and state['text'] == home_text
    assert state['status'] == 'PLANNED' and state['fact_state']['case_id'] == 'D01'
    assert not state.get('session_issue')
    assert_unexecuted(store, state)


def test_repeated_local_preparation_preserves_plan_and_requires_approval(store, home_text):
    state = planned_example(store, home_text)
    original_plan = copy.deepcopy(state['plan'])
    original_id = state['id']
    state = action(store, state)
    assert state['id'] == original_id and state['plan'] == original_plan
    assert state['status'] == 'PLANNED' and not state['questions']
    assert_unexecuted(store, state)
    with pytest.raises(WorkflowError):
        action(store, state, 'run', approve=False, plan_sha256=state['plan']['sha256'])
    assert_unexecuted(store, store.get(state['id']))


def test_followup_correction_survives_another_local_plan(store, home_text):
    state = planned_example(store, home_text)
    changed = action(store, state, 'fact_update', text='更正波长为532 nm', event_id='user-wavelength-correction')
    assert '更正波长为532 nm' in changed['text'] and home_text in changed['text']
    assert changed['fact_state']['facts']['parameters.wavelength_nm']['value'] == 532.
    replanned = action(store, changed)
    assert replanned['fact_state']['facts']['parameters.wavelength_nm']['value'] == 532.
    if replanned.get('plan'):
        assert all(c['parameters']['wavelength_nm'] == 532. for c in replanned['plan']['candidates'])
    assert_unexecuted(store, replanned)


def test_saved_approved_version_cannot_be_rewritten_by_builtin_example(store, home_text):
    state = planned_example(store, home_text)
    # A saved-state fixture, not a solver run or a new approval event.
    state['approved_plan_sha256'] = state['plan']['sha256']
    store.save(state)
    before = copy.deepcopy(store.get(state['id']))
    with pytest.raises(WorkflowError):
        action(store, state)
    assert store.get(state['id']) == before
