"""Constrained previews and approved follow-ups on a selected saved candidate."""

from __future__ import annotations

import copy
import math

from ..core import digest, finite
from ..workflow import WorkflowError
from ..workflow_model import wire
from .case_workflow import comparison_needs_new_round, prepare_comparison_budget, start_exploration
from .optics import evaluate, gaussian_estimate, validate_parameters
from .planner import build_plan, parameters_for, unit_for
from .slit_formula import FIRST_MINIMUM, FULL_WIDTH, WIDTH_LABELS, numerator, width_mm
from .tutor import read_result


SOURCE = {'record_id', 'result_sha256', 'candidate_id'}
TEXT_EVIDENCE = {'input_text', 'input_quotes'}
MAX_OPTIONS = 8
MAX_PLANNED = 5
LABELS = {
    'wavelength_nm': '波长', 'slit_width_um': '缝宽', 'focal_length_mm': '焦距',
    'screen_distance_mm': '屏幕位置', 'layer_count': '层数', 'layers': '单层膜厚',
}


def source_for(store, data):
    if any(not isinstance(data.get(key), str) or not data[key] for key in SOURCE):
        raise WorkflowError('请先选择已保存结果中的一个候选。', 409)
    context = read_result(store, data['record_id'], data['result_sha256'], data['candidate_id'])
    candidate = next((row for row in context.get('candidates', []) if row['id'] == data['candidate_id']), None)
    if context['source']['kind'] != 'FORWARD_RESULT' or not candidate or not candidate['checks_passed']:
        raise WorkflowError('这项规划需要一条通过数值核对的正向实验候选。', 409)
    record = store.get(data['record_id'])
    row = next(row for row in record['plan']['candidates'] if row['id'] == candidate['id'])
    baseline = {**record['spec']['known_parameters'], **row['changes']}
    if record['domain'] != 'film':
        baseline.update(row['parameters'])
    return record, context, baseline


def _value(value, name, lower, upper):
    if not finite(value) or not lower <= value <= upper:
        raise WorkflowError(f'{name}须在 {lower:g}–{upper:g} 之间。')
    return value


def _scope(raw, current, name, lower, upper, *, source=None, continuous=True):
    if not isinstance(raw, dict):
        raise WorkflowError(f'请明确{name}的可用范围或目录。')
    kind = raw.get('kind')
    if kind == 'source' and set(raw) == {'kind'} and source:
        return source
    if kind == 'fixed' and set(raw) == {'kind'}:
        return {'kind': 'catalog', 'values': [current]}
    if kind == 'catalog' and set(raw) == {'kind', 'values'}:
        values = raw['values']
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_OPTIONS:
            raise WorkflowError(f'{name}目录应有 1–{MAX_OPTIONS} 项。')
        return {'kind': 'catalog', 'values': sorted({_value(value, name, lower, upper) for value in values})}
    if kind == 'range' and continuous and set(raw) == {'kind', 'min', 'max'}:
        a, b = (_value(raw[key], name, lower, upper) for key in ('min', 'max'))
        if a <= b:
            return {'kind': 'range', 'min': a, 'max': b}
    raise WorkflowError(f'请检查{name}的目录或上下界。')


def _contains(scope, value):
    return scope['min'] <= value <= scope['max'] if scope['kind'] == 'range' else value in scope['values']


def _intersect(a, b):
    if a['kind'] == b['kind'] == 'range':
        return {'kind': 'range', 'min': max(a['min'], b['min']), 'max': min(a['max'], b['max'])}
    fixed, other = (a, b) if a['kind'] == 'catalog' else (b, a)
    return {'kind': 'catalog', 'values': [value for value in fixed['values'] if _contains(other, value)]}


def _points(scope, *anchors):
    if scope['kind'] == 'catalog':
        return scope['values']
    lo, hi = scope['min'], scope['max']
    return sorted({lo, hi, *(max(lo, min(hi, value)) for value in anchors)}) if lo <= hi else []


