"""Drive the continuous learning journeys through the production HTTP routes."""

import argparse
import io
import json
import math
from pathlib import Path
import subprocess
import threading
import uuid
import zipfile

from photonguard.workflow import atomic_json, make_server
from validation.workbench_client import FILM, Workbench, fit_observations, forward_summary

TARGET = ('波长633 nm、焦距200 mm，中央亮区全宽10±0.2 mm。'
          '采用教学模型。')
RANGE = TARGET + '允许缝宽15—35 μm连续调整。'
INVENTORY = TARGET + '只用这三块现成缝片（16、20、24 μm）。'
COMPOUND = '波长改成532 nm，同时把缝高改成120 μm，先预测。'


def question(api, text, case=None, **fields):
    binding = {}
    if case is not None:
        binding = {'record_id': case['id'], 'result_sha256': case['experiment_record']['result_sha256']}
    return api.api('/learning/ask', {'question': text, **binding, **fields})


def slit_plan(api, text):
    intake = api.api('/learning/slit/inspect', {'text': text, 'mode': 'local'})
    body = {'text': text, 'teaching': True, 'values': intake['values'], 'excerpts': intake['quotes']}
    preview = api.api('/learning/slit/preview', body)
    assert preview['status'] == 'PREVIEW', preview['questions']
    return api.api('/learning/slit/plan', {**body, 'preview_sha256': preview['preview_sha256']})['case']


def prepare(api, text, prediction):
    return api.api('/learning/followup', {'question': text, **prediction['source'],
                   'candidate_id': prediction['candidate_id'], 'prediction_sha256': prediction['sha256'],
                   'confirm_change': True})


class ContraryTutor:
    def preflight(self):
        pass

    def status(self):
        return {'live_enabled': True, 'mode': 'TEST_INJECTION', 'model': 'fixture'}

    def complete(self, _ident, _payload):
        return {'audit': {'status': 'ASSISTANT_RETURNED', 'live_dispatch': False}, 'assistant': {
            'content': json.dumps({'intent': 'PREDICT_CHANGE', 'conclusion_id': 'slit_registered_change', 'card_ids': ['slit-scale'],
                       'intro': '波长缩短后，两侧第一暗纹离中心更远，中央亮区会变宽。'}, ensure_ascii=False)}}


def semantic_journey(server, api, out, log):
    catalog = api.api('/learning/slit/preview', {'text': INVENTORY, 'teaching': True})
    assert catalog['design']['restriction']['values_um'] == [16, 20, 24]
    assert not catalog['design']['analytically_feasible']
    unknown = api.api('/learning/slit/preview', {'text': TARGET + '手头只有这三种宽度，别建议其他尺寸。',
                                                 'teaching': True})
    assert unknown['status'] == 'NEEDS_INFORMATION' and len(unknown['questions']) == 1
    log['catalog'] = {'input': INVENTORY, 'design': catalog['design'], 'narrow_confirmation': unknown['questions']}

    source = api.solve(slit_plan(api, RANGE))
    before = source['experiment_record']['result_sha256']
    log['source'] = forward_summary(source)
    negative = question(api, '先别把波长改成532 nm；只解释当前结果。', source)
    assert negative['prediction'] is None
    incomplete = question(api, '波长改成532 nm，同时改一下缝高。', source)
    assert not incomplete['prediction']['can_prepare']
    log['intent_checks'] = {'negative': negative['question'], 'negative_prediction': negative['prediction'],
                            'partial': incomplete['prediction']}

    server.store.product_client = ContraryTutor()
    try:
        rejected = question(api, COMPOUND, source, mode='live', approve_data=True)
    finally:
        server.store.product_client = None
    assert rejected['mode'] == 'LOCAL_REFERENCE_AFTER_AI_FAILURE'
    assert '减小' in rejected['conclusion']['text']
    log['injected_contradiction'] = rejected
    assert '同一横截面' in question(api, '高斯束的1/e²半径参照什么强度？')['conclusion']['text']

    prediction = question(api, COMPOUND, source)['prediction']
    assert prediction['fft_calls'] == 0 and prediction['can_prepare']
    assert {row['field']: row['after'] for row in prediction['changes'] if row['state'] == 'CHANGE'} == {
        'wavelength_nm': 532, 'slit_height_um': 120}
    prepared = prepare(api, COMPOUND, prediction)['case']
    assert prepared['status'] == 'PLANNED' and prepared['result'] is None
    assert prepare(api, COMPOUND, prediction)['reused_draft']
    completed = api.solve(prepared)
    new = completed['plan']['candidates'][0]['parameters']
    assert new['wavelength_nm'] == 532 and new['slit_height_um'] == 120
    assert api.api('/cases/' + source['id'])['experiment_record']['result_sha256'] == before
    log['comparison'] = forward_summary(completed)
    (out / 'compound-family.zip').write_bytes(api.api('/cases/' + completed['id'] + '/family-export'))
    return source


