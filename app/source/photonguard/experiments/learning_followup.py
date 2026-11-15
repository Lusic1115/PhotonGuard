"""Turn a checked prediction into a new draft with the original solver gate."""

import copy
import math
import re

from ..core import digest, finite
from ..workflow import WorkflowError
from .goals import assess
from .learning_units import DISTANCE, length
from .optics import validate_parameters
from .planner import build_plan, unit_for
from .slit_formula import FIRST_MINIMUM, FORMULA_ID, FULL_WIDTH, WIDTH_LABELS, width_mm

_VARIABLES = {
    'wavelength_nm': ('波长', 'nm', 1e6, r'波长|λ'),
    'focal_length_mm': ('焦距', 'mm', 1, r'焦距|焦长|\bf\b'),
    'slit_width_um': ('缝宽', 'μm', 1000, r'缝宽|缝的宽度'),
    'slit_height_um': ('缝高', 'μm', 1000, r'缝高|缝的高度'),
}
_FIELD = re.compile('|'.join(rf'(?P<{key}>{item[3]})' for key, item in _VARIABLES.items()), re.I)
_EDIT = r'更改|改变|调整|修改|改|换|变|设|取|用|试|调'
_DIRECTIONAL_EDIT = r'增加|减少|加|减|缩|升|降|提'
_ACTION = rf'(?:{_EDIT}|{_DIRECTIONAL_EDIT})'
_ALSO = r'(?:(?:也|再|还(?:要)?)\s*)?'
_EDIT_PREFIX = rf'(?:{_EDIT})\s*(?:一下|一点)?\s*(?:把|将)?$'
_KEEP = r'保持|沿用|维持|保留|固定|仍为|还是|照旧|不变|原样'
_NEGATION = r'不要|先别|先不|暂不|不打算|没打算|别|不必|不用|无需|不允许|不能|不得|不'
_VALUE = re.compile(rf'{_ALSO}(?:(?:{_EDIT}|{_KEEP})?\s*(?:为|成|到|至|[:：=])?|'
                    rf'(?:{_DIRECTIONAL_EDIT})\s*(?:到|至))\s*{DISTANCE}', re.I)
_REPLACEMENT = re.compile(rf'(?:由|从)\s*{DISTANCE}\s*{_ACTION}?\s*(?:为|成|到|至)\s*{DISTANCE}', re.I)
_REQUEST = r'如果|假如|会怎样|会如何|什么变化'
_NEGATED_PREFIX = rf'(?:{_NEGATION})\s*(?:去|把|将|{_ACTION}|\s)*$'
_HALF = r'减半|减到一半|变成一半|缩小一半|缩至一半'
_RELATIVE = r'(?:比(?:这(?:一)?版|当前(?:版|结果)?|上一版|原来)(?:的结果)?|亮区|主瓣)\s*(?:再)?\s*([宽窄])\s*(\d+(?:\.\d+)?)\s*[%％]'
_COMPARISON_FIELDS = {
    'gaussian': {'screen_distance_mm': ('屏幕位置', 'mm')},
    'diffraction': {'slit_width_um': ('缝宽', 'μm'), 'slit_separation_um': ('双缝中心距', 'μm'),
                    'relative_phase_rad': ('相对相位', 'rad')},
}


def _entry(key, parameters, state, quote, value=None):
    label, unit, _, _ = _VARIABLES[key]
    return {'field': key, 'label': label, 'unit': unit, 'before': parameters[key], 'after': value,
            'state': state, 'quote': quote.strip()}