def _pref(request, allowed):
    preference = request.get('preference', 'balanced')
    if preference not in ('balanced', *allowed):
        raise WorkflowError('请选择界面列出的排序偏好。')
    return preference


def _slit_width_policy(store, record, context):
    # A one-candidate comparison narrows its calculation, not the inherited
    # stock of available slit plates. Follow its checked sources back to the
    # last explicit width policy. A bounded plan may narrow that policy.
    seen = {record['id']}
    while (link := record.get('slit_followup')) and not record.get('bounded_goal'):
        source = link['source']
        parent_id = source['record_id']
        if parent_id != record['parent_id'] or parent_id in seen:
            raise WorkflowError('无法核对来源缝片目录。', 409)
        seen.add(parent_id)
        origin = read_result(store, parent_id, source['result_sha256'], link['candidate_id'])
        if origin['learning_task_id'] != context['learning_task_id'] or origin['domain'] != 'diffraction':
            raise WorkflowError('缝片目录必须来自同一实验任务。', 409)
        record, context = store.get(parent_id), origin
    return context['change_policy']


def _slit_reference(store, current, request):
    binding = request['target_reference']
    if not isinstance(binding, dict) or set(binding) != SOURCE or 'target' in request:
        raise WorkflowError('请单独选定参照版本和候选，或填写明确目标值。', 409)
    record, context, params = source_for(store, binding)
    if context['learning_task_id'] != current['family_id'] or params.get('aperture') != 'single_slit':
        raise WorkflowError('恢复目标须引用同一实验任务的单缝结果。', 409)
    if record['id'] == current['id']:
        raise WorkflowError('请选用另一版参照结果，或直接填写保持本版的目标值。', 409)
    return context


def _slit(record, context, baseline, request):
    allowed = {'wavelength_nm', 'width_scope', 'focal_scope', 'target', 'tolerance', 'preference', 'metric', 'target_reference'}
    if baseline['aperture'] != 'single_slit' or set(request) - allowed:
        raise WorkflowError('保宽规划使用单缝焦平面模型。')
    metric = request.get('metric', FULL_WIDTH)
    if not isinstance(metric, str) or metric not in WIDTH_LABELS:
        raise WorkflowError('请选择单侧暗纹距离或中央亮区全宽。')
    reference = context.get('restoration_reference')
    wave = _value(request.get('wavelength_nm'), '新波长（nm）', 200, 2000)
    w0, f0 = baseline['slit_width_um'], baseline['focal_length_mm']
    original = width_mm(baseline['wavelength_nm'], f0, w0, metric)
    if reference:
        ref_row = next(row for row in reference['candidates'] if row['id'] == reference['selected_candidate_id'])
        original = ref_row['metrics'][metric]
    target = _value(request.get('target', original), WIDTH_LABELS[metric] + '（mm）', 0.000001, 40000)
    tolerance = _value(request.get('tolerance'), '允许误差（mm）', 0, 40000)
    preference = _pref(request, ('keep_focus', 'keep_width'))
    policy = context['change_policy']
    prior = {'kind': 'range', **policy['range_um']} if 'range_um' in policy else {'kind': 'catalog', 'values': policy['catalog_um']}
    desired = _scope(request.get('width_scope'), w0, '缝宽（μm）', 1, 5000, source=prior)
    focal = _scope(request.get('focal_scope'), f0, '焦距（mm）', 1, 10000)
    window_um = baseline['window_mm'] * 1000
    physical = {'kind': 'range', 'min': max(1, 8 * window_um / baseline['grid_size'], wave * 0.01),
                'max': min(5000, 0.8 * window_um)}
    width = _intersect(_intersect(prior, desired), physical)
    limits = {'wavelength_nm': {'kind': 'catalog', 'values': [wave]}, 'slit_width_um': desired, 'focal_length_mm': focal}
    effective = {**limits, 'slit_width_um': width}
    factor = numerator(wave, 1, metric)
    pairs = set()
    # With one coordinate fixed, W is monotonic in the other. In a rectangle,
    # include intersections of W=target with the edges and the two held values.
    for f in _points(focal, f0, *(target * w / factor for w in _points(width, w0))):
        pairs.update((w, f) for w in _points(width, w0, factor * f / target))
    for w in _points(width, w0, *(factor * f / target for f in _points(focal, f0))):
        pairs.update((w, f) for f in _points(focal, f0, target * w / factor))
    if reference and width['kind'] == 'range' and focal['kind'] == 'catalog' and len(focal['values']) == 1 and tolerance > 0:
        f = focal['values'][0]
        nearby = [factor * f / t for t in (target - tolerance / 2, target + tolerance / 2) if t > 0]
        pairs.update((w, f) for w in nearby if _contains(width, w))
    rows = []
    for w, f in sorted(pairs):
        values = {'wavelength_nm': wave, 'slit_width_um': w, 'focal_length_mm': f}
        try:
            validate_parameters('diffraction', {**baseline, **values})
        except ValueError:
            continue
        value = width_mm(wave, f, w, metric)
        rows.append({'values': values, 'value': value,
                     'details': {'vertical_width_mm': width_mm(wave, f, baseline['slit_height_um'], FULL_WIDTH)}})
    goal = {'metric': metric, 'operator': 'near', 'target': target, 'tolerance': tolerance, 'unit': 'mm'}
    held = {'keep_focus': 'focal_length_mm', 'keep_width': 'slit_width_um'}.get(preference)
    return rows, goal, limits, effective, held, '理想单缝解析几何', len(rows)