class Lesson:
    def __init__(self, api, case=None):
        self.api, self.case = api, case
        self.ident, self.last = uuid.uuid4().hex, None
        self.turns = []

    def ask(self, text, **fields):
        answer = question(self.api, text, self.case, session_id=self.ident, turn_id=uuid.uuid4().hex,
                          expected_turn_id=self.last, **fields)
        self.last = answer['session']['last_turn_id']
        self.turns.append({key: answer[key] for key in ('question', 'mode', 'intro', 'source', 'depth',
                          'view', 'focused_facts', 'solver_calls', 'conclusion', 'revisited_turn_id') if key in answer})
        return answer


def controls_preview(api, case, changes, candidate=None):
    binding = {'record_id': case['id'], 'result_sha256': case['experiment_record']['result_sha256'],
               **({'candidate_id': candidate} if candidate else {})}
    body = {**binding, 'changes': changes}
    prediction = api.api('/learning/followup/preview', body)['prediction']
    assert prediction['can_prepare'] and prediction['fft_calls'] == 0
    return body, prediction


def approve_controls(api, body, prediction):
    draft = api.api('/learning/followup', {**body, 'prediction_sha256': prediction['sha256'],
                    'confirm_change': True})['case']
    assert draft['status'] == 'PLANNED' and draft['result'] is None
    return api.solve(draft)


def export_record(api, case, out, name):
    content = api.api('/cases/' + case['id'] + '/family-export')
    (out / (name + '.zip')).write_bytes(content)
    with zipfile.ZipFile(io.BytesIO(content)) as family:
        summary = family.read(case['id'] + '/LEARNING_RECORD.md').decode('utf-8')
        assert '这次想弄清什么' in summary and '这版实际改变了什么' in summary
    (out / (name + '-learning.md')).write_text(summary, encoding='utf-8')