def _request_change(question, parameters):
    rows = []
    relative_target = False
    for clause in re.split(r'[，,；;。！？!?\n]+', question):
        matches = list(_FIELD.finditer(clause))
        for index, field in enumerate(matches):
            key = field.lastgroup
            left = matches[index - 1].end() if index else 0
            right = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
            prefix, tail = clause[left:field.start()].strip(), clause[field.end():right].strip()
            quote = clause[left:right]
            replacement = _REPLACEMENT.match(tail)
            value = replacement or _VALUE.match(tail)
            number = length(*value.groups()[-2:], _VARIABLES[key][2]) if value else None
            if key == 'slit_width_um' and re.match(_HALF, tail):
                number = parameters[key] / 2
            if (not tail.startswith('不变') and re.match(rf'{_ALSO}(?:{_NEGATION})\s*{_ACTION}', tail)
                    or re.search(_NEGATED_PREFIX, prefix)):
                rows.append(_entry(key, parameters, 'NEGATED', quote))
            elif (re.match(_KEEP, tail) or re.search(rf'(?:{_KEEP})\s*(?:把|将)?$', prefix)
                  or index and re.fullmatch(r'[和与及、]', prefix) and rows and rows[-1]['state'] == 'KEEP'):
                state = 'REVIEW' if number is not None and number != parameters[key] else 'KEEP'
                rows.append(_entry(key, parameters, state, quote, number))
            elif number is not None and (re.match(rf'{_ALSO}(?:{_ACTION}|由|从|[:：=]|{_HALF})', tail)
                                         or re.search(_EDIT_PREFIX, prefix)
                                         or re.search(_REQUEST, clause)):
                alternatives = value and re.match(r'\s*(?:或|或者|、|和)\s*\d', tail[value.end():])
                wrong_source = replacement and not math.isclose(length(*replacement.groups()[:2], _VARIABLES[key][2]),
                                                                 parameters[key], rel_tol=1e-12)
                state = 'REVIEW' if alternatives or wrong_source else 'KEEP' if number == parameters[key] else 'CHANGE'
                rows.append(_entry(key, parameters, state, quote, number))
            elif (re.search(rf'{_ACTION}\s*(?:一下|一点)?\s*(?:把|将)?$', prefix)
                  or re.match(rf'{_ALSO}{_ACTION}', tail)):
                rows.append(_entry(key, parameters, 'REVIEW', quote))
        shorthand = clause[:matches[0].start()] if matches else clause
        if (not re.search(rf'(?:{_NEGATION})\s*(?:把|将|再|{_EDIT})*\s*\d', shorthand)
                and re.search(rf'换|改|用|试|{_REQUEST}', shorthand)):
            waves = {length(*pair, 1e6) for pair in re.findall(DISTANCE, shorthand, re.I)
                     if pair[1].casefold() in ('nm', '纳米')}
            if waves:
                key = 'wavelength_nm'
                value = next(iter(waves)) if len(waves) == 1 else None
                state = 'REVIEW' if value is None else 'KEEP' if value == parameters[key] else 'CHANGE'
                rows.append(_entry(key, parameters, state, shorthand, value))
        relative = re.search(_RELATIVE, clause)
        if relative and not re.search(_NEGATION, clause):
            fraction = float(relative[2]) / 100 * (1 if relative[1] == '宽' else -1)
            relative_target = True
            value = round(parameters['slit_width_um'] / (1 + fraction), 9) if -1 < fraction <= 10 else None
            rows.append(_entry('slit_width_um', parameters, 'CHANGE' if value else 'REVIEW', clause, value))
        unsupported = re.search(rf'(?:(?:{_EDIT})(?:为|成|到)?\s*(?:一下|把|将)?\s*(网格|孔径窗口|缝距|相位|目标尺寸)|'
                                rf'(网格|孔径窗口|缝距|相位|目标尺寸)\s*(?:{_EDIT}))', clause)
        if unsupported:
            rows.append({'field': 'unsupported', 'label': unsupported[1] or unsupported[2], 'unit': '',
                         'before': None, 'after': None, 'state': 'REVIEW', 'quote': clause.strip()})

    consolidated = {}
    for row in rows:
        key = row['field']
        previous = consolidated.get(key)
        if previous and (previous['state'], previous['after']) != (row['state'], row['after']):
            row = {**row, 'after': None, 'state': 'REVIEW', 'quote': previous['quote'] + ' / ' + row['quote']}
        consolidated[key] = row
    changes = list(consolidated.values())
    if relative_target and sum(row['state'] == 'CHANGE' for row in changes) > 1:
        consolidated['slit_width_um']['state'] = 'REVIEW'
    return changes, relative_target


def _width_allowed(policy, value):
    if 'range_um' in policy:
        return policy['range_um']['min'] <= value <= policy['range_um']['max']
    return any(math.isclose(x, value, rel_tol=1e-12, abs_tol=1e-12) for x in policy.get('catalog_um', []))


def _selected_slit(context):
    if not context or context['domain'] != 'diffraction' or context['source']['kind'] != 'FORWARD_RESULT':
        return None
    row = next((item for item in context['candidates'] if item['id'] == context['selected_candidate_id']), None)
    if not row or not row['checks_passed'] or row['parameters'].get('aperture') != 'single_slit':
        return None
    return row


