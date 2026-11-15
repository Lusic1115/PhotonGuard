"""Read-only optical tutoring on checked sources and saved experiment summaries."""

import json
import re
import uuid
from functools import lru_cache
from importlib.resources import files

from ..core import digest, finite
from ..product_phase import json_payload
from ..workflow import WorkflowError, atomic_json, parse_json
from ..workflow_model import wire
from . import general_tutoring, knowledge_retrieval, learning_session
from .slit_formula import FIRST_MINIMUM, FORMULA_ID, FULL_WIDTH, WIDTH_LABELS, width_mm

_INTENTS = {'EXPLAIN_CONCEPT', 'EXPLAIN_RESULT', 'PREDICT_CHANGE', 'PROPOSE_EXPERIMENT', 'STUDY_CHECK'}
_METRIC_LABELS = WIDTH_LABELS | {
    'fringe_period_mm': '双缝条纹周期', 'central_intensity_ratio': '中心相干强度比',
    'screen_radius_um': '屏幕 1/e² 强度半径', 'new_waist_radius_um': '新束腰半径',
    'new_waist_z_mm': '新束腰位置', 'reflectance': '反射率', 'transmittance': '透射率',
}
_PARAMETERS = {
    'diffraction': ('aperture', 'wavelength_nm', 'focal_length_mm', 'slit_width_um', 'slit_height_um',
                    'slit_separation_um', 'relative_phase_rad', 'grid_size', 'window_mm'),
    'gaussian': ('wavelength_nm', 'focal_length_mm', 'waist_radius_um', 'waist_z_mm', 'screen_distance_mm', 'z_max_mm'),
    'film': ('material_n_high', 'material_n_low', 'layer_count', 'design_wavelength_um'),
}
_PUBLIC_METRICS = {
    'diffraction': ('first_zero_mm', 'fringe_period_mm', 'peak_intensity', 'central_intensity_ratio',
                    'analytic_relative_l2', 'resolution_relative_l2'),
    'gaussian': ('screen_radius_um', 'new_waist_radius_um', 'new_waist_z_mm', 'input_rayleigh_range_mm'),
    'film': ('layer_count', 'total_thickness_um'),
}
_DIFFRACTION_VIEWS = {
    'physical': ('I / I_incident', '纵轴为光强 / 入射光强，候选使用同一线性标尺。'),
    'peak': ('I / I_max', '纵轴按各曲线自身峰值归一化，便于比较形状。'),
    'log': ('10 log10(I / I_incident)', '纵轴为相对入射光强的分贝值，便于查看弱侧瓣。'),
}
_HORIZONTAL_CLAIM = re.compile(
    r'(?:横向(?:中央)?(?:亮区|全宽|宽度)|中央(?:亮区|全宽)|主瓣|第一暗纹)'
    r'[^，。！？；\n]{0,12}?(?P<direction>变宽|变大|增大|扩大|离中心更远|远离中心|向外移|'
    r'变窄|变小|减小|缩小|向中心靠近|向内移|更?靠近中心|保持不变|不变)')
_HORIZONTAL_DIRECTIONS = {
    'DECREASE': {'变窄', '变小', '减小', '缩小', '向中心靠近', '向内移', '更靠近中心', '靠近中心'},
    'INCREASE': {'变宽', '变大', '增大', '扩大', '离中心更远', '远离中心', '向外移'},
    'UNCHANGED': {'保持不变', '不变'},
}
_WRONG_RADIUS_REFERENCE = re.compile(
    r'(?:以|相对(?:于)?|参照|取自|用)[^。！？；，\n]{0,6}(?:入射|束腰处|其他截面|上一截面)[^。！？；，\n]{0,6}(?:光强|强度)')
_SLIT_FOCUS_REFERENCE = re.compile(
    r'(?:焦距.{0,14}(?:等于|就是)|屏幕.{0,4}(?:离|距|到))(?:单缝|狭缝|缝片|缝).{0,8}(?:距离|更远|更近|变长|变短)')
_DEFINITION_CONSTANTS = {
    'gaussian-waist': ('gaussian_radius_threshold', '1/e²', re.compile(r'(?<![\d.])1\s*/\s*e(?:²|\^2)(?![\d.])')),
    'gaussian-beam-quality': ('ideal_tem00_quality', 'M² 为 1',
                              re.compile(r'(?<![a-z0-9])M²\s*(?:为|等于|是|[=＝])\s*1(?![\d.])', re.I)),
}


@lru_cache(maxsize=1)
def _knowledge():
    data = json.loads(files(__package__).joinpath('knowledge_cards.json').read_text(encoding='utf-8'))
    return [{**card, 'sources': [data['sources'][key] for key in card['source_refs']],
             'review_status': data['review_status'], 'license_info': data['license_info']} for card in data['cards']]


def search_cards(question='', domain=None):
    cards, _ = knowledge_retrieval.retrieve(_knowledge(), question, domain,
                                           kind=knowledge_retrieval.question_kind(question))
    return cards


def knowledge_library():
    return {'mode': 'LOCAL_REFERENCE', 'cards': _knowledge()}


def _study_card(entry):
    """Expose a published article through the tutor's existing card view."""
    first_sentence, period, _ = entry['body'].partition('。')
    return {'id':entry['id'],'title':entry['title'],'definition':entry['definition'],
            'terms':entry['terms'],'intuitive_text':first_sentence + period,
            'detailed_text':entry['body'],'verification':entry['example'],
            'common_mistakes':[entry['caution']], 'assumptions':entry['conditions'],
            'formula':entry.get('formula'),'variables':entry.get('variables'),
            'sources':entry['sources'],'catalog_article':True}


def read_only_client(store, data):
    if data.get('approve_data') is not True:
        raise WorkflowError('请先同意发送本轮问题和必要的已核对摘要。', 403)
    if getattr(store.owner, 'simulation_context', None):
        raise WorkflowError('模拟会话不能发起收费请求。', 403)
    client = store.client
    if not client:
        raise WorkflowError('请先连接 AI，或使用本地知识卡。', 503)
    client.preflight()
    return client


def strict_object(response):
    assistant = response.get('assistant') or {}
    if assistant.get('tool_calls') or not isinstance(assistant.get('content'), str):
        raise ValueError('JSON text required')
    value = parse_json(assistant['content'])
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def _numeric_fields(data, keys):
    return {key: data[key] for key in keys if finite(data.get(key)) and not isinstance(data[key], bool)}


def _source(record, result, kind):
    return {'record_id': record['id'], 'revision': record['revision'], 'result_sha256': digest(result), 'kind': kind}


def _project_forward(record, candidate_id):
    result = record['result']
    domain = record['domain']
    candidates = result.get('candidates', [])
    if candidate_id and not any(row['id'] == candidate_id for row in candidates):
        raise WorkflowError('所选候选不在这个结果版本中。', 409)
    candidate_id = candidate_id or result.get('winner_id') or next((row['id'] for row in candidates), None)
    kept = [row for row in candidates if row['id'] == candidate_id]
    kept += [row for row in candidates if row['id'] != candidate_id][:4]
    rows = []
    axes = {}
    for row in kept:
        p = row.get('parameters', {})
        metrics = _numeric_fields(row.get('metrics', {}), _PUBLIC_METRICS[domain])
        params = _numeric_fields(p, _PARAMETERS[domain])
        if domain == 'diffraction':
            params['aperture'] = p.get('aperture')
            if p.get('aperture') == 'single_slit' and FIRST_MINIMUM in metrics:
                metrics[FULL_WIDTH] = 2 * metrics[FIRST_MINIMUM]
        if domain == 'film':
            params['layer_count'] = len(p.get('layers', []))
        goal = row.get('goal') or {}
        source = goal.get('source') or {}
        engine = row.get('engine') or {}
        rows.append({'id': row['id'], 'parameters': params, 'metrics': metrics, 'status': row.get('status'),
                     'checks_passed': row.get('all_checks_passed') is True,
                     'goal': {key: goal[key] for key in ('value', 'unit', 'met', 'metric') if key in goal},
                     'goal_source': source.get('kind'), 'engine': engine.get('name') if isinstance(engine, dict) else str(engine)})
        chart = row.get('data') or {}
        if not axes and chart.get('x_label'):
            axes = {'x': chart['x_label'], 'y': chart['y_label']}
            if domain == 'diffraction':
                axes.update(normalization='I / I_incident', common_scale=True,
                            series=[item.get('label') for item in chart.get('series', [])])
            if domain == 'gaussian':
                axes.update(radius_convention='1/e² 强度半径', coordinate_origin='透镜位置 z=0')
    goal = record['spec']['goal']
    spec = record['spec']
    bounds = spec.get('candidate_bounds', {}).get('slit_width_um')
    width_values = spec['allowed_variables'].get('slit_width_um', [spec['known_parameters'].get('slit_width_um')])
    policy = {'range_um': bounds} if bounds else {'catalog_um': [x for x in width_values if finite(x)]}
    width_scope = record.get('bounded_goal', {}).get('effective_limits', {}).get('slit_width_um')
    if width_scope:
        policy = ({'range_um': {key: width_scope[key] for key in ('min', 'max')}} if width_scope['kind'] == 'range'
                  else {'catalog_um': width_scope['values']})
    return {'source': _source(record, result, 'FORWARD_RESULT'), 'domain': domain,
            'context_kind': record['spec']['context_kind'], 'status': result['status'],
            'goal': {key: goal[key] for key in ('metric', 'operator', 'target', 'tolerance', 'unit', 'wavelength_um') if key in goal},
            'selected_candidate_id': candidate_id, 'winner_id': result.get('winner_id'), 'candidate_count': len(candidates),
            'candidates': rows, 'axes': axes, 'change_policy': policy if domain == 'diffraction' else {},
            'allowed_variations': {key: {'bounds': spec.get('candidate_bounds', {}).get(key), 'values': vals}
                                   for key, vals in spec['allowed_variables'].items()}}


