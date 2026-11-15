"""A short learning record derived from the same checked result as the tutor."""

from html import escape

from .learning_lessons import goal_reflection, state as lesson_state, study_history
from .tutor import _focused_facts, _grounded_facts, read_result

_CONDITION_LABELS = {
    'wavelength_nm': ('波长', 'nm'), 'focal_length_mm': ('焦距', 'mm'),
    'slit_width_um': ('缝宽', 'μm'), 'slit_height_um': ('缝高', 'μm'),
    'slit_separation_um': ('双缝中心距', 'μm'), 'relative_phase_rad': ('相对相位', 'rad'),
    'waist_radius_um': ('入射束腰半径', 'μm'), 'waist_z_mm': ('入射束腰位置', 'mm'),
    'screen_distance_mm': ('屏幕位置', 'mm'), 'film_index': ('膜层折射率', ''),
    'substrate_index': ('基底折射率', ''), 'incident_index': ('入射介质折射率', ''),
    'layer_count': ('膜层数', ''), 'design_wavelength_um': ('设计波长', 'μm'),
}


def _conditions(parameters):
    return [f'{label} {parameters[key]:.7g} {unit}'.strip() for key, (label, unit) in _CONDITION_LABELS.items()
            if key in parameters]


def _study_notes(store, case):
    lines = []
    for version, notes in study_history(store, case):
        ident = version['id']
        if hypothesis := notes.get('hypothesis'):
            lines.append(f"- 用户对下一轮的猜想（来源 `{ident}`） · {escape(' '.join(hypothesis.split()))}")
        if observation := notes.get('reflection'):
            lines.append(f"- 用户观察（`{ident}`） · {escape(' '.join(observation.split()))}")
        comparison = version.get('slit_followup') or {}
        if verified := comparison.get('verification'):
            question = escape(' '.join(verified['question'].split()))
            lines.append(f"- 验证问题（`{ident}`） · {question}")
            fixed = _conditions(comparison.get('fixed', {}))
            if fixed:
                lines.append('  - 保持 · ' + ' · '.join(fixed))
            lines.append(f"  - 程序解析预测 · {comparison['metric_label']} "
                         f"{comparison['before_mm']:.7g} → {comparison['after_mm']:.7g} mm。")
            result = version.get('result') or {}
            if winner := next((row for row in result.get('candidates', []) if row['id'] == result.get('winner_id')
                               and row['all_checks_passed'] is True), None):
                value = winner['goal']['value']
                lines.append(f"  - 已批准的数值执行 · 保存的 FFT 场配有解析指标 {value:.7g} mm。")
                if comparison['direction'] == 'UNCHANGED':
                    lines.append('  - 结论 · 固定缝宽且波长与焦距的乘积保持时，理想焦平面的横向暗纹距离保持。')
                elif comparison['verification']['strategy'] == 'CHANGE_WAVELENGTH_ONLY':
                    lines.append('  - 结论 · 保持焦距与开口尺寸时，较短波长让第一暗纹更靠近中心。')
    return lines


def markdown(store, case):
    context = read_result(store, case['id'])
    source = context['source']
    bounded = case.get('bounded_goal') or {}
    original = str(bounded.get('input_text') or case.get('task_goal_text') or case.get('text') or case.get('title') or '查看这次光学实验。')
    title = escape(str(case.get('title') or '光学实验'))
    lines = [f'# 学习记录 · {title}', '', f"实验 `{source['record_id']}` · 结果 `{source['result_sha256']}`", '',
             '## 这次想弄清什么', '', escape(original).replace('\n', ' '), '']
    lesson = lesson_state(store, case)
    if lesson:
        lines.extend(['## 教学任务', '', lesson['title'], '', lesson['observe'], ''])
        if prediction := lesson['saved_prediction']:
            lines.extend([f"课前预测（记录 `{prediction['record_id']}`） · {escape(prediction['text'])}", ''])
    reflection = lesson['reflection'] if lesson else (goal_reflection(store, case) or {}).get('text')
    study = _study_notes(store, case)
    if reflection and not study:
        lines.extend(['本版观察 · ' + escape(' '.join(reflection.split())), ''])
    if study:
        lines.extend(['## 这几次怎样验证', '', *study, ''])
    selected = next((row for row in context.get('candidates', []) if row['id'] == context.get('selected_candidate_id')), {})
    facts = context.get('facts', {})
    conditions = _conditions(selected.get('parameters') or facts.get('fixed', {}))
    conditions.append('来源为教学条件或教学合成数据。' if context.get('context_kind') == 'teaching'
                      or facts.get('data_kind') == 'TEACHING_SYNTHETIC' else '条件与数据来源见本版任务记录。')
    lines.extend(['## 核对的条件', '', ' · '.join(conditions), ''])

    comparison = case.get('slit_followup') or case.get('learning_comparison') or {}
    changes = comparison.get('changes') or ([comparison['change']] if comparison.get('change') else [])
    edits = [f"{row['label']}由 {row['before']:.7g} 变为 {row['after']:.7g} {row['unit']}"
             for row in changes if row.get('state', 'CHANGE') == 'CHANGE']
    if facts.get('acquisition_comparison'):
        extra = facts['acquisition_comparison']['added_wavelengths_nm']
        edits.append('加入 ' + '、'.join(f'{value:g}' for value in extra) + ' nm 的后续观测。')
    if bounded:
        prior = bounded['source']
        edits.append(f"沿用来源 `{prior['record_id']}` 的 `{prior['candidate_id']}`，本轮批准 {len(bounded['chosen_ids'])} 组受限候选。")
        lines.extend(['## 预览和实算', '', escape(bounded['method']), '',
                      '来源约束、所选组合与结果保存在本版计划和完整证据中。', ''])
        if target := bounded.get('target_reference'):
            goal = bounded['goal']
            lines.extend([f"恢复目标取自 `{target['record_id']}` 的 `{target['candidate_id']}`。"
                          f"目标 {goal['target']:.7g} ± {goal['tolerance']:.7g} {goal['unit']}。", ''])
    lines.extend(['## 这版实际改变了什么', '', ' · '.join(edits) if edits else '这是本版用于比较的已保存条件。', ''])
    evidence = _grounded_facts(context)
    lines.extend(['## 看到了什么', '', *('- ' + line for line in _focused_facts(context, evidence, '', 'result')), ''])
    domain = context['domain']
    if domain == 'diffraction' and selected.get('parameters', {}).get('aperture') == 'single_slit':
        principle = '理想焦平面第一暗纹距离为 λf/a。横向使用缝宽，纵向使用缝高。二维图和中心曲线取自保存的 FFT。'
        next_step = '保持其余条件，拖动一个参数预览暗纹，再核对并批准新计划。'
    elif domain == 'diffraction':
        principle = '理想双缝中，单条缝宽控制包络，中心距影响条纹周期，相对相位影响中心亮暗。'
        next_step = '用同一光强标尺比较两版曲线，先找相邻条纹，再观察外层包络。'
    elif domain == 'gaussian':
        principle = '光斑半径在当前截面按轴心强度的 1/e² 定义。束腰是半径沿传播方向最小的位置。'
        next_step = '固定透镜候选，换一个屏幕位置并对照屏幕半径和新束腰。'
    else:
        principle = '不同厚度可在同一波长产生相近反射率。新波长为联合分析增加另一份约束。'
        next_step = '对照允许的补测波长、实际观测来源以及前后保存的厚度候选。'
    lines.extend(['## 用到的原理', '', principle, '', '## 下一步可怎样验证', '', next_step, ''])
    return '\n'.join(lines)