def predict(question, context):
    row = _selected_slit(context)
    if not row or re.search(r'无透镜|没有透镜|不用透镜|FWHM|半高全宽|近场', question, re.I):
        return None
    changes, relative = _request_change(question, row['parameters'])
    if any(item['state'] == 'CHANGE' for item in changes):
        return _predict_rows(context, row, changes, relative)
    proposed = _verification_edits(question, row['parameters'])
    if not proposed:
        return _predict_rows(context, row, changes, relative)
    prediction = _predict_rows(context, row, proposed)
    prediction['verification'] = {
        'question': question, 'basis': 'PROGRAM_CONTROLLED_COMPARISON',
        'strategy': 'HOLD_WAVELENGTH_FOCAL_PRODUCT' if len(proposed) == 2 else 'CHANGE_WAVELENGTH_ONLY',
    }
    return prediction


def _verification_edits(question, params):
    """Offer a single controlled comparison for the registered slit scale."""
    short_wave = r'波长.{0,6}(?:变短|缩短|变小|减小|降低)|缩短.{0,4}波长|换.{0,5}短波|短波长'
    if (not re.search(r'验证|怎么试|设计.{0,5}对照', question) or not re.search(short_wave, question)
            or re.search(DISTANCE, question, re.I)
            or re.search(r'不(?:能|许|准|允许|要)改.{0,3}波长|波长.{0,5}(?:固定|不变|不动)|先别.{0,5}(?:算|准备)', question)):
        return []
    wave = round(params['wavelength_nm'] * .9, 9)
    if wave < 200:
        return []
    edits = [_entry('wavelength_nm', params, 'CHANGE', '选择较短波长，保持缝宽与缝高', wave)]
    can_change_focus = re.search(r'允许.{0,20}(?:焦距|透镜)|(?:焦距|透镜)也(?:能|可)(?:改|调)', question)
    fixed_focus = re.search(r'(?:焦距|透镜)[^，。；]{0,5}(?:固定|保持|不变|不动)|'
                            r'(?:保持|固定|不改|不允许调整)(?:原)?(?:焦距|透镜)|只(?:改|调)波长', question)
    if can_change_focus and not fixed_focus:
        focal = params['focal_length_mm'] * params['wavelength_nm'] / wave
        edits.append(_entry('focal_length_mm', params, 'CHANGE', '调焦距，使波长与焦距的乘积保持', focal))
    return edits


def _predict_rows(context, row, changes, relative=False):
    parameters = row['parameters']
    edits = [row for row in changes if row['state'] == 'CHANGE']
    unresolved = [row['label'] for row in changes if row['state'] == 'REVIEW']
    if not edits and not unresolved:
        return None
    metric = context['goal']['metric'] if context['goal']['metric'] in WIDTH_LABELS else FULL_WIDTH
    changed_values = {row['field']: row['after'] for row in edits}
    new_parameters = {**parameters, **changed_values}
    before = width_mm(parameters['wavelength_nm'], parameters['focal_length_mm'], parameters['slit_width_um'], metric)
    before_vertical = width_mm(parameters['wavelength_nm'], parameters['focal_length_mm'], parameters['slit_height_um'], FULL_WIDTH)
    after = after_vertical = None
    issue = '请明确这轮的完整改动：' + '、'.join(unresolved) + '。' if unresolved else None
    if not issue and 'slit_width_um' in changed_values and not _width_allowed(context.get('change_policy', {}), changed_values['slit_width_um']):
        issue = '新缝宽超出来源实验允许的范围或目录。可另建实验并明确新的范围。'
    if not unresolved:
        try:
            validate_parameters('diffraction', new_parameters)
        except ValueError as exc:
            issue = '此变化超出来源计划的采样或模型适用条件。' + str(exc)
        else:
            after = width_mm(new_parameters['wavelength_nm'], new_parameters['focal_length_mm'], new_parameters['slit_width_um'], metric)
            after_vertical = width_mm(new_parameters['wavelength_nm'], new_parameters['focal_length_mm'], new_parameters['slit_height_um'], FULL_WIDTH)
    direction = ('UNRESOLVED' if after is None else 'UNCHANGED' if math.isclose(after, before, rel_tol=1e-12)
                 else 'DECREASE' if after < before else 'INCREASE')
    source = {k: context['source'][k] for k in ('record_id', 'result_sha256')}
    result = {'kind': 'ANALYTIC_PREDICTION', 'source': source, 'candidate_id': row['id'],
              'change': (edits or changes)[0], 'changes': changes,
              'fixed': {name: parameters[name] for name in _VARIABLES if name not in changed_values},
              'metric': metric, 'metric_label': WIDTH_LABELS[metric], 'before_mm': before, 'after_mm': after,
              'scale': after / before if after is not None else None, 'direction': direction,
              'vertical': {'before_mm': before_vertical, 'after_mm': after_vertical},
              'formula_id': FORMULA_ID, 'can_prepare': not issue, 'issue': issue,
              'updates_target': relative, 'fft_calls': 0}
    # The approved geometry is canonical across equivalent phrasings. The
    # actual confirming wording is retained on the resulting draft.
    signature = {**result, 'changes': [{k: v for k, v in item.items() if k != 'quote'} for item in changes]}
    signature.pop('change')
    result['sha256'] = digest(signature)
    return result