def _project_inverse(store, record):
    result = record['inverse_result']
    domain = record['domain']
    groups = record.get('groups', [])
    fixed = record.get('inverse_settings', {}).get('fixed', {})
    plan = record.get('measurement_plan') or {}
    if plan.get('fit_result_id') != result['id']:
        plan = {}
    facts = {'datasets': len(groups), 'points': sum(group['dataset']['rows'] for group in groups),
             'data_kind': _observation_kind(groups),
             'fixed': _numeric_fields(fixed, ('film_index', 'substrate_index', 'incident_index', 'wavelength_nm',
                                               'focal_length_mm', 'relative_phase_rad')),
             'estimate': _numeric_fields(result.get('estimated_parameters') or {},
                                         ('thickness_nm', 'waist_radius_um', 'waist_z_mm', 'slit_width_um', 'slit_separation_um'))}
    facts['residual_evidence'] = [{**_numeric_fields(row, ('training_rmse','holdout_rmse')),
                                  'holdout_points': sum(flag is True for flag in row.get('holdout', []))}
                                 for row in (result.get('data') or [])[:3] if isinstance(row,dict)]
    facts['csv_interpretations'] = [{'group': index+1, 'original_rows': review['original_rows'], 'mapped_rows': review['mapped_rows'],
                                    'excluded_rows': len(review['excluded_rows']), 'sorted': review['sorted'],
                                    'diameter_to_radius': review['mapping'].get('width_kind') == 'diameter'}
                                   for index,group in enumerate(groups) if (review := group.get('intake'))]
    revision = record.get('data_interpretation')
    if revision:
        facts['csv_revision'] = {'group': revision['group_index']+1, 'source_record_id': revision['record_id']}
    if domain == 'film':
        facts.update(retained_modes=len(result.get('modes', [])),
                     periodicity_nm=result.get('diagnostics', {}).get('period_nm_at_first_wavelength'),
                     observed_wavelengths_nm=_observed_wavelengths(groups),
                     thickness_bounds_nm=record['inverse_settings']['bounds']['thickness_nm'])
        origin = record.get('comparison_origin') or {}
        if origin.get('record_id') and origin.get('fit_result_id'):
            try:
                parent = store.verified_case(origin['record_id'])
            except WorkflowError as exc:
                if exc.status != 404:
                    raise
                parent = {}
            previous = parent.get('inverse_result') or {}
            old_groups = parent.get('groups', [])
            if (previous.get('id') == origin['fit_result_id'] and parent['family_id'] == record['family_id']
                    and parent.get('inverse_settings') == record['inverse_settings']
                    and old_groups == groups[:len(old_groups)] and len(old_groups) < len(groups)):
                added = groups[len(old_groups):]
                facts['acquisition_comparison'] = {'source': _source(parent, previous, 'INVERSE_RESULT'),
                                                   'previous_modes': len(previous.get('modes', [])),
                                                   'added_wavelengths_nm': _observed_wavelengths(added),
                                                   'added_data_kind': _observation_kind(added)}
                impact = result.get('observation_impact')
                if impact and impact['source'] == {'record_id': parent['id'], 'fit_result_id': previous['id']}:
                    facts['acquisition_comparison']['impact'] = {
                        'points': impact['points'][:3], 'new_point_count': impact['new_point_count'],
                        'same_wavelength_only': impact['same_wavelength_only'],
                        'possible_mismatch': impact['possible_mismatch'],
                        'incomplete_search': impact['incomplete_search'],
                    }
        option = next((item for item in plan.get('plans', []) if item['id'] == plan.get('recommended_id')), None)
        if option:
            facts['next_wavelengths_nm'] = option.get('wavelengths_nm', [])[:9]
    return {'source': _source(record, result, 'INVERSE_RESULT'), 'domain': domain,
            'status': result['status'], 'facts': facts, 'warnings': result.get('warnings', [])[:12]}


def _observed_wavelengths(groups):
    return sorted({w for group in groups for w in group['dataset'].get('x_nm', [])})[:12]


def _observation_kind(groups):
    kinds = {group['dataset'].get('source_kind') for group in groups}
    if kinds == {'SYNTHETIC_TEACHING'}:
        return 'TEACHING_SYNTHETIC'
    return 'MIXED_OBSERVATIONS' if 'SYNTHETIC_TEACHING' in kinds else 'USER_SUPPLIED_OBSERVATIONS'


_OBSERVATION_LABELS = {'TEACHING_SYNTHETIC': '教学合成观测', 'MIXED_OBSERVATIONS': '教学与导入观测',
                       'USER_SUPPLIED_OBSERVATIONS': '导入观测'}


def read_result(store, record_id, result_sha256=None, candidate_id=None):
    """Project a verified result, never an active draft or an implicit parent."""
    with store.lock:
        record = store.verified_case(record_id)
        if record.get('result'):
            context = _project_forward(record, candidate_id)
        elif record.get('inverse_result'):
            saved_fit = record['inverse_result']
            seen = {record['id']}
            while (record.get('inverse_plan') or {}).get('sha256') != saved_fit['plan_sha256']:
                source_id = record.get('inverse_reference_parent')
                if not source_id or source_id in seen:
                    raise WorkflowError('找不到该拟合结果对应的已批准条件。', 409)
                seen.add(source_id)
                record = store.verified_case(source_id)
                if record.get('inverse_result') != saved_fit:
                    raise WorkflowError('拟合来源结果不匹配。', 409)
            context = _project_inverse(store, record)
        else:
            raise WorkflowError('这个版本还没有完成的结果。请在历史记录中打开要解释的版本。', 409)
        if result_sha256 is not None and result_sha256 != context['source']['result_sha256']:
            raise WorkflowError('结果版本发生变化，请打开需要解释的保存记录。', 409)
        context['learning_task_id'] = record['family_id']
        return context


def _number(value):
    return format(value, '.6g')


def _grounded_facts(context):
    if not context:
        return []
    if context['source']['kind'] == 'INVERSE_RESULT':
        fact = context['facts']
        label = _OBSERVATION_LABELS[fact['data_kind']]
        lines = [f"此版使用 {fact['datasets']} 组{label}，共 {fact['points']} 个采样点。"]
        if fact.get('csv_revision'):
            lines.append(f"采集 {fact['csv_revision']['group']} 沿用原文件，按本版列解释重新分析。")
        for review in fact.get('csv_interpretations', []):
            line = f"采集 {review['group']} 的原文件有 {review['original_rows']} 条，本版分析使用 {review['mapped_rows']} 条。"
            if review['diameter_to_radius']:
                line += '按已确认口径，将 1/e² 强度直径换成半径。'
            lines.append(line)
        unit = {'film':'比例','diffraction':'相对光强','gaussian':'μm'}[context['domain']]
        for index,row in enumerate(fact.get('residual_evidence', []),1):
            if 'training_rmse' in row:
                line = f"采集 {index} 的拟合点 RMSE 为 {_number(row['training_rmse'])} {unit}。"
                if 'holdout_rmse' in row and row['holdout_points']:
                    line += f"另有 {row['holdout_points']} 个留出点，RMSE 为 {_number(row['holdout_rmse'])} {unit}。"
                lines.append(line)
        if context['domain'] == 'film':
            if fact['observed_wavelengths_nm']:
                lines.append('观测波长（nm）：' + '、'.join(map(_number, fact['observed_wavelengths_nm'])) + '。')
            lower, upper = map(_number, fact['thickness_bounds_nm'])
            change = fact.get('acquisition_comparison')
            if change:
                added = '、'.join(map(_number, change['added_wavelengths_nm']))
                added_label = _OBSERVATION_LABELS[change['added_data_kind']]
                lines.append(f"加入 {added} nm 的{added_label}后，{lower}–{upper} nm 搜索范围内保留的候选由 "
                             f"{change['previous_modes']} 组变为 {fact['retained_modes']} 组。")
                if impact := change.get('impact'):
                    for point in impact['points']:
                        lo, hi = map(_number, point['prior_prediction_range'])
                        lines.append(f"{_number(point['wavelength_nm'])} nm 的导入反射率为 "
                                     f"{_number(point['reflectance'])}，旧候选预测范围 {lo}–{hi}。")
            else:
                lines.append(f"在 {lower}–{upper} nm 搜索范围内保留 {fact['retained_modes']} 组厚度候选。")
            if finite(fact['periodicity_nm']):
                lines.append('首波长对应的模型厚度周期约 ' + _number(fact['periodicity_nm']) + ' nm。')
            if fact.get('next_wavelengths_nm'):
                lines.append('已有补测计划的推荐波长（nm）：' + '、'.join(map(_number, fact['next_wavelengths_nm'])) + '。')
        estimate = fact['estimate']
        for key, label, unit in (('thickness_nm', '本版最佳模型拟合厚度', 'nm'), ('waist_radius_um', '拟合束腰半径', 'μm'),
                                 ('waist_z_mm', '拟合束腰位置', 'mm'), ('slit_width_um', '拟合缝宽', 'μm'),
                                 ('slit_separation_um', '拟合缝距', 'μm')):
            if key in estimate:
                lines.append(f'{label} {_number(estimate[key])} {unit}。')
        return lines
    goal = context['goal']
    metric = _METRIC_LABELS.get(goal['metric'], goal['metric'])
    target = f" {goal['operator']} {_number(goal['target'])}" if 'target' in goal else '（按候选排序）'
    if goal.get('operator') == 'near':
        target = f" {_number(goal['target'])} ± {_number(goal['tolerance'])} {goal.get('unit', '')}"
    lines = [f'本版目标为{metric}{target}。']
    axes = context['axes']
    if axes.get('normalization'):
        lines.append(_DIFFRACTION_VIEWS[axes.get('view', 'physical')][1] + '选择按目标和数值检查判定。')
    if axes.get('radius_convention'):
        lines.append('图中的半径采用 1/e² 强度定义，位置以透镜为原点。')
    for row in context['candidates']:
        parameters, result, metrics = row['parameters'], row['goal'], row['metrics']
        changes = []
        for key, label, unit in (('slit_width_um', '缝宽', 'μm'), ('slit_separation_um', '缝距', 'μm'),
                                 ('relative_phase_rad', '相对相位', 'rad'), ('focal_length_mm', '焦距', 'mm'),
                                 ('wavelength_nm', '波长', 'nm'), ('layer_count', '层数', '')):
            if key in parameters:
                changes.append(f'{label} {_number(parameters[key])} {unit}'.rstrip())
        if result.get('value') is None:
            lines.append(row['id'] + ' 本版没有通过的目标数值。')
            continue
        state = '数值核对未通过' if not row['checks_passed'] else ('达标' if result.get('met') is True else '未达标' if result.get('met') is False else '参与排序')
        basis = '，解析几何' if row['goal_source'] == 'ANALYTIC_GEOMETRY' else ''
        description = f"{row['id']}（{'，'.join(changes)}）{metric} {_number(result['value'])} {result['unit']}，{state}{basis}。"
        if 'peak_intensity' in metrics:
            description += 'FFT 峰值 ' + _number(metrics['peak_intensity']) + '。'
        if 'fringe_period_mm' in metrics and goal['metric'] != 'fringe_period_mm':
            description += '解析条纹周期 ' + _number(metrics['fringe_period_mm']) + ' mm。'
        if 'new_waist_z_mm' in metrics:
            description += '新束腰位置 ' + _number(metrics['new_waist_z_mm']) + ' mm。'
        lines.append(description)
    return lines