def _gaussian(record, context, baseline, request):
    if set(request) - {'focal_scope', 'screen_scope', 'metric', 'target', 'tolerance', 'preference'}:
        raise WorkflowError('高斯方案比较字段无效。')
    z0, f0 = baseline['screen_distance_mm'], baseline['focal_length_mm']
    source_focal = {'kind': 'catalog', 'values': record['spec']['allowed_variables'].get('focal_length_mm', [f0])}
    focal = _scope(request.get('focal_scope'), f0, '透镜焦距（mm）', -10000, 10000, source=source_focal, continuous=False)
    screen = _scope(request.get('screen_scope'), z0, '屏幕位置（mm）', 0, baseline['z_max_mm'])
    preference = _pref(request, ('keep_focus', 'keep_screen'))
    goal_kind = request.get('metric')
    if goal_kind == 'min_radius':
        goal = {'metric': 'screen_radius_um', 'operator': 'minimize', 'unit': 'um'}
    elif goal_kind == 'radius_limit':
        goal = {'metric': 'screen_radius_um', 'operator': '<=', 'unit': 'um',
                'target': _value(request.get('target'), '屏幕半径上限（μm）', 0.000001, 10000000)}
    elif goal_kind == 'waist_position':
        goal = {'metric': 'new_waist_z_mm', 'operator': 'near', 'unit': 'mm',
                'target': _value(request.get('target'), '束腰位置（mm）', -100000, 100000),
                'tolerance': _value(request.get('tolerance'), '允许误差（mm）', 0, 100000)}
    else:
        raise WorkflowError('请选择屏幕半径或新束腰位置作为目标。')
    limits = {'focal_length_mm': focal, 'screen_distance_mm': screen}
    # The original lesson's lenses are examples. A user's already approved
    # directory in a derived record remains a constraint on its descendants.
    starter_lesson = (record['kind'] == 'teaching' and not record.get('parent_id')
                      and record.get('learning_lesson_id') == 'gaussian-waist')
    effective = limits if starter_lesson else {**limits, 'focal_length_mm': _intersect(source_focal, focal)}
    rows = []
    for f in _points(effective['focal_length_mm']):
        try:
            waist = gaussian_estimate({**baseline, 'focal_length_mm': f})['new_waist_z_mm']
        except ValueError:
            continue
        for z in _points(screen, z0, waist):
            values = {'focal_length_mm': f, 'screen_distance_mm': z}
            metrics = gaussian_estimate({**baseline, **values})
            rows.append({'values': values, 'value': metrics[goal['metric']], 'details': metrics})
    held = {'keep_focus': 'focal_length_mm', 'keep_screen': 'screen_distance_mm'}.get(preference)
    return rows, goal, limits, effective, held, '理想高斯 q 参数解析估计', len(rows)