def _typed_prediction(changes, context):
    row = _selected_slit(context)
    if not row:
        return _comparison_preview(changes, context)
    if not isinstance(changes, dict) or not changes or set(changes) - _VARIABLES.keys():
        raise WorkflowError('请选择本轮要调整的单缝参数。')
    if any(type(value) not in (int, float) or not finite(value) or value <= 0 for value in changes.values()):
        raise WorkflowError('交互参数须为有限正数。')
    params = row['parameters']
    edits = [_entry(key, params, 'CHANGE', f'解析控件选择{_VARIABLES[key][0]} {value:g} {_VARIABLES[key][1]}', value)
             for key, value in sorted(changes.items()) if value != params[key]]
    return _predict_rows(context, row, edits)


def _comparison_preview(changes, context):
    domain = context['domain']
    fields = _COMPARISON_FIELDS.get(domain, {})
    if context['source']['kind'] != 'FORWARD_RESULT' or not fields:
        raise WorkflowError('此结果没有对应的继续实验控件。', 409)
    row = next((item for item in context['candidates'] if item['id'] == context['selected_candidate_id']), None)
    if not row or not row['checks_passed'] or domain == 'diffraction' and row['parameters'].get('aperture') != 'double_slit':
        raise WorkflowError('请选用通过数值核对的来源候选。', 409)
    if not isinstance(changes, dict) or len(changes) != 1 or set(changes) - fields.keys():
        raise WorkflowError('这项对照一次调整一个已列出的参数。')
    field, value = next(iter(changes.items()))
    if type(value) not in (int, float) or not finite(value):
        raise WorkflowError('调整值须为有限数。')
    params = row['parameters']
    if value == params[field]:
        return None
    choice = context['allowed_variations'].get(field)
    issue = None
    if choice:
        bounds = choice['bounds']
        if (bounds and not bounds['min'] <= value <= bounds['max']
                or not bounds and value not in choice['values']):
            issue = '新值超出已保存的候选目录或范围。'
    if not issue:
        try:
            validate_parameters(domain, {**params, field: value})
        except ValueError as exc:
            issue = '请核对本轮模型和数值条件。' + str(exc)
    label, unit = fields[field]
    edit = {'field': field, 'before': params[field], 'after': value, 'unit': unit, 'label': label,
            'state': 'CHANGE', 'quote': f'对照控件选择{label} {value:g} {unit}'}
    source = {key: context['source'][key] for key in ('record_id', 'result_sha256')}
    result = {'kind': 'SAVED_RESULT_COMPARISON', 'source': source, 'candidate_id': row['id'],
              'changes': [edit], 'change': edit, 'can_prepare': not issue, 'issue': issue,
              'solver_calls': 0, 'fft_calls': 0, 'updates_target': False}
    result['sha256'] = digest(result)
    return result


_SOURCE_FIELDS = {'record_id', 'result_sha256', 'candidate_id'}


def _checked_source(store, data):
    from .tutor import read_result

    if not data.get('result_sha256'):
        raise WorkflowError('需要来源结果的版本指纹。', 409)
    return read_result(store, data.get('record_id'), data['result_sha256'], data.get('candidate_id'))