def _prediction_reason(prediction):
    direction = prediction['direction']
    if direction == 'UNRESOLVED':
        return '核对改动后即可比较原值与新值。'
    movement = {'DECREASE': '两侧第一暗纹会向中心靠近。', 'INCREASE': '两侧第一暗纹会离中心更远。',
                'UNCHANGED': '横向第一暗纹位置保持不变。'}[direction]
    return '在理想焦平面中，横向暗纹位置随波长和焦距的乘积变化，并与缝宽成反比。' + movement


_SCREEN_MOVE = re.compile(r'(?:从|由)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*'
                          r'(?:向前|向后|往前|往后)?(?:移回?|挪回?|改|变|换)?'
                          r'(?:到|至|成|为)?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)', re.I)


def _screen_comparison(question, context, selected):
    move = _SCREEN_MOVE.search(question)
    if not move or move[1] == move[2]:
        return None
    fixed_fields = ('wavelength_nm', 'focal_length_mm', 'waist_radius_um', 'waist_z_mm')
    fixed = selected['parameters']
    endpoints = []
    for position in map(float, move.groups()):
        matches = [row for row in context['candidates'] if row['status'] == 'RUN_COMPLETED'
                   and row['parameters'].get('screen_distance_mm') == position
                   and all(key in fixed and row['parameters'].get(key) == fixed[key] for key in fixed_fields)
                   and finite(row['metrics'].get('screen_radius_um')) and finite(row['metrics'].get('new_waist_z_mm'))]
        if len(matches) != 1:
            return None
        row = matches[0]
        endpoints.append({'candidate_id': row['id'], 'screen_distance_mm': position,
                          'screen_radius_um': row['metrics']['screen_radius_um'],
                          'waist_z_mm': row['metrics']['new_waist_z_mm']})
    start, end = endpoints
    if abs(start['waist_z_mm'] - end['waist_z_mm']) > 1e-9:
        return None
    return {'start': start, 'end': end, 'common_waist_z_mm': start['waist_z_mm']}


def registered_conclusion(question, context, prediction):
    if prediction and prediction['after_mm'] is not None:
        names = {'DECREASE': '减小', 'INCREASE': '增大', 'UNCHANGED': '保持不变'}
        before, after = (_number(prediction[key]) for key in ('before_mm', 'after_mm'))
        conclusion = f"{prediction['metric_label']}{names[prediction['direction']]}，由 {before} mm 变为 {after} mm。"
        vertical = prediction['vertical']
        dimensions = (f"纵向中央全宽由 {_number(vertical['before_mm'])} mm 变为 {_number(vertical['after_mm'])} mm。"
                      if abs(vertical['after_mm'] - vertical['before_mm']) >= 1e-12
                      else '纵向中央全宽保持不变。')
        return {'id': 'slit_registered_change', 'text': conclusion,
                'styles': {'mechanism': _prediction_reason(prediction) + dimensions,
                           'formula': '横向尺度比例由新旧波长比、焦距比和旧缝宽除以新缝宽相乘得到。'
                                      '纵向使用缝高作同样的计算。' + dimensions,
                           'example': '可把暗纹间距看作图样的横向刻度。沿用同一坐标比较两版图，'
                                      '先找左右第一暗纹的位置，再观察纵向变化。' + dimensions}}
    domain = context['domain'] if context else None
    inverse = context and context['source']['kind'] == 'INVERSE_RESULT'
    if inverse and domain == 'film' and re.search(r'补测|新增观测|新增波长|排除|多测|重复|不一致|数据错|模型错', question):
        facts = context['facts']
        change = facts.get('acquisition_comparison') or {}
        impact = change.get('impact')
        if impact:
            before, after = change['previous_modes'], facts['retained_modes']
            statement = f'在同一厚度范围内，新增观测前后分别保留 {before} 组、{after} 组候选。'
            repeat = '重复同一波长可评估观测波动；该波长处给出相同反射率的候选仍难区分。'
            method = ('把新增反射率与旧候选在相应波长的预测对照，再按联合拟合的保留区间核对。'
                      if not impact['same_wavelength_only'] else repeat)
            if impact['possible_mismatch']:
                method = '本次联合拟合残差偏大。保留两份观测，分别核对校准、数据条件和单膜模型。'
            if re.search(r'多测|重复|同一波长', question) and not impact['possible_mismatch']:
                method = repeat
            return {'id': 'film_observation_impact', 'text': statement,
                    'styles': {'mechanism': method, 'formula': method + '不同波长改变往返相位随厚度变化的关系。',
                               'example': method + '可以在新增观测面板对照本次保存的反射率。'}}
    if inverse and re.search(r'残差|RMSE|拟合得很|拟合得好|误差很小|能信|可信', question,re.I):
        facts = context['facts']; evidence = facts.get('residual_evidence') or []
        holdout = sum(row['holdout_points'] for row in evidence)
        first = evidence[0].get('training_rmse') if evidence else None
        unit = {'film':'比例','diffraction':'相对光强','gaussian':'μm'}[domain]
        measured = f"第一组拟合点 RMSE 为 {_number(first)} {unit}。" if finite(first) else ''
        check = (f'本版另有 {holdout} 个留出点可检查预测。' if holdout else '本版的点用于拟合，可另采数据检查预测。')
        if domain == 'film' and facts.get('retained_modes',0)>1:
            check += f"当前范围内还有 {facts['retained_modes']} 组候选，可选预测分歧较大的波长继续区分。"
        return {'id':'inverse_residual_evidence','text':measured+'残差衡量模型曲线与这批观测的差异。',
                'styles':{'mechanism':check,'formula':'RMSE 是观测减预测后的均方根。'+check,
                          'example':'先看残差图有无连续偏向同一侧，再核对数据解释和候选。'+check}}
    if inverse and domain != 'film' and re.search(r'补采|补测|下一(?:次|份)数据|减少噪声|区分候选',question):
        facts=context['facts']
        return {'id':'inverse_observation_goal','text':f"本版使用 {facts['datasets']} 组观测，共 {facts['points']} 点。可以按想解决的问题选择补采方式。",
                'styles':{'mechanism':'在已有位置重复观测可评估波动；在候选预测不同的位置补采可区分候选。',
                          'formula':'先比较候选在拟采位置的预测分歧，再用已声明的噪声尺度核对能否分辨。',
                          'example':'高斯光束可在不同纵向位置比较半径，双缝可查看候选分歧较明显的坐标。核对采集条件后把新数据加入联合分析。'}}
    gaussian = domain == 'gaussian' or bool(re.search(r'高斯|TEM.?00|瑞利|1\s*/\s*e', question, re.I))
    if domain == 'gaussian' and not inverse and knowledge_retrieval.gaussian_screen_waist_question(question):
        selected = next(row for row in context['candidates'] if row['id'] == context['selected_candidate_id'])
        waist = selected['metrics'].get('new_waist_z_mm')
        if finite(waist):
            principle = '透镜和入射光束固定时，只移动屏幕，束腰位置保持。'
            pair = _screen_comparison(question, context, selected)
            summary = f'当前候选的新束腰位置为 {_number(waist)} mm。'
            if pair:
                start, end = pair['start'], pair['end']
                summary = (f"屏幕从 {_number(start['screen_distance_mm'])} mm 移到 {_number(end['screen_distance_mm'])} mm，"
                           f"屏幕半径由 {_number(start['screen_radius_um'])} μm 变为 {_number(end['screen_radius_um'])} μm。"
                           f"两处读数对应同一束腰位置 {_number(pair['common_waist_z_mm'])} mm。")
            elif _SCREEN_MOVE.search(question):
                summary = '当前记录没有同时保存问题指定的两个屏幕位置和读数。'
            return {'id': 'gaussian_screen_waist', 'text': summary, 'screen_comparison': pair,
                    'styles': {'mechanism': principle + '屏幕只改变读取半径的截面。曲线最低点标出束腰。',
                               'formula': principle + '传播曲线以束腰位置为中心，屏幕位置是曲线的取样坐标。',
                               'example': principle + '在横轴上找屏幕的位置读半径，再找曲线最低点。'}}
    if gaussian and re.search(r'瑞利|z.?r', question, re.I):
        return {'id': 'gaussian_rayleigh', 'text': '瑞利长度是从束腰传播到光斑半径增至束腰半径 √2 倍的距离。',
                'styles': {'mechanism': '束腰越大，光束的发散越慢。瑞利长度随束腰半径的平方增大，随波长增大而减小。',
                           'formula': '近轴远场发散半角约为 λ/(πw₀)，同时由传播曲线可写成 w₀/zᵣ。联立得到 zᵣ = πw₀²/λ。'
                                      '固定波长时，增大束腰既增大起始半径，又减小发散，因此出现平方关系。',
                           'example': '同一种颜色下，把束腰半径加倍，光斑从最细处扩大到 √2 倍所需的距离会变为四倍。'}}
    if gaussian and re.search(r'光斑|半径|直径|束腰|焦距|1\s*/\s*e', question, re.I):
        return {'id': 'gaussian_section_radius',
                'text': '高斯光斑的 1/e² 强度半径，是在同一横截面上强度降至该截面中心强度的 1/e² 时离中心的距离。',
                'styles': {'mechanism': '透镜改变新束腰的大小与位置，固定屏幕上的半径取决于这两项。'
                                       '束腰位于传播曲线的最低点。',
                           'formula': '同一截面的强度为 I(r,z) = I(0,z) exp(−2r²/w(z)²)。'
                                      '直径为 2w，半高全宽对应另一强度阈值。',
                           'example': '可以在屏幕上从中心向外找同一亮度比的边界，再沿传播方向找半径最小的截面。'
                                      '这两个位置可以不同。'}}
    if re.search(r'光学厚度|四分之一波', question):
        return {'id': 'film_optical_thickness', 'text': '正入射时光学厚度等于折射率乘以几何膜厚。四分之一波层满足 nd = λ₀/4。',
                'styles': {'mechanism': '同一设计波长下，折射率较大的材料达到相同光学厚度时，所需几何厚度更薄。',
                           'formula': '用真空设计波长 λ₀ 除以 4n，得到几何厚度 d。',
                           'example': '比较两种材料时先固定设计波长，再分别用各自折射率换算几何厚度。'}}
    if domain == 'film' and re.search(r'多解|多个膜厚|厚度候选|补测|候选变|保留的厚度', question):
        return {'id': 'film_more_observations', 'text': '在单波长下，相近的反射率可以对应几个不同膜厚。',
                'styles': {'mechanism': '薄膜的往返相位随厚度增加而周期变化。选一个使现有候选预测分开的波长，可以用新的观测筛选候选。',
                           'formula': '法向入射的往返相位为 φ = 4πnd/λ。换波长会改变相位随厚度变化的尺度。',
                           'example': '假设两种厚度在第一次观测中给出近似亮度。找一个让两种预测明显分开的波长，再看新观测更接近哪些候选。'}}
    selected = next((row for row in (context or {}).get('candidates', [])
                     if row['id'] == context.get('selected_candidate_id')), {})
    single = selected.get('parameters', {}).get('aperture') == 'single_slit'
    if (single or '单缝' in question) and re.search(r'缝越宽|缝变宽|缝越窄|缝变窄|缝宽、包络|缝宽.{0,8}(?:亮区|主瓣)', question):
        return {'id': 'slit_width_spread', 'text': '波长与焦距相同时，缝越宽，横向中央亮区越窄。',
                'styles': {'mechanism': '在同一观察方向，缝变宽会让两端的相位差变大，第一处抵消在更靠近中心的位置出现。',
                           'formula': '第一暗纹距离是 x₁ = λf/a。固定 λ 和 f 时，增大缝宽 a 会使 x₁ 减小。',
                           'example': '可以用左右第一条暗纹给亮区画两条边线。保持光源和透镜，换更宽的缝片后，两条边线会靠近。'}}
    return None