def _film(record, context, baseline, request):
    if set(request) - {'variable', 'scope', 'wavelength_nm', 'metric', 'target', 'preference'}:
        raise WorkflowError('薄膜方案比较字段无效。')
    wave = _value(request.get('wavelength_nm'), '评价波长（nm）', 500, 5000) / 1000
    measure = request.get('metric')
    if measure not in ('reflectance', 'transmittance'):
        raise WorkflowError('请选择目标波长处的反射率或透射率。')
    target = _value(request.get('target'), '目标比例', 0, 1)
    variable = request.get('variable')
    _pref(request, ('fewer_layers',))
    options = record['spec']['allowed_variables']
    if variable == 'layer_count' and not baseline.get('layers') and 'layer_count' in baseline:
        baseline_scope = {'kind': 'catalog', 'values': options.get('layer_count', [baseline['layer_count']])}
        scope = _scope(request.get('scope'), baseline['layer_count'], '层数', 1, 8, source=baseline_scope, continuous=False)
        if any(int(count) != count for count in scope['values']):
            raise WorkflowError('薄膜层数须为整数。')
        values_for = lambda n: {'layer_count': int(n)}
        effective = _intersect(baseline_scope, scope)
    elif variable == 'thickness_nm' and len(baseline.get('layers', [])) == 1:
        layer = baseline['layers'][0]
        scope = _scope(request.get('scope'), layer['thickness_um'] * 1000, '单层膜厚（nm）', 20, 5000, continuous=False)
        values_for = lambda nm: {'layers': [{**layer, 'thickness_um': nm / 1000}]}
        effective = scope
    else:
        raise WorkflowError('请选择已有四分之一波膜系的层数，或已保存单层膜的厚度目录。')
    goal = {'metric': measure, 'operator': '>=', 'target': target, 'wavelength_um': wave, 'unit': 'fraction'}
    axis = parameters_for('film', baseline, goal)['wavelengths_um']
    if not min(axis) <= wave <= max(axis):
        raise WorkflowError('评价波长须处于来源记录的扫描范围。')
    rows = []
    for n in _points(effective):
        values = values_for(n)
        parameters = parameters_for('film', {**baseline, **values}, goal)
        validation = evaluate('film', {'layers': parameters['layers'], 'wavelengths_um': [wave]})
        if not validation['all_checks_passed']:
            continue
        value = validation['data']['R' if measure == 'reflectance' else 'T'][0]
        rows.append({'values': values, 'value': value, 'details': {'layer_count': len(parameters['layers']),
                     'thickness_nm': parameters['layers'][0]['thickness_um'] * 1000 if variable == 'thickness_nm' else None}})
    return rows, goal, {variable: scope}, {variable: effective}, None, 'TMM 单点筛选，完整光谱待批准', len(_points(effective))


def _score(row, goal):
    value = row['value']
    operator = goal['operator']
    if operator == 'near':
        error = abs(value - goal['target'])
        met = error <= goal['tolerance']
        if goal['metric'] in WIDTH_LABELS and not met:
            met = math.isclose(error, goal['tolerance'], rel_tol=1e-12, abs_tol=1e-12)
        return met, error
    if operator in ('>=', '<='):
        error = (goal['target'] - value) if operator == '>=' else (value - goal['target'])
        return error <= 0, abs(error)
    return None, value