def preview_controls(store, data):
    if set(data) - (_SOURCE_FIELDS | {'changes'}):
        raise WorkflowError('交互预览只接收来源版本和调整后的参数。')
    prediction = _typed_prediction(data.get('changes'), _checked_source(store, data))
    return {'status': 'PREVIEW' if prediction else 'UNCHANGED', 'prediction': prediction, 'solver_calls': 0}


def preview_compensation(store, data):
    if set(data) - (_SOURCE_FIELDS | {'wavelength_nm', 'adjust'}):
        raise WorkflowError('请核对保宽对照的条件。')
    wave, adjust = data.get('wavelength_nm'), data.get('adjust')
    if type(wave) not in (int, float) or not finite(wave) or wave <= 0 or adjust not in ('slit_width_um', 'focal_length_mm'):
        raise WorkflowError('请填写有效的新波长，并选择一个可调整量。')
    context = _checked_source(store, data)
    row = _selected_slit(context)
    if not row or not data.get('candidate_id'):
        raise WorkflowError('请选择一条已通过数值检查的单缝候选。', 409)
    p = row['parameters']
    if wave == p['wavelength_nm']:
        return {'status': 'UNCHANGED', 'prediction': None, 'solver_calls': 0}
    # Equal W requires equal λf/a. Restrict the resulting change through the
    # same candidate and sampling checks as any other manual follow-up.
    scale = wave / p['wavelength_nm'] if adjust == 'slit_width_um' else p['wavelength_nm'] / wave
    changes = {'wavelength_nm': wave, adjust: p[adjust] * scale}
    if adjust == 'slit_width_um':
        required = changes[adjust]
        changes[adjust] = next((value for value in context['change_policy'].get('catalog_um', [])
                                if math.isclose(value, required, rel_tol=1e-12, abs_tol=1e-12)), required)
    prediction = _typed_prediction(changes, context)
    return {'status': 'PREVIEW', 'changes': changes, 'prediction': prediction, 'solver_calls': 0}


def slit_change_causes(prior, current, metric):
    before, after = _selected_slit(prior), _selected_slit(current)
    if not before or not after or metric not in WIDTH_LABELS:
        return None
    p, q = before['parameters'], after['parameters']
    initial = width_mm(p['wavelength_nm'], p['focal_length_mm'], p['slit_width_um'], metric)
    combined = width_mm(q['wavelength_nm'], q['focal_length_mm'], q['slit_width_um'], metric)
    effects = []
    for field in ('wavelength_nm', 'focal_length_mm', 'slit_width_um'):
        if p[field] == q[field]:
            continue
        alternative = {**p, field: q[field]}
        value = width_mm(alternative['wavelength_nm'], alternative['focal_length_mm'], alternative['slit_width_um'], metric)
        label, unit, *_ = _VARIABLES[field]
        effects.append({'field': field, 'label': label, 'unit': unit, 'before': p[field], 'after': q[field],
                        'isolated_mm': value, 'ratio': value / initial,
                        'direction': 'INCREASE' if value > initial else 'DECREASE'})
    if not effects:
        return None
    unchanged = math.isclose(initial, combined, rel_tol=1e-12)
    direction = 'UNCHANGED' if unchanged else 'INCREASE' if combined > initial else 'DECREASE'
    opposing = len({row['direction'] for row in effects}) > 1
    dominant = next((row['field'] for row in effects if row['direction'] == direction), None) if opposing and len(effects) == 2 else None
    original_goal = prior['goal']
    target = None
    if original_goal['metric'] == metric:
        # Apply the same target rule as an actual run, including its tolerance.
        selected = {'metrics': {FIRST_MINIMUM: combined / 2 if metric == FULL_WIDTH else combined}}
        target = {**original_goal, 'met': assess(selected, original_goal)['met']}
    return {'kind': 'ANALYTIC_CHANGE_CAUSES', 'formula_id': FORMULA_ID, 'metric': metric,
            'label': WIDTH_LABELS[metric], 'initial_mm': initial, 'combined_mm': combined,
            'ratio': combined / initial, 'direction': direction, 'opposing': opposing, 'dominant_field': dominant, 'effects': effects,
            'original_target': target, 'solver_calls': 0}