def _figure_conclusion(figure, context):
    if not figure or not figure['id'].startswith('slit_dark_fringe_'):
        return None
    row = next(row for row in context['candidates'] if row['id'] == figure['candidate_id'])
    distance = row['metrics'][FIRST_MINIMUM]
    x = distance if figure['id'].endswith('_right') else -distance
    return {'id': 'selected_slit_minimum',
            'text': f"{figure['label']}的解析位置为 {_number(x)} mm，距中心 {_number(distance)} mm。",
            'styles': {'mechanism': '这条线由保存的波长、焦距和缝宽算出。曲线来自场计算，可在标记附近查看强度低谷。',
                       'formula': '理想焦平面上，第一暗纹距中心为 x₁ = λf/a，左右两侧分别取负号和正号。',
                       'example': '先在横轴找中心，再找这一侧的虚线和附近低谷。跨过中心到另一侧暗纹才量得全宽。'}}


def _result_topic(context):
    if context['source']['kind'] == 'INVERSE_RESULT':
        return 'film-retained-modes' if 'retained_modes' in context['facts'] else 'fit-residuals'
    row = next((row for row in context['candidates'] if row['id'] == context['selected_candidate_id']), {})
    for metric, concept in ((FULL_WIDTH, 'slit-width-definitions'), ('fringe_period_mm', 'double-period'),
                            ('screen_radius_um', 'gaussian-waist-screen'), ('layer_count', 'quarter-wave')):
        if metric in row.get('metrics', {}):
            return concept
    return None


def _bound_references(context, prediction=None, cards=(), *, include_vertical=False):
    values = {'formula_' + card['id'].replace('-', '_'): card['formula'] for card in cards if card.get('formula')}
    if any(card['id'] == 'gaussian-rayleigh' for card in cards):
        values['rayleigh_radius_ratio'] = '√2'
    if prediction and prediction['after_mm'] is not None:
        full = prediction['metric'] == FULL_WIDTH
        for stage in ('before', 'after'):
            width = prediction[stage + '_mm']
            values['horizontal_full_width_' + stage] = f'{_number(width if full else 2 * width)} mm'
            values['horizontal_first_zero_' + stage] = f'{_number(width / 2 if full else width)} mm'
            if include_vertical:
                vertical = prediction['vertical'][stage + '_mm']
                values['vertical_full_width_' + stage] = f'{_number(vertical)} mm'
                values['vertical_first_zero_' + stage] = f'{_number(vertical / 2)} mm'
        for row in prediction['changes']:
            if row['state'] == 'CHANGE':
                values['proposed_' + row['field']] = f"{_number(row['after'])} {row['unit']}"
        values['preview_scale'] = _number(prediction['scale'])
    if not context:
        return values
    rows = context.get('candidates', [])
    selected = next((row for row in rows if row['id'] == context.get('selected_candidate_id')), {})
    for key, unit in (('wavelength_nm', 'nm'), ('focal_length_mm', 'mm'), ('slit_width_um', 'μm'),
                      ('slit_height_um', 'μm'), ('slit_separation_um', 'μm'), ('waist_radius_um', 'μm'),
                      ('screen_distance_mm', 'mm')):
        if key in selected.get('parameters', {}):
            values[key] = f"{_number(selected['parameters'][key])} {unit}"
    goal = selected.get('goal', {})
    if finite(goal.get('value')):
        values['candidate_value'] = f"{_number(goal['value'])} {goal['unit']}"
    if 'retained_modes' in context.get('facts', {}):
        values['retained_modes'] = str(context['facts']['retained_modes'])
    if context['source']['kind'] == 'INVERSE_RESULT':
        unit = {'film':'比例','diffraction':'相对光强','gaussian':'μm'}[context['domain']]
        for index, row in enumerate(context['facts'].get('residual_evidence', []),1):
            for key in ('training_rmse','holdout_rmse'):
                if finite(row.get(key)):
                    values[f'group_{index}_{key}'] = f'{_number(row[key])} {unit}'
        added = context['facts'].get('acquisition_comparison')
        if added:
            values['previous_modes'] = str(added['previous_modes'])
            values['added_wavelengths_nm'] = '、'.join(map(_number,added['added_wavelengths_nm'])) + ' nm'
            for index, point in enumerate((added.get('impact') or {}).get('points', []), 1):
                values[f'added_point_{index}_observed'] = _number(point['reflectance'])
                values[f'added_point_{index}_prior_range'] = '–'.join(map(_number, point['prior_prediction_range']))
    for key, unit in (('first_zero_mm', 'mm'), ('fringe_period_mm', 'mm'), ('screen_radius_um', 'μm'),
                      ('new_waist_radius_um', 'μm'), ('new_waist_z_mm', 'mm'), ('input_rayleigh_range_mm', 'mm')):
        value = selected.get('metrics', {}).get(key)
        if finite(value):
            values[key] = f'{_number(value)} {unit}'
    return values


def _focused_facts(context, facts, question, depth):
    if not context or not facts:
        return []
    if context['source']['kind'] == 'FORWARD_RESULT':
        selected = context.get('selected_candidate_id')
        row = next((line for line in facts if selected and line.startswith(selected)), None)
        axis = next((line for line in facts if '纵轴' in line or '半径采用' in line), None)
        if re.search(r'图轴|纵轴|颜色|色标|标尺|归一化|分贝|侧瓣', question):
            priority = [axis, row, facts[0]]
        else:
            priority = [row, facts[0], axis] if depth == 'result' else [facts[0], row, axis]
        return [line for line in dict.fromkeys(priority) if line][:3 if depth == 'result' else 2]
    relevant = [line for line in facts if '候选' in line or '补测' in line]
    if re.search(r'残差|RMSE|误差|可信',question,re.I):
        relevant = [line for line in facts if 'RMSE' in line] + relevant
    elif re.search(r'原始|直径|列解释|单位',question):
        relevant = [line for line in facts if '原文件' in line or '列解释' in line] + relevant
    return list(dict.fromkeys(relevant + facts))[:3 if depth == 'result' else 2]


def _compare_saved_results(current, reference, *, single_side=False):
    def selected(context):
        return next((row for row in context.get('candidates', []) if row['id'] == context.get('selected_candidate_id')), {})

    before, after = selected(reference), selected(current)
    reference_facts, current_facts = reference.get('facts', {}), current.get('facts', {})
    metric_specs = [(FULL_WIDTH, '中央亮区全宽', 'mm'), (FIRST_MINIMUM, '中心到单侧第一暗纹的距离', 'mm'),
                    ('fringe_period_mm', '双缝条纹周期', 'mm'), ('screen_radius_um', '屏幕光斑半径', 'μm'),
                    ('new_waist_z_mm', '新束腰位置', 'mm'), ('retained_modes', '保留的膜厚候选', '组')]
    if single_side and FIRST_MINIMUM in after.get('metrics', {}) and FIRST_MINIMUM in before.get('metrics', {}):
        metric_specs.sort(key=lambda item: item[0] != FIRST_MINIMUM)
    elif FULL_WIDTH in after.get('metrics', {}):
        metric_specs = [item for item in metric_specs if item[0] != FIRST_MINIMUM]
    entries = []
    for key, label, unit in metric_specs:
        old = before.get('metrics', {}).get(key, reference_facts.get(key))
        new = after.get('metrics', {}).get(key, current_facts.get(key))
        if finite(old) and finite(new):
            entries.append({'metric': key, 'label': label, 'unit': unit, 'reference_value': old, 'current_value': new})
    old_params, new_params = before.get('parameters', {}), after.get('parameters', {})
    return {'reference': reference['source'], 'current': current['source'], 'entries': entries,
            'reference_candidate_id': reference.get('selected_candidate_id'), 'current_candidate_id': current.get('selected_candidate_id'),
            'changes': {key: {'from': old_params[key], 'to': new_params[key]} for key in old_params.keys() & new_params.keys()
                        if old_params[key] != new_params[key]}}