def preview(store, data):
    required = SOURCE | {'request'}
    if not required <= set(data) <= required | TEXT_EVIDENCE or not isinstance(data['request'], dict):
        raise WorkflowError('请核对受限方案的参数表。')
    text, quotes = data.get('input_text', ''), data.get('input_quotes', {})
    if (not isinstance(text, str) or len(text) > 1600 or wire.credential_risk(text) or not isinstance(quotes, dict) or len(quotes)>6
            or any(key not in ('wavelength_nm','width_scope','focal_scope','preference','target','tolerance') or
                   not isinstance(quote, str) or not quote or quote not in text for key, quote in quotes.items())):
        raise WorkflowError('受限方案原文或引用无效。')
    record, context, baseline = source_for(store, data)
    request = data['request']
    if record['domain'] == 'diffraction' and 'target_reference' in request:
        context['restoration_reference'] = _slit_reference(store, record, request)
    if record['domain'] == 'diffraction':
        context['change_policy'] = _slit_width_policy(store, record, context)
    builder = {'diffraction': _slit, 'gaussian': _gaussian, 'film': _film}[record['domain']]
    try:
        rows, goal, limits, effective, held, method, evaluated = builder(record, context, baseline, data['request'])
    except (ValueError, ImportError) as exc:
        raise WorkflowError('请核对来源模型、可用目录和评价条件。' + str(exc)) from None
    for row in rows:
        row['meets'], row['distance'] = _score(row, goal)
        row['keeps_preference'] = not held or row['values'].get(held) == baseline.get(held)
        row['changed_fields'] = [key for key, value in row['values'].items() if value != baseline.get(key)]
    eligible = [row for row in rows if row['meets'] is True]
    preferred = bool(held and any(row['keeps_preference'] for row in eligible))
    if preferred:
        eligible = [row for row in eligible if row['keeps_preference']]
    if goal['operator'] == 'minimize':
        status, shown = 'RANKED', sorted(rows, key=lambda row: (row['distance'], not row['keeps_preference']))
    elif eligible:
        fewer_layers = data['request'].get('preference') == 'fewer_layers'
        status, shown = 'FOUND', sorted(eligible, key=lambda row: (
            row['details'].get('layer_count', 0) if fewer_layers else 0,
            not row['keeps_preference'], len(row['changed_fields']), row['distance']))
    else:
        status, shown = ('NO_MATCH' if rows else 'NO_LEGAL_CANDIDATE'), sorted(rows, key=lambda row: row['distance'])[:1]
    if not rows:
        status = 'NO_LEGAL_CANDIDATE'
    cap = min(MAX_PLANNED, record['spec']['resource_budget']['max_candidates'])
    for index, row in enumerate(shown[:cap], 1):
        row['id'] = str(index)
    summary = {'domain': record['domain'], 'source': {key: data[key] for key in SOURCE}, 'goal': goal,
               'limits': limits, 'effective_limits': effective, 'held_preference': held, 'preferred_available': preferred,
               'method': method, 'screened_candidates': evaluated, 'rows': shown[:cap],
               'status': status, 'worker_calls': 0, 'fast_cost_per_candidate': 2 if record['domain'] == 'diffraction' else 1}
    if reference := context.get('restoration_reference'):
        summary['target_reference'] = {**reference['source'], 'candidate_id': reference['selected_candidate_id']}
        if set(effective['focal_length_mm']) == {'kind', 'values'} and len(effective['focal_length_mm']['values']) == 1:
            f = effective['focal_length_mm']['values'][0]
            summary['ideal_width_um'] = numerator(request['wavelength_nm'], f, goal['metric']) / goal['target']
    if comparison_needs_new_round(store, record):
        summary['new_round_required'] = True
    if text:
        summary.update(input_text=text, input_quotes=quotes)
    summary['sha256'] = digest({'summary': summary, 'request': data['request'], 'source_spec': record['spec']})
    return summary