def learning_journeys(server, api, out, evidence):
    semantic = {}
    source = semantic_journey(server, api, out, semantic)
    evidence['A2_compound_and_rejection'] = semantic

    fixed = api.solve(slit_plan(api, INVENTORY))
    fixed_lesson = Lesson(api, fixed)
    first = fixed_lesson.ask('三块缝片为什么都没达到全宽目标？')
    second = fixed_lesson.ask('没懂，用这版最接近的参数说。')
    assert first['source']['result_sha256'] == second['source']['result_sha256']
    assert not any(row['goal']['met'] for row in fixed['result']['candidates'])
    evidence['A1_catalog_limit'] = {'calculation': forward_summary(fixed), 'turns': fixed_lesson.turns}
    export_record(api, fixed, out, 'inventory')

    target = {**fixed['plan']['goal'], 'tolerance': .6}
    scored = api.action(fixed, 'revise_goal', goal=target)
    assert scored['result']['reuse']['new_solver_calls'] == 0
    assert scored['result']['candidates'][-1]['data'] == fixed['result']['candidates'][-1]['data']
    evidence['A3_tolerance_reuses_arrays'] = {'result': forward_summary(scored), 'reuse': scored['result']['reuse']}

    height_source = api.solve(slit_plan(api, TARGET + '缝宽固定25.32 μm。'))
    height_lesson = Lesson(api, height_source)
    height_lesson.ask('缝高为什么影响纵向图样？')
    ref = height_lesson.ask('用图解释这条曲线。', view='peak')
    assert ref['view'] == 'peak' and ref['result']['axes']['normalization'] == 'I / I_max'
    body, preview = controls_preview(api, height_source, {'slit_height_um': 120})
    assert preview['direction'] == 'UNCHANGED' and preview['vertical']['after_mm'] < preview['vertical']['before_mm']
    height = approve_controls(api, body, preview)
    evidence['A4_height_controls'] = {'before': forward_summary(height_source), 'preview': preview,
                                       'after': forward_summary(height), 'turns': height_lesson.turns}
    export_record(api, height, out, 'height-contrast')

    film = fit_observations(api, api.api('/inverse', {'model': FILM, 'teaching': True}))
    film = api.action(film, 'inverse_measurement_plan', constraints={'wavelengths_nm': [660, 780, 850]})
    film_lesson = Lesson(api, film)
    reply = film_lesson.ask('为什么同一个反射率会有多个膜厚？')
    assert reply['result']['facts']['retained_modes'] == len(film['inverse_result']['modes'])
    reply = film_lesson.ask('没懂，补测怎么增加信息？')
    assert reply['next_step'] == 'REVIEW_SAVED_ACQUISITION'
    plan = film['measurement_plan']
    child = api.action(film, 'inverse_append', teaching=True, approve=True, proposal_id=plan['recommended_id'],
                       proposal_sha256=plan['sha256'])
    joint = fit_observations(api, child)
    evidence['B1_film_acquisition'] = {'turns': film_lesson.turns, 'plan': plan,
        'before_count': len(film['inverse_result']['modes']), 'after_count': len(joint['inverse_result']['modes']),
        'before_id': film['id'], 'after_id': joint['id'], 'source': 'TEACHING_SYNTHETIC'}
    export_record(api, joint, out, 'film-acquisition')

    joint_lesson = Lesson(api, joint)
    joined = joint_lesson.ask('补测后保留的厚度候选是什么意思？')
    details = joint_lesson.ask('用刚才那组参数解释。')
    assert joined['result']['facts']['acquisition_comparison']['source']['record_id'] == film['id']
    assert details['result']['facts']['retained_modes'] == len(joint['inverse_result']['modes'])
    evidence['B2_explain_joint_result'] = {'turns': joint_lesson.turns, 'facts': details['result']['facts']}

    gaussian = api.solve(api.action(api.api('/cases', {'kind': 'teaching', 'template_id': 'gaussian-catalog'}),
                                   'plan', mode='rules'))
    gauss_lesson = Lesson(api, gaussian)
    radius = gauss_lesson.ask('光斑半径相对哪个强度？哪片镜头的屏幕光斑最小？')
    assert '同一横截面' in radius['conclusion']['text']
    focused = gauss_lesson.ask('用刚才那组参数解释。')
    assert any('新束腰' in line for line in focused['focused_facts'])
    evidence['C1_gaussian_waist_and_screen'] = {'run': forward_summary(gaussian), 'turns': gauss_lesson.turns}

    body, p = controls_preview(api, gaussian, {'screen_distance_mm': 140})
    new_screen = approve_controls(api, body, p)
    old = next(row for row in gaussian['result']['candidates'] if row['id'] == p['candidate_id'])
    new = new_screen['result']['candidates'][0]
    assert old['metrics']['new_waist_z_mm'] == new['metrics']['new_waist_z_mm']
    assert old['metrics']['screen_radius_um'] != new['metrics']['screen_radius_um']
    assert new_screen['exploration_root'] == gaussian['id']
    evidence['C2_new_screen'] = {'preview': p, 'after': forward_summary(new_screen)}
    export_record(api, new_screen, out, 'gaussian-screen')

    double = api.solve(api.action(api.api('/cases', {'kind': 'teaching', 'template_id': 'diffraction-phase'}),
                                 'plan', mode='rules'))
    body, w = controls_preview(api, double, {'slit_width_um': 24}, 'candidate-01')
    after_width = approve_controls(api, body, w)
    spacing = api.api('/library/create', {'case_id': 'D02', 'mode': 'teaching'})
    spacing = api.solve(spacing['case'] if 'case' in spacing else spacing)
    dual_lesson = Lesson(api, after_width)
    dual_lesson.ask('双缝的缝宽、条纹周期和相对相位分别改变什么？')
    assert after_width['result']['candidates'][0]['metrics']['fringe_period_mm'] == double['result']['candidates'][0]['metrics']['fringe_period_mm']
    assert len({row['metrics']['fringe_period_mm'] for row in spacing['result']['candidates']}) > 1
    evidence['D1_double_width_spacing_phase'] = {'phase': forward_summary(double), 'width': forward_summary(after_width),
                                                   'spacing': forward_summary(spacing), 'turns': dual_lesson.turns}
    export_record(api, after_width, out, 'double-width-and-phase')
    export_record(api, spacing, out, 'double-spacing')

    ray = Lesson(api)
    ray.ask('什么是瑞利长度？')
    ray.ask('没懂，换一个例子。')
    formula = ray.ask('这里为什么是平方？')
    assert 'πw₀²/λ' in formula['intro']
    assert ray.ask('回到上一个问题。')['mode'] == 'SAVED_EXPLANATION'
    evidence['O1_theory_rayleigh_followups'] = {'turns': ray.turns}

    concepts = Lesson(api)
    first = concepts.ask('单缝全宽和单侧第一暗纹距离怎么区分？')
    simpler = concepts.ask('再讲简单一点。')
    assert first['cards'][0]['id'] == simpler['cards'][0]['id']
    assert first['source'] is None and simpler['solver_calls'] == 0
    evidence['O2_theory_width_followups'] = {'turns': concepts.turns}

    study = Lesson(api, source)
    last = study.ask('这版的宽度怎么从曲线理解？')
    rejected = None
    try:
        question(api, '用刚才那组参数解释。', source, session_id=study.ident, turn_id=uuid.uuid4().hex,
                 expected_turn_id='0' * 32)
    except RuntimeError as exc:
        assert 'HTTP 409' in str(exc)
        rejected = str(exc)
    assert rejected
    restored = api.api('/learning/session/' + study.ident)
    assert restored['last_turn_id'] == last['session']['last_turn_id']
    current = study.ask('用刚才那组参数解释。')
    assert current['result']['selected_candidate_id'] == last['result']['selected_candidate_id']
    evidence['X1_stale_turn_recovery'] = {'rejected': rejected, 'readback': restored, 'turns': study.turns}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stage', choices=('r1', 'all'), default='all')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    log = {'status': 'STARTED', 'mode': 'LOCAL_HTTP_WITH_ONE_INJECTED_REPLY', 'provider_requests': 0,
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
           'worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain']))}
    try:
        with make_server(out / 'workspace', port=0, allow_workers=True) as server:
            loop = threading.Thread(target=server.serve_forever, daemon=True)
            loop.start()
            try:
                api = Workbench(server)
                if args.stage == 'r1':
                    semantic_journey(server, api, out, log)
                else:
                    log['learning_journeys'] = {}
                    learning_journeys(server, api, out, log['learning_journeys'])
                    assert len(log['learning_journeys']) == 12
                log['status'] = 'PASS'
            finally:
                server.shutdown()
                loop.join()
    except Exception as exc:
        log.update(status='FAIL', failure_type=type(exc).__name__, failure=str(exc))
        raise
    finally:
        atomic_json(out / 'journeys.json', log)
        print(json.dumps({key: log[key] for key in ('status', 'mode', 'provider_requests', 'source_commit')},
                         ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