def _comparison_authority(comparison, question=''):
    if not comparison['entries']:
        return None
    first = comparison['entries'][0]
    unit, old, new = first['unit'], _number(first['reference_value']), _number(first['current_value'])
    title = f"两个已保存结果的{first['label']}分别为 {old} {unit} 和 {new} {unit}。"
    mechanism = ('沿同一侧，从图的中心量到第一条暗纹。左侧坐标带负号，离中心的距离取坐标的绝对值。'
                 if first['metric'] == FIRST_MINIMUM else
                 '在理想焦平面中，沿同一轴比较时，图样尺度随波长和焦距的乘积增加，随该轴开口尺寸增加而减小。'
                 if first['metric'] == FULL_WIDTH else '逐项核对两版已采用的条件与选中候选，按同一指标比较。')
    direction = None
    if first['metric'] == FULL_WIDTH:
        a, b = first['reference_value'], first['current_value']
        direction = 'UNCHANGED' if abs(a - b) <= 1e-9 else 'DECREASE' if b < a else 'INCREASE'
    authority = {'id': 'saved_result_comparison', 'text': title, 'direction': direction,
                 'slit_scaling': first['metric'] in (FULL_WIDTH, FIRST_MINIMUM),
                 'styles': {'mechanism': mechanism, 'formula': mechanism,
                            'example': '先对齐两版的观察位置和指标定义，再读出各自保存的值。'}}
    if question and (causes := comparison.get('causes')):
        directions = {'INCREASE': '变大', 'DECREASE': '变小', 'UNCHANGED': '保持不变'}
        focus = re.search(r'(?:只|单独).{0,5}(波长|焦距|缝宽)', question)
        rows = [row for row in causes['effects'] if not focus or focus[1] == row['label']]
        effects = '；'.join(
            f"只改{row['label']}，{first['label']}{directions[row['direction']]}" for row in rows)
        if focus:
            body = (effects + '。' if rows else f"两版{focus[1]}相同，这一项没有造成差别。")
        else:
            together = '各项变化沿同一方向影响这个量' if len(rows) > 1 else '其余横向尺度条件保持'
            if causes['opposing']:
                together = '不同方向的作用' + ('完全抵消' if causes['direction'] == 'UNCHANGED' else '部分抵消')
                if dominant := next((row for row in rows if row['field'] == causes['dominant_field']), None):
                    together += f"，{dominant['label']}的作用较大"
            net = f"{together}，合并后{first['label']}{directions[causes['direction']]}。"
            body = net if re.search(r'直接.{0,5}结论', question) else effects + '。' + net
        authority.update(direction=None, combined_direction=causes['direction'], styles={
            'mechanism': body,
            'formula': '固定其余条件逐项计算，再把各项倍率相乘。' + body,
            'example': '从旧图的暗纹位置起步，每次只替换一个条件。' + body,
        })
    return authority


_SCALE_PARAMETER = r'波长|焦距|缝宽'
_SCALE_VERB = r'变长|加长|变短|缩短|增大|变大|变宽|减小|变小|变窄|加宽|收窄'
_SCALE_CHANGE = re.compile(fr'({_SCALE_PARAMETER})(?:单独|各自)?\s*({_SCALE_VERB})|'
                           fr'({_SCALE_VERB})了?\s*({_SCALE_PARAMETER})')
_SCALE_GROWS = frozenset(('变长', '加长', '增大', '变大', '变宽', '加宽'))
_SCALE_EFFECT = re.compile(r'推远|推近|向外移|向内移|(?:离|距)中心更(?:远|近)|远离中心|向中心靠近|更?靠近中心'
                           r'|(?:图样|全宽|距离)(?:会|就|也)?(?:变大|变小|变宽|变窄)')


def _wrong_slit_scaling(text):
    for sentence in re.split(r'[。！？；\n]', text):
        edits = [edit for edit in _SCALE_CHANGE.finditer(sentence)
                 if not re.search(r'(?:未|没有|不|不要|不能)\s*$', sentence[:edit.start()])]
        if not edits:
            continue
        tail = sentence[edits[-1].end():]
        effect = _SCALE_EFFECT.search(tail)
        if not effect or effect.start() > 26:
            continue
        lead = tail[:effect.start()]
        if re.search(r'不一定|未必|如果|但|取决于', lead):
            continue
        # One combined net change needs the product of the actual values.
        # Separate claims joined by “或” or “都” can be checked individually.
        join = sentence[edits[0].end():edits[-1].start()] + lead
        if len(edits) > 1 and not re.search(r'或|都|各自|分别|均', join):
            continue
        grows = bool(re.search(r'远|外移|变大|变宽', effect[0]))
        negated = bool(re.search(r'不会|不能|并不|没有(?:让|使|导致)|不可能', lead))
        for edit in edits:
            parameter, verb = (edit[1], edit[2]) if edit[1] else (edit[4], edit[3])
            expected_grows = (verb in _SCALE_GROWS) != (parameter == '缝宽')
            if (expected_grows == grows) == negated:
                return True
    return False


def _conflicts_with_evidence(text, authority, prediction):
    if re.search(r'(?:点击|提交|运行).{0,10}(?:Shell|终端|命令)|(?:保证|证明).{0,8}(?:真实厚度唯一|唯一真值|唯一解|仪器精度|绝对准确)', text, re.I):
        return True
    if authority and authority['id'] in ('gaussian_section_radius', 'gaussian-waist'):
        for match in _WRONG_RADIUS_REFERENCE.finditer(text):
            if not re.search(r'(?:不是|并非|不要|无需|不可|不必|不能|不)$', text[max(0, match.start() - 4):match.start()]):
                return True
    if authority and authority['id'] == 'gaussian_screen_waist':
        if re.search(r'束腰(?:位置)?(?:也|就|会|也会)?(?:随着|随|跟着)屏幕(?:一起|一同)?(?:改变|移动|后移|前移)', text):
            return True
    if authority and authority['id'] == 'gaussian-waist':
        for sentence in re.split(r'[。！？；，,\n]', text):
            if (re.search(r'(?:各截面|不同截面|所有截面).{0,18}(?:同一(?:个)?|统一(?:的)?)(?:中心|轴心|峰值)强度', sentence)
                    and not re.search(r'各自|分别|不共用|不使用|不是|不采用|并非', sentence)):
                return True
            claim = re.search(r'束腰(?:处|位置|半径)?[^。]{0,14}(?:最宽|最粗|最大(?:半径|光斑)|半径最大|半径为零)', sentence)
            if claim and not re.search(r'不是|并非|不能|不会|不等于|曲率|波前', sentence[:claim.end()]):
                return True
    if (prediction or authority and (authority.get('slit_scaling') or authority['id'] == 'slit_width_spread')) and _wrong_slit_scaling(text):
        return True
    if prediction or authority and authority.get('slit_scaling'):
        for match in _SLIT_FOCUS_REFERENCE.finditer(text):
            if not re.search(r'(?:不是|并非|不能用|不能当作)$', text[max(0, match.start()-7):match.start()]):
                return True
    if authority and (net := authority.get('combined_direction')):
        expected_net = _HORIZONTAL_DIRECTIONS[net]
        for claim in re.finditer(r'(?:合并后|两项一起(?:改|变)后|最终|净效果)([^。！？；\n]*)', text):
            for actual in _HORIZONTAL_CLAIM.finditer(claim[1]):
                if actual['direction'] not in expected_net:
                    return True
    expected = (prediction['direction'] if prediction else 'DECREASE' if authority and authority['id'] == 'slit_width_spread'
                else (authority or {}).get('direction'))
    if expected not in _HORIZONTAL_DIRECTIONS:
        return False
    if prediction and re.search(r'(?:新版本|这次|本次|已经|现已).{0,6}(?:完成实算|运行了|重新计算了|实测了|完成计算)', text):
        return True
    if prediction and re.search(r'峰值[^。！？；\n]{0,36}(?:基本不变|保持不变|会变[大小]|会升高|会降低|也会改变)', text):
        return True
    for match in _HORIZONTAL_CLAIM.finditer(text):
        start, end = match.span('direction')
        if '纵向' in text[max(0, match.start() - 3):match.start()] or re.search(r'(?:不会|并未|不能|未|不)$', text[match.start():start]):
            continue
        if text[start:end] not in _HORIZONTAL_DIRECTIONS[expected]:
            return True
    return False


def _reference_conflict(text, slot):
    """Catch direct assignment of a width or version reference to the wrong role."""
    for sentence in re.split(r'[。！？；\n]', text):
        if _sentence_reference_conflict(sentence, slot):
            return True
    return False


def _sentence_reference_conflict(sentence, slot):
    if re.search(r'(?:从|由)\s*\[\[screen_end_(position|radius)\]\]'
                 r'[^。！？；\n]{0,36}\[\[screen_start_\1\]\]', sentence):
        return True
    axis = 'horizontal_'
    for clause in re.split(r'[，,]', sentence):
        horizontal = bool(re.search(r'横向|水平方向', clause))
        vertical = bool(re.search(r'纵向|竖直', clause))
        if horizontal != vertical:
            axis = 'vertical_' if vertical else 'horizontal_'
        keys = re.findall(slot, clause)
        if not keys:
            continue
        prior, current = bool(re.search(r'参考版|旧版|上一版|原版', clause)), bool(re.search(r'当前版|新版|本版|此版', clause))
        if prior != current and any(key.startswith('current_' if prior else 'reference_') for key in keys):
            return True
        # Bind each slot to its own preceding label. A single comma-delimited
        # clause can list full width and one-sided distance side by side.
        role, end = None, 0
        for match in re.finditer(slot, clause):
            label = clause[end:match.start()]
            full = bool(re.search(r'全宽|两侧暗纹间距|左右暗纹间距', label))
            side = bool(re.search(r'(?:单侧|左侧|右侧|中心到).{0,9}(?:暗纹|距离|位置)|第一暗纹位置|(?:单侧|左侧|右侧)\s*$', label))
            if full != side:
                role = 'full_width_' if full else 'first_zero_'
            elif full:
                role = None  # Both definitions are discussed, not assigned.
            key = match[1]
            if role and key.startswith(('horizontal_', 'vertical_')) and not key.startswith(axis + role):
                return True
            end = match.end()
    return False