def comparison_summary(store, data):
    from .case_workflow import comparison_needs_new_round
    from .tutor import read_result

    if set(data) != {'record_id', 'result_sha256'} or not data.get('result_sha256'):
        raise WorkflowError('请指定当前已保存结果的版本。')
    case = store.get(data['record_id'])
    link = case.get('slit_followup') or case.get('learning_comparison')
    if not link or case['status'] != 'COMPLETED':
        raise WorkflowError('这版还没有可核对的对照结果。', 409)
    source = link['source']
    if source['record_id'] != case['parent_id'] or source['record_id'] != case['plan']['spec']['parent_task_id']:
        raise WorkflowError('来源版本与已批准的对照计划不一致。', 409)
    prior = read_result(store, source['record_id'], source['result_sha256'], link['candidate_id'])
    current = read_result(store, case['id'], data['result_sha256'])
    if prior['learning_task_id'] != current['learning_task_id'] or prior['domain'] != current['domain']:
        raise WorkflowError('两版来源与当前任务不一致。', 409)
    before, after = (next(row for row in context['candidates'] if row['id'] == context['selected_candidate_id'])
                     for context in (prior, current))
    if not before['checks_passed'] or not after['checks_passed']:
        raise WorkflowError('两版候选都需要通过数值检查。', 409)
    p, q = before['parameters'], after['parameters']
    retained = {key: value for key, value in p.items() if key in q and value == q[key]}
    changed = {key: [value, q[key]] for key, value in p.items() if key in q and value != q[key]}
    measures = []

    def metric(label, unit, first, second, basis='保存的数值结果'):
        if finite(first) and finite(second):
            measures.append({'label': label, 'unit': unit, 'before': first, 'after': second, 'basis': basis})

    x, y = before['metrics'], after['metrics']
    if p.get('aperture') == 'single_slit':
        metric('横向中央全宽', 'mm', x.get(FULL_WIDTH), y.get(FULL_WIDTH), '保存条件的解析几何')
        vertical = [width_mm(a['wavelength_nm'], a['focal_length_mm'], a['slit_height_um'], FULL_WIDTH) for a in (p, q)]
        metric('纵向中央全宽', 'mm', *vertical, '保存条件的解析几何')
    elif p.get('aperture') == 'double_slit':
        metric('相邻条纹间距', 'mm', x.get('fringe_period_mm'), y.get('fringe_period_mm'))
        envelope = [width_mm(a['wavelength_nm'], a['focal_length_mm'], a['slit_width_um'], FULL_WIDTH) for a in (p, q)]
        metric('包络中央全宽', 'mm', *envelope, '单缝包络的解析几何')
        metric('中心强度比', '', x.get('central_intensity_ratio'), y.get('central_intensity_ratio'))
    if prior['domain'] == 'diffraction':
        metric('共同入射标尺下的峰值', '', x.get('peak_intensity'), y.get('peak_intensity'), '保存的 FFT 场')
    else:
        metric('屏幕半径', 'μm', x.get('screen_radius_um'), y.get('screen_radius_um'))
        metric('新束腰位置', 'mm', x.get('new_waist_z_mm'), y.get('new_waist_z_mm'))
    summary = {'prior': {**prior['source'], 'candidate_id': before['id']},
               'current': {**current['source'], 'candidate_id': after['id']},
               'retained': retained, 'changed': changed, 'goals': [before['goal'], after['goal']], 'measures': measures}
    metric_name = prior['goal']['metric']
    causes = slit_change_causes(prior, current, metric_name if metric_name in WIDTH_LABELS else FIRST_MINIMUM)
    if causes:
        summary['causes'] = causes
        origin = store.get(prior['source']['record_id'])
        can_compare = not origin.get('bounded_goal')
        causes['new_round_required'] = comparison_needs_new_round(store, origin)
        for effect in causes['effects']:
            if can_compare:
                preview = _typed_prediction({effect['field']: effect['after']}, prior)
                effect['can_prepare'] = preview['can_prepare']
                effect['issue'] = preview['issue']
    return summary