def prepare(store, data):
    extra = {'preview_sha256', 'candidate_ids', 'confirm_plan', 'verify_closest', 'new_round'}
    new_round = data.get('new_round', False)
    if (set(data) - (SOURCE | {'request'} | extra | TEXT_EVIDENCE)
            or data.get('confirm_plan') is not True or type(new_round) is not bool):
        raise WorkflowError('请确认要准备验证的候选。', 409)
    with store.lock:
        arguments = {key: value for key, value in data.items() if key not in extra}
        report = preview(store, arguments)
        ids = data.get('candidate_ids')
        if (report['sha256'] != data.get('preview_sha256') or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_PLANNED
                or any(not isinstance(ident, str) for ident in ids) or len(set(ids)) != len(ids)):
            raise WorkflowError('预览或选择已经变化，请重新核对。', 409)
        rows = [row for row in report['rows'] if row['id'] in ids]
        if len(rows) != len(ids) or report['status'] == 'NO_MATCH' and data.get('verify_closest') is not True:
            raise WorkflowError('当前只能明确选择核对最接近的一项。', 409)
        receipt = digest({'preview': report['sha256'], 'ids': sorted(ids)})
        for path in store.root.glob('*/case.json'):
            existing = store.get(path.parent.name)
            if existing.get('bounded_goal', {}).get('receipt') == receipt:
                return {'status': 'CREATED', 'case': store.public_record(existing), 'reused_draft': True}
        parent, _, baseline = source_for(store, data)
        original = parent['spec']
        spec = copy.deepcopy(original)
        chosen = [row['values'] for row in rows]
        keys = sorted(chosen[0])
        variables = {key: list({digest(values[key]): values[key] for values in chosen}.values()) for key in keys
                     if any(values[key] != chosen[0][key] for values in chosen)}
        known = {**baseline, **chosen[0]}
        spec.update(known_parameters=known, allowed_variables=variables, locked_parameters=sorted(set(known)-set(variables)),
                    candidate_rows=[{key: values[key] for key in variables} for values in chosen], candidate_bounds={},
                    goal=report['goal'], parent_task_id=parent['id'])
        spec['resource_budget'].update(max_candidates=min(original['resource_budget']['max_candidates'], MAX_PLANNED),
                                       max_meep_validations=0, max_fdtd=0)
        for key in known:
            if key in keys and any(values[key] != baseline.get(key) for values in chosen):
                spec['parameter_sources'][key] = {'kind': 'USER_CONFIRMED_BOUNDED_CANDIDATES',
                    'quote': '用户核对的受限候选 ' + ' / '.join(str(values[key]) for values in chosen), 'unit': unit_for(key)}
            elif key not in spec['parameter_sources']:
                spec['parameter_sources'][key] = {'kind': 'SAVED_RESULT', 'quote': '所选来源候选的已保存参数', 'unit': unit_for(key)}
        reference = {'id': parent['id'], 'spec': {**spec, 'goal': original['goal']}, 'plan_sha256': parent['plan']['sha256'],
                     'source_spec_sha256': digest(original), 'explicit_changes': sorted(set(keys))}
        try:
            plan = build_plan(spec, reference)
            prepared = {'spec': plan['spec'], 'plan': plan, 'parent_context': reference}
            prepare_comparison_budget(store, parent, prepared, new_round=new_round)
        except ValueError as exc:
            raise WorkflowError(str(exc), 409) from None
        title = {'diffraction': '单缝 · 受限保宽', 'gaussian': '高斯光束 · 受限方案', 'film': '薄膜 · 受限方案'}[parent['domain']]
        if report.get('target_reference'):
            title = '单缝 · 恢复参照目标'
        child = store.create({'kind': 'specified', 'text': title})
        evidence = {**report, 'request': data['request'], 'chosen_ids': [row['id'] for row in rows], 'receipt': receipt}
        child.update(prepared, status='PLANNED', title=title, parent_id=parent['id'], family_id=parent['family_id'],
                     kind=parent['kind'], domain=parent['domain'], bounded_goal=evidence)
        if new_round:
            start_exploration(child)
        store.save(child)
        store.event(child, 'BOUNDED_GOAL_PLAN_CREATED', {'bounded_goal': evidence, 'plan_sha256': child['plan']['sha256'], 'worker_calls': 0})
        return {'status': 'CREATED', 'case': store.public_record(child)}