def _validated_explanation(response, cards, authority=None, references=None, *, prediction=None, targets=None):
    obj = strict_object(response)
    required = {'intent', 'intro', 'card_ids'} | ({'conclusion_id'} if authority else set())
    if not required <= obj.keys() or set(obj) - required - {'highlight', 'suggestion'} or obj['intent'] not in _INTENTS:
        raise ValueError('Unexpected tutor response')
    if authority and obj['conclusion_id'] != authority['id']:
        raise ValueError('Conclusion does not match the program')
    text, ids = obj['intro'], obj['card_ids']
    refs = references or {}
    if not isinstance(text, str) or not 8 <= len(text.strip()) <= 600 or wire.credential_risk(text):
        raise ValueError('Unusable explanation')
    slot = r'\[\[([a-z0-9_]+)\]\]'
    bare = re.sub(slot, '', text)
    matches = re.findall(slot, text)
    unbound = bare
    if constant := _DEFINITION_CONSTANTS.get((authority or {}).get('id')):
        key, literal, pattern = constant
        if refs.get(key) == literal:
            unbound = pattern.sub('', bare)
    if any(key not in refs for key in matches) or re.search(r'[\d<>=]|\[\[|\]\]|https?[:：]|DOI|www\.', unbound, re.I):
        raise ValueError('Narrative contains an unbound value, formula or link')
    if _conflicts_with_evidence(bare, authority, prediction) or _reference_conflict(text, slot):
        raise ValueError('Narrative conflicts with the saved evidence')
    intro = re.sub(slot, lambda match: refs[match[1]], text).strip()
    if authority and (intro == authority['text'] or intro in authority['styles'].values()):
        raise ValueError('Narrative repeats a stored paragraph')
    if not isinstance(ids, list) or not ids or len(ids) > len(cards) or any(type(key) is not str for key in ids):
        raise ValueError('No usable card reference')
    by_id = {card['id']: card for card in cards}
    if any(key not in by_id for key in ids) or len(ids) != len(set(ids)):
        raise ValueError('Unknown card reference')
    if any(key.startswith('formula_') and key not in {'formula_' + card.replace('-', '_') for card in ids} for key in matches):
        raise ValueError('Formula citation requires its source card')
    choices = targets or {}
    pointers = {}
    for key in ('highlight', 'suggestion'):
        value = obj.get(key)
        if value is None or value == '' or value == []:
            continue
        items = value if isinstance(value, list) else [value]
        if len(items) > 2 or any(not isinstance(item, str) or item not in choices.get(key, ()) for item in items):
            raise ValueError('Unregistered highlight or suggestion')
        if len(set(items)) == 1:
            pointers[key] = items[0]
    return {'intro': intro, 'intent': obj['intent'], 'cards': [by_id[key] for key in ids], **pointers}


_NAMED_OLD_SLIT = re.compile(r'(第一版|初版|参考版|原版|旧版|上一版)(?:的)?(?:缝宽(?:为|是)?\s*)?\s*'
                             r'(\d+(?:\.\d+)?)\s*(?:[μµu]m|微米)', re.I)


def _named_slit_reference(store, question, context, reference, *, live):
    match = _NAMED_OLD_SLIT.search(question)
    if not match or context['domain'] != 'diffraction' or context['source']['kind'] != 'FORWARD_RESULT':
        return reference
    if re.match(r'\s*(?:、|和|或|与)\s*\d', question[match.end():]):
        raise WorkflowError('请一次选明要解释的一个参照缝片。', 409)
    first = match[1] in ('第一版', '初版')
    family = context['learning_task_id']
    if not reference and not first or live and not reference:
        raise WorkflowError('请在助教侧栏先选参照版本和对应缝片。', 409)
    record_id = reference['source']['record_id'] if reference else family
    if first and record_id != family or record_id == context['source']['record_id']:
        raise WorkflowError('请选中该任务的第一版已保存结果。', 409)
    record = store.verified_case(record_id)
    candidates = [row['id'] for row in (record.get('result') or {}).get('candidates', [])
                  if row['parameters'].get('aperture') == 'single_slit'
                  and row['parameters']['slit_width_um'] == float(match[2])]
    if len(candidates) != 1:
        raise WorkflowError('参照结果中没有唯一匹配的缝片，请核对版本及候选。', 409)
    candidate = candidates[0]
    if live and candidate != reference['selected_candidate_id']:
        raise WorkflowError('请在侧栏选择所说的参照缝片，再确认可发送的资料。', 409)
    if reference and candidate == reference['selected_candidate_id']:
        return reference
    result_hash = reference['source']['result_sha256'] if reference else None
    return read_result(store, record_id, result_hash, candidate)


def answer(store, data):
    keys = {'question', 'mode', 'approve_data', 'record_id', 'result_sha256', 'candidate_id',
            'session_id', 'turn_id', 'expected_turn_id', 'depth', 'view', 'learning_task_id', 'compare_to', 'figure_id'}
    if set(data) - keys or data.get('mode', 'local') not in ('local', 'live'):
        raise WorkflowError('助教问题字段无效。')
    question = data.get('question', '')
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1600 or wire.credential_risk(question):
        raise WorkflowError('请填写不含密钥的光学问题，最多 1600 字。')
    question = question.strip()
    requested = data.get('depth')
    if requested is not None and (not isinstance(requested, str) or requested not in learning_session.DEPTHS):
        raise WorkflowError('请选择直观、推导或当前结果。')
    record_id = data.get('record_id')
    if not record_id and any(data.get(key) for key in ('candidate_id', 'result_sha256')):
        raise WorkflowError('请同时指定要解释的结果记录。')
    context = read_result(store, record_id, data.get('result_sha256'), data.get('candidate_id')) if record_id else None
    task_id = data.get('learning_task_id')
    reference = None
    if 'learning_task_id' in data and (not isinstance(task_id, str) or not context or 'session_id' not in data
                                      or not data.get('result_sha256') or context['learning_task_id'] != task_id):
        raise WorkflowError('请明确选择本学习任务下的一个已保存结果版本。', 409)
    if 'compare_to' in data:
        other = data['compare_to']
        if (not task_id or not isinstance(other, dict) or set(other) - {'record_id', 'result_sha256', 'candidate_id'}
                or not other.get('record_id') or not other.get('result_sha256')):
            raise WorkflowError('对照需要同一学习任务内的保存版本和结果指纹。')
        reference = read_result(store, other['record_id'], other['result_sha256'], other.get('candidate_id'))
        if (reference['learning_task_id'] != task_id or reference['source']['record_id'] == context['source']['record_id']
                or reference['domain'] != context['domain'] or reference['source']['kind'] != context['source']['kind']):
            raise WorkflowError('请选择同一学习任务内的另一份同类结果。', 409)
    if task_id:
        reference = _named_slit_reference(store, question, context, reference, live=data.get('mode') == 'live')
    view = data.get('view', 'physical')
    if not isinstance(view, str) or view not in _DIFFRACTION_VIEWS:
        raise WorkflowError('未知的图表显示方式。')
    if view != 'physical' and (not context or context['domain'] != 'diffraction' or not context.get('axes')):
        raise WorkflowError('此显示方式需要有效的衍射曲线。')
    if context and context['domain'] == 'diffraction' and context.get('axes'):
        normalization, label = _DIFFRACTION_VIEWS[view]
        context['axes'].update(view=view, normalization=normalization, common_scale=view != 'peak')
        if view != 'physical':
            context['axes']['y'] = label.rstrip('。')
    figure = None
    if 'figure_id' in data:
        figure = knowledge_retrieval.selected_figure(context, data['figure_id'])
        if not figure or not data.get('result_sha256') or data.get('candidate_id') != figure['candidate_id']:
            raise WorkflowError('图形位置需要与当前保存结果及候选对应，请在图上重新选择。', 409)
    if 'session_id' in data:
        return learning_session.run_turn(store, data, context, question,
            lambda depth, topic, history: _answer_turn(store, data, question, context, depth, topic, history,
                                                      reference=reference, figure=figure),
            reference=reference)
    if 'turn_id' in data or 'expected_turn_id' in data:
        raise WorkflowError('请同时指定学习会话。')
    return _answer_turn(store, data, question, context, learning_session.depth_for(question, requested), None, [],
                        figure=figure)