def prepare(store, data):
    from ..workflow_model import wire
    from .case_workflow import prepare_comparison_budget, start_exploration

    allowed = _SOURCE_FIELDS | {'question', 'changes', 'prediction_sha256', 'confirm_change', 'new_round'}
    new_round = data.get('new_round', False)
    if set(data) - allowed or data.get('confirm_change') is not True or type(new_round) is not bool:
        raise WorkflowError('请明确选择准备这一个对照方案。', 409)
    controls = 'changes' in data
    if controls and 'question' in data:
        raise WorkflowError('请选择同一份来源的对照预览。')
    question = data.get('question', '')
    if not controls and (not isinstance(question, str) or not 1 <= len(question.strip()) <= 1600 or wire.credential_risk(question)):
        raise WorkflowError('对照问题无效。')
    with store.lock:
        context = _checked_source(store, data)
        if store.get(context['source']['record_id']).get('bounded_goal'):
            raise WorkflowError('请从受限方案入口核对目录和可用范围。', 409)
        proposal = _typed_prediction(data['changes'], context) if controls else predict(question, context)
        if not proposal or not proposal['can_prepare'] or proposal['sha256'] != data.get('prediction_sha256'):
            raise WorkflowError('对照预览已经变化，或新条件超出允许范围，请重新核对。', 409)
        parent = store.get(context['source']['record_id'])
        slit = proposal['kind'] == 'ANALYTIC_PREDICTION'
        evidence_key = 'slit_followup' if slit else 'learning_comparison'
        # Reuse a draft if the browser lost the first response. No repeat
        # solve is enabled by reopening a saved or already completed version.
        for path in store.root.glob('*/case.json'):
            child = store.get(path.parent.name)
            if child.get(evidence_key, {}).get('sha256') == proposal['sha256']:
                return {'status': 'CREATED', 'case': store.public_record(child), 'reused_draft': True}
        selected = next(row for row in parent['plan']['candidates'] if row['id'] == proposal['candidate_id'])
        edits = [row for row in proposal['changes'] if row['state'] == 'CHANGE']
        if controls:
            question = '。'.join(edit['quote'] for edit in edits)
        original = copy.deepcopy(parent['spec'])
        spec = copy.deepcopy(original)
        spec['known_parameters'].update(selected['parameters'])
        spec['allowed_variables'] = {name: [selected['parameters'][name]] for name in original['allowed_variables']}
        if context['domain'] == 'diffraction':
            spec['parameter_sources'].setdefault('grid_size', {'quote': '来源结果的 FFT 网格', 'unit': 'dimensionless'})
            spec['parameter_sources'].setdefault('window_mm', {'quote': '来源结果的孔径窗口', 'unit': 'mm'})
        else:
            spec['parameter_sources'].setdefault('z_max_mm', {'quote': '来源结果的纵向显示范围', 'unit': 'mm'})
        spec['parent_task_id'] = parent['id']
        if proposal['updates_target']:
            spec['goal'] = {'metric': proposal['metric'], 'operator': 'near', 'target': proposal['after_mm'],
                            'tolerance': spec['goal'].get('tolerance', 0), 'unit': 'mm'}
        spec['resource_budget'].update(max_meep_validations=0, max_fdtd=0)
        # Only edits listed in the verified preview can amend the comparison reference.
        reference = copy.deepcopy(original)
        for edit in edits:
            field, value = edit['field'], edit['after']
            spec['known_parameters'][field] = reference['known_parameters'][field] = value
            if field in spec['allowed_variables']:
                spec['allowed_variables'][field] = reference['allowed_variables'][field] = [value]
            spec['parameter_sources'][field] = {'kind': 'USER_CONFIRMED_CHANGE', 'quote': edit['quote'], 'unit': unit_for(field)}
        parent_context = {'id': parent['id'], 'spec': reference, 'plan_sha256': parent['plan']['sha256'],
                          'source_spec_sha256': digest(original), 'explicit_changes': edits}
        plan = build_plan(spec, parent_context)
        prepared = {'spec': plan['spec'], 'plan': plan, 'parent_context': parent_context}
        prepare_comparison_budget(store, parent, prepared, new_round=new_round)
        child = store.create({'kind': 'specified', 'text': question})
        title = '单缝' if slit else '高斯光束' if context['domain'] == 'gaussian' else '双缝'
        child.update(prepared, parent_id=parent['id'], family_id=parent['family_id'], kind=parent['kind'],
                     title=title + ' · 对照验证', domain=context['domain'], status='PLANNED', **{evidence_key: proposal})
        if new_round:
            start_exploration(child)
        store.save(child)
        store.event(child, 'TUTOR_COMPARISON_DRAFT', {'prediction': proposal, 'plan_sha256': child['plan']['sha256'], 'fft_calls': 0})
        return {'status': 'CREATED', 'case': store.public_record(child)}