def _answer_turn(store, data, question, context, depth, topic, history, *, reference=None, figure=None):
    from .learning_followup import predict, slit_change_causes

    kind = knowledge_retrieval.question_kind(question, depth)
    prediction = predict(question, context)
    domain = context['domain'] if context else None
    explain_changes = bool(reference and domain == 'diffraction' and (
        re.search(r'谁.{0,5}影响|(?:各自|分别).{0,8}(?:作用|影响)|(?:作用|影响).{0,5}(?:各自|分别)|'
                  r'抵消|(?:只|单独).{0,5}(?:波长|焦距|缝宽)|'
                  r'为什么.{0,16}(?:变|宽|窄|远|近)|(?:合并|合起来|共同|一起).{0,8}(?:作用|影响|变|宽|窄)', question)
        or (history and history[-1].get('compared_slit_changes')
            and re.search(r'不用公式|不看公式|(?:用|看)公式|推导|再简单|直接.{0,5}结论|再解释|看图', question))))
    if explain_changes and prediction and prediction['direction'] == 'UNRESOLVED' and not any(
            row['state'] == 'CHANGE' for row in prediction['changes']):
        prediction = None
    explicit_full = re.search(r'全宽|两侧暗纹|左右暗纹|整个中央亮区', question)
    single_side = not explicit_full and bool(
        re.search(r'暗纹', question) and re.search(r'离中心|距中心|单侧|左[边侧]|右[边侧]', question)
        or explain_changes and reference['goal']['metric'] == FIRST_MINIMUM)
    comparison = (_compare_saved_results(context, reference, single_side=single_side)
                  if reference and (kind == 'COMPARE' or explain_changes) else None)
    if comparison and comparison['entries']:
        causes = slit_change_causes(reference, context, comparison['entries'][0]['metric'])
        if causes:
            comparison['causes'] = causes
    anchors = (('slit-scale', 'slit-width-definitions') if prediction else
               knowledge_retrieval.result_concepts(question, context, data.get('view', 'physical')))
    cards, basis = knowledge_retrieval.retrieve(_knowledge(), question, domain, topic=topic, figure=figure,
                                                kind=kind, anchored=anchors)
    if not cards and context and re.search(r'总结(?:一下)?这次|总结本(?:次|版)|(?:这|本)版结果.{0,8}(?:看|读|解释)', question):
        concept = _result_topic(context)
        if concept:
            cards, basis = knowledge_retrieval.retrieve(_knowledge(), question, domain, kind=kind, anchored=(concept,))
    if not cards and comparison and comparison['entries']:
        concept_by_metric = {FULL_WIDTH:'slit-width-definitions', 'first_zero_mm':'slit-width-definitions',
                             'fringe_period_mm':'double-period', 'screen_radius_um':'gaussian-waist-screen',
                             'new_waist_z_mm':'gaussian-waist-screen', 'retained_modes':'film-retained-modes'}
        concept = concept_by_metric[comparison['entries'][0]['metric']]
        cards, basis = knowledge_retrieval.retrieve(_knowledge(), question, domain, kind=kind, anchored=(concept,))
    choices = []
    if basis in ('CLARIFY', 'QUESTION', 'CONVERSATION') and len(question)<=1000:
        from .knowledge_catalog import catalog
        from .knowledge_learning import select_articles
        book = catalog(store.owner.root)
        articles = select_articles(book,question)
        definitions = book.definition_matches(question,articles)
        named = book.named(question,articles)
        legacy_ids = {card['id'] for card in _knowledge()}
        previous_id = (topic or {}).get('id')
        if not book.definition_covered(question,[card['id'] for card in cards] + articles):
            cards, basis = [], 'CLARIFY'
        elif (history and history[-1].get('general_explanation')
              and knowledge_retrieval.general_followup(question) and not named):
            cards, basis, articles = [], 'CLARIFY', []
        if len(definitions)>1 and (not cards or not context):
            choices = [book.brief(ident) for ident in definitions]
            cards, basis = [], 'CLARIFY'
        elif definitions and definitions[0] not in legacy_ids:
            cards, basis = [_study_card(book.entries[definitions[0]])], 'KNOWLEDGE'
        elif (not cards and previous_id in book.entries and previous_id not in legacy_ids
              and not named and knowledge_retrieval.is_followup(question)):
            cards, basis = [_study_card(book.entries[previous_id])], 'CONVERSATION'
        elif articles and (not cards or not context and articles[0] not in legacy_ids and articles[0] in named):
            selected = named if articles[0] in named else articles
            cards = [_study_card(book.entries[ident]) for ident in selected[:3]]
            basis = 'KNOWLEDGE'
    primary = cards[0] if cards else None
    current_topic = {'id': primary['id'], 'title': primary['title']} if primary else None
    if basis in ('DEFINITION','KNOWLEDGE') or primary and (
        primary.get('catalog_article') or domain and domain not in primary['scope']
    ):
        # An explicit theory question can change scene while the task stays open.
        context, comparison, prediction = None, None, None
    figure = figure if basis == 'FIGURE' else None
    topical_question = question + (' ' + primary['title'] if basis in ('CONVERSATION', 'FIGURE') else '')
    checked_card = primary and primary.get('terms')
    authority = ((_comparison_authority(comparison, question if explain_changes else '') if comparison and not prediction else None)
                 or _figure_conclusion(figure, context)
                 or (registered_conclusion(topical_question, context, prediction) if cards and not checked_card else None))
    if authority and authority['id'] == 'gaussian_section_radius' and primary['id'] != 'gaussian-size':
        authority = None
    if not authority and checked_card:
        authority = {'id': primary['id'], 'text': primary['definition'],
                     'styles': {'mechanism': primary['intuitive_text'], 'formula': primary['detailed_text'],
                                'example': primary['verification']}}
    source = context['source'] if context else None
    facts = _grounded_facts(context) if primary else []
    requested_style = ('formula' if depth == 'formula' or kind == 'DERIVE'
                       else 'example' if re.search(r'没懂|例子|类比|用图|再简单', question) else None)
    style = requested_style or 'mechanism'
    if primary:
        intro = primary['detailed_text'] if style == 'formula' else primary['intuitive_text']
    elif domain == 'diffraction':
        intro = '想看的是中央亮区全宽、第一暗纹、纵向尺寸，还是光强标尺？可以说出想看的量，或在图上点选标记。'
    elif domain == 'gaussian':
        intro = '想核对屏幕上的半径、束腰位置，还是传播规律？可以说出想看的量，或在图上点选标记。'
    elif domain == 'film':
        intro = '想核对膜层条件、膜厚候选，还是补测和拟合？请指出其中一项。'
    else:
        intro = '本地知识库没有找到能直接回答的条目。可以换个光学术语，或浏览知识卡查找相关概念。'
    response = {'mode': 'LOCAL_REFERENCE', 'explanation_source': 'LOCAL_REFERENCE', 'question': question, 'result': context,
                'grounded_facts': facts, 'focused_facts': _focused_facts(context, facts, question, depth), 'source': source,
                'depth': depth, 'view': data.get('view', 'physical'), 'topic': current_topic, 'figure': figure,
                'retrieval': {'basis': basis, 'question_kind': kind, 'concept_ids': [card['id'] for card in cards]},
                'cards': cards, 'prediction': prediction, 'solver_calls': 0,
                'intent': 'PREDICT_CHANGE' if prediction else 'EXPLAIN_RESULT' if context else 'EXPLAIN_CONCEPT',
                'intro': intro}
    if choices:
        response.update(intro='这个术语有多种用法。可以选择下面的条目，或在问题里补充使用场景。',choices=choices)
    if prediction:
        response['prediction_reason'] = _prediction_reason(prediction)
        if prediction['can_prepare']:
            from .case_workflow import comparison_needs_new_round
            parent = store.get(prediction['source']['record_id'])
            response['verification_round_required'] = comparison_needs_new_round(store, parent)
    if comparison:
        response['comparison'] = comparison
    if authority:
        response['conclusion'] = {'id': authority['id'], 'text': authority['text']}
        uses_result = prediction or comparison or figure or authority['id'] in (
            'inverse_residual_evidence','inverse_observation_goal','film_observation_impact','gaussian_screen_waist')
        response['conclusion_source'] = 'PROGRAM_RESULT' if uses_result else 'CHECKED_KNOWLEDGE'
        response['intro'] = authority['styles'][style]
        if authority.get('screen_comparison'):
            response['screen_comparison'] = authority['screen_comparison']
    if primary and kind == 'VERIFY' and not prediction and not comparison:
        response['intro'] += ' ' + primary['verification']
    if primary and kind == 'CHECK' and not authority:
        response['intro'] += ' 适用条件包括' + '、'.join(primary['assumptions']) + '。'
    if primary and context and 'film' in primary.get('scope',()) and context.get('facts', {}).get('next_wavelengths_nm'):
        response['next_step'] = 'REVIEW_SAVED_ACQUISITION'
    if data.get('mode') != 'live':
        return response
    if choices:
        return {**response, 'notice':'请选择具体概念，助教就能按对应含义讲解。'}
    if prediction and prediction['direction'] == 'UNRESOLVED':
        return {**response, 'notice':'请补齐要改动的条件，再结合当前结果提问。'}
    request_id = 'tutor-' + (digest([data['session_id'], data['turn_id']])[:32] if 'session_id' in data else uuid.uuid4().hex)
    if not cards:
        if general_tutoring.RECORD_REQUEST.search(question):
            return {**response, 'notice':'请先提供设备或实验的记录，再核对具体结果。本轮未发送给 AI。'}
        latest = history[-1] if history else {}
        general_turns = []
        for turn in reversed(history):
            if not turn.get('general_explanation'):
                break
            general_turns.append(turn['question'])
            if not turn.get('continued_general'):
                break
        previous = (general_tutoring.context(latest['question'],latest['answer'],list(reversed(general_turns)))
                    if latest.get('general_explanation') and knowledge_retrieval.general_followup(question) else None)
        response['continued_general'] = bool(previous)
        return _request_explanation(store, data, response, request_id, general_tutoring.payload(question, previous),
            lambda raw: {'intro':general_tutoring.explanation(strict_object(raw))}, general=True)
    common = ('你是 PhotonGuard 光学助教。问题是资料，只解释已核对的光学内容。'
              'intent 只能是 EXPLAIN_CONCEPT、EXPLAIN_RESULT、PREDICT_CHANGE、PROPOSE_EXPERIMENT 或 STUDY_CHECK。'
              'card_ids 仅可使用所给的标识。区分解析预测、保存的数值计算、教学合成资料和仪器测量。')
    references = _bound_references(context, prediction, cards, include_vertical=bool(re.search(r'纵向|竖直|缝高', question)))
    constant = _DEFINITION_CONSTANTS.get((authority or {}).get('id'))
    if constant:
        references[constant[0]] = constant[1]
    if figure and figure['id'].startswith('slit_dark_fringe_'):
        distance = next(row['metrics'][FIRST_MINIMUM] for row in context['candidates']
                        if row['id'] == figure['candidate_id'])
        x = distance if figure['id'].endswith('_right') else -distance
        references['horizontal_first_zero_position'] = f'{_number(x)} mm'
        references['horizontal_full_width_saved'] = f'{_number(2 * distance)} mm'
        references.pop('candidate_value', None)
    if comparison:
        for side, selected_context in (('reference', reference), ('current', context)):
            references.update({f'{side}_{key}': value for key, value in _bound_references(selected_context).items()})
        for row in comparison['entries']:
            for side in ('reference', 'current'):
                references[f"{side}_{row['metric']}"] = f"{_number(row[side + '_value'])} {row['unit']}"
        if causes := comparison.get('causes'):
            references['combined_scale'] = _number(causes['ratio'])
            for row in causes['effects']:
                references['isolated_' + row['field']] = f"{_number(row['isolated_mm'])} mm"
    screen_comparison = response.get('screen_comparison')
    if authority and authority['id'] == 'gaussian_screen_waist' and _SCREEN_MOVE.search(topical_question):
        for key in ('screen_distance_mm', 'screen_radius_um', 'candidate_value', 'new_waist_z_mm'):
            references.pop(key, None)
        if screen_comparison:
            for endpoint in ('start', 'end'):
                row = screen_comparison[endpoint]
                references[f'screen_{endpoint}_position'] = f"{_number(row['screen_distance_mm'])} mm"
                references[f'screen_{endpoint}_radius'] = f"{_number(row['screen_radius_um'])} μm"
            references['common_waist_position'] = f"{_number(screen_comparison['common_waist_z_mm'])} mm"
    skip_formula = bool(re.search(r'(?:不想看|不看|不用|不要|先别写)(?:数学)?公式',question))
    if skip_formula:
        references = {key: value for key, value in references.items() if not key.startswith('formula_')}
    public_cards = [{key: card[key] for key in ('id', 'title', 'definition', 'intuitive_text', 'detailed_text',
                    'assumptions', 'common_mistakes', 'verification', 'variables', 'sources')} for card in cards]
    rows = (context or {}).get('candidates', [])
    selected = next((row for row in rows if row['id'] == context.get('selected_candidate_id')), {})
    has_slit_marker = ('first_zero_mm' in references and selected.get('parameters', {}).get('aperture') == 'single_slit')
    markers = ['slit_dark_fringe_left', 'slit_dark_fringe_right'] if has_slit_marker else []
    if figure:
        markers = [marker for marker in markers if marker == figure['id']]
    targets = {'highlight': markers, 'suggestion': []}
    if prediction and prediction['can_prepare']:
        targets['suggestion'].append('REVIEW_PREVIEW')
    if response.get('next_step') == 'REVIEW_SAVED_ACQUISITION':
        targets['suggestion'].append(response['next_step'])
    model_input = {'question': question, 'result_summary': context, 'cards': public_cards, 'depth': depth,
                   'question_kind': kind,
                   'reference_slots': {f'[[{key}]]': value for key, value in references.items()}, 'allowed_pointers': targets}
    prompt = common + ('以本轮检索到的知识卡为依据，保留定义和适用条件。正文可用自己的话讲解，来源由界面按引用卡片显示。'
                       '返回一个 JSON 对象。intent 是字符串，intro 是讲解正文，card_ids 是卡片标识数组。'
                       'highlight 和 suggestion 可省略；如填写，各给一个允许的标识字符串。'
                       '正文用两到四句直接回应困惑。按用户要求选用推导、类比或读图，跳过复述和重复报数。'
                       '【正文硬规则】intro 里不要写任何阿拉伯数字、等号或外链。确实需要数值或公式时，'
                       '把 reference_slots 中带双中括号的键逐字写进 intro，保持双中括号，程序负责代入。'
                       '公式引用同时列出来源卡。只讲解析预览或已保存结果，避免声称已经执行新操作。')
    if constant:
        prompt += f'本概念的固定写法可用 [[{constant[0]}]]，也允许逐字写成「{constant[1]}」。其他数字和公式沿用槽位。'
    if authority and authority['id'] == 'gaussian-waist':
        prompt += '回答束腰先讲最小的截面和怎样读图。若提到半径阈值，明确每个截面用各自的轴心强度。'
    if figure:
        model_input['selected_figure'] = figure
        prompt += '回答用户选中的图形对象，按当前候选解释其物理意义和读法。'
    if history and (basis == 'CONVERSATION' or comparison or topic == current_topic):
        included = {card['id'] for card in cards}
        relevant = [item for item in history if (item.get('topic') or {}).get('id') in included]
        if relevant:
            model_input['recent_lessons'] = relevant[-3:]
    if comparison:
        model_input['saved_result_comparison'] = comparison
        prompt += ('区分两个已经保存的结果。比较数值时，参考版使用 reference_ 开头的槽，当前版使用 current_ 开头的槽。'
                   '用参考版和当前版指代记录，除非用户指定显示顺序，否则不要假定两幅图的左右位置。')
        if single_side and comparison['entries'] and comparison['entries'][0]['metric'] == FIRST_MINIMUM:
            prompt += '本题比较中心到同侧第一暗纹的距离。先指出哪版距离更大，再用两版 first_zero_mm 槽给依据。'
        if comparison.get('causes') and explain_changes:
            prompt += ('用已核对的 causes 回应问题，effects 是每次只换一个条件的解析预测，'
                       'combined 是同时采用本版条件的解析结果。作用用倍率相乘，不把差值相加。'
                       '用户指定单个条件时先回答那个条件；要求直观时可用方向解释，要求公式时说明乘积。')
    if context and context['source']['kind'] == 'INVERSE_RESULT':
        prompt += ('按当前场景区分数据列解释、拟合点和留出点。留出点来自同一扫描。只有记录了新增组才描述新观测。'
                   '残差用于解释已选模型和数据。')
        if domain == 'film':
            prompt += '仅在当前搜索范围和数据条件下描述厚度候选。'
    if screen_comparison:
        model_input['ordered_screen_comparison'] = screen_comparison
        prompt += ('屏幕移动按 ordered_screen_comparison 的 start 到 end 叙述，半径也按同一顺序。'
                   '优先使用有序槽，不要从当前选中候选倒着讲。入射条件和透镜固定，解释为何读数变化而共同束腰保持。')
    prompt += '聚焦本题涉及的场景、量和任务，省略未被问到的其他光学场景。'
    if has_slit_marker:
        prompt += '图上的第一暗纹是解析标记。比较标记和数值曲线低谷的接近程度，实际采样点的位置可以偏离标记。'
    if 'rayleigh_radius_ratio' in references:
        prompt += '需要写根号二时，用 [[rayleigh_radius_ratio]]。'
    if kind == 'DERIVE':
        prompt += '解释至少一个从适用关系到结论的中间步骤，再指出用户问的量怎样变化。'
    if authority:
        model_input['registered_conclusion'] = response['conclusion']
        prompt += ('程序单独展示核对结论。正文解释原因或如何读图，遵守结论与当前条件。'
                   f'返回前核对 JSON 顶层必填键 intent、intro、card_ids、conclusion_id；'
                   f'conclusion_id 必须逐字写为 "{authority["id"]}"。这四项都要有。')
        if prediction or authority.get('slit_scaling'):
            prompt += ('固定其余条件时，波长或焦距各自增大会让单侧暗纹离中心更远，缝宽增大会使距离减小。'
                       '波长与焦距一增一减时，解释合并后的结果，以程序给出的变化为准。'
                       '本模型中的焦距是理想薄透镜焦距，观察面位于透镜后焦面。说明同一衍射角在该平面上的横向投影；不要用屏幕到缝片的间距替代。')
    if skip_formula:
        prompt += '按用户要求仅用直观解释，正文不使用公式引用。'
    if prediction:
        model_input['analytic_preview'] = {'metric': prediction['metric_label'], 'direction': prediction['direction'],
                                           'changes': [row['field'] for row in prediction['changes'] if row['state'] == 'CHANGE'],
                                           'solver_calls': 0}
        prompt += ('数值槽的 horizontal 指横向，vertical 指纵向，full_width 指两侧暗纹之间全宽，'
                   'first_zero 指从中心到单侧暗纹的位置。请按槽名说明含义。解析尺度预览只给暗纹位置和几何宽度；'
                   '峰值和强度变化应留待实际计算。')
    payload = json_payload(prompt, model_input)

    def checked_explanation(raw):
        explanation = _validated_explanation(raw, cards, authority, references, prediction=prediction, targets=targets)
        if context and knowledge_retrieval.adds_unasked_scene(explanation['intro'], question, context['domain']):
            raise ValueError('Explanation brings in an unasked optical scene')
        return explanation

    return _request_explanation(store, data, response, request_id, payload, checked_explanation)


def _request_explanation(store, data, response, request_id, payload, validate, *, general=False):
    receipt = store.owner.safe('tutor-receipts', request_id + '.json')
    request_hash = digest(data)
    with store.lock:
        if receipt.is_file():
            previous = json.loads(receipt.read_text(encoding='utf-8'))
            if previous.get('request_sha256') != request_hash:
                raise WorkflowError('该学习轮次已绑定另一请求。', 409)
            if 'response' in previous:
                return previous['response']
            return {**response, 'mode': 'LOCAL_REFERENCE_AFTER_AI_FAILURE', 'request_id': request_id,
                    'notice': '上次已进入发送步骤，回执尚未保存。此轮保留本地解释，可继续下一问。'}
        client = read_only_client(store, data)
        receipt.parent.mkdir(exist_ok=True)
        atomic_json(receipt, {'id': request_id, 'request_sha256': request_hash, 'status': 'DISPATCH_STARTED'})
    audit, raw_content = {}, None
    try:
        raw = client.complete(request_id, payload)
        audit = raw.get('audit', {})
        candidate_text = (raw.get('assistant') or {}).get('content')
        if isinstance(candidate_text, str) and len(candidate_text) <= 12000 and not wire.credential_risk(candidate_text):
            raw_content = candidate_text
        explanation = validate(raw)
        verified_live = audit.get('status') == 'ASSISTANT_RETURNED' and audit.get('live_dispatch') is True
        mode = 'LIVE_AI' if verified_live else 'INJECTED_AI'
        if general:
            response.update(explanation, mode=mode + '_GENERAL', result=None, source=None,
                            explanation_source='LLM_GENERAL_EXPLANATION',
                            notice='这段讲解来自 DeepSeek，暂无匹配的本地出处。')
        else:
            response.update(explanation, mode=mode, explanation_source='LLM_GROUNDED_EXPLANATION')
    except WorkflowError as exc:
        response['mode'] = 'LOCAL_REFERENCE_AFTER_AI_FAILURE'
        response['notice'] = str(exc) if general else str(exc) + ' 本轮已保留本地讲解。'
    except (ValueError, TypeError, KeyError, RuntimeError):
        response['mode'] = 'LOCAL_REFERENCE_AFTER_AI_FAILURE'
        response['notice'] = ('AI 本次未返回可显示的讲解，可以重新提问。' if general
                              else 'AI 本次回复未完成核对，以下为本地知识与已保存结果。')
    # A tutor receipt is separate from the immutable case and solver ledger.
    public_audit = {key: audit[key] for key in ('status', 'request_sha256', 'response_sha256', 'live_dispatch',
                                               'provider_model', 'usage', 'estimated_peak_cost_cny') if key in audit}
    response['request_id'] = request_id
    atomic_json(receipt, {'id': request_id, 'request_sha256': request_hash, 'response': response,
                          'audit': public_audit, 'raw_assistant_content': raw_content})
    return response
