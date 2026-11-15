"""R1 acceptance cases for source-bound constraints and teaching conclusions."""

import json

import pytest

from photonguard.experiments import learning_followup, slit_goal, tutor
from photonguard.experiments.slit_formula import FULL_WIDTH


GOAL = '波长633 nm、焦距200 mm，中央亮区全宽10±0.2 mm。采用教学模型。'


def preview(suffix, **body):
    return slit_goal.prepare_goal({'text': GOAL + suffix, 'teaching': True, **body})


@pytest.mark.parametrize('suffix', [
    '只用这三块现成缝片（16、20、24 μm）。',
    '先用教学模型，但缝宽只能从16、20、24里面选，单位微米。',
    '仅用手上的缝片 0.016 mm、20 微米和 24000 nm。',
])
def test_available_catalog_preserves_every_quoted_width(suffix):
    plan = preview(suffix)
    assert plan['status'] == 'PREVIEW'
    assert plan['design']['restriction']['values_um'] == [16, 20, 24]
    assert plan['design']['closest_width_um'] == 24
    assert plan['design']['analytically_feasible'] is False


@pytest.mark.parametrize('suffix', ['手头只有这三种宽度，别建议其他尺寸。',
                                   '只用16、20、24 μm这些尺寸，别扩充。'])
def test_unresolved_constraint_gets_a_narrow_confirmation(suffix):
    result = preview(suffix)
    assert result['status'] == 'NEEDS_INFORMATION'
    assert len(result['questions']) == 1 and '缝宽' in result['questions'][0]
    assert result['intake']['values']['wavelength_nm'] == 633
    assert result['intake']['values']['focal_length_mm'] == 200


def test_example_list_does_not_override_a_continuous_range():
    result = preview('举例有三种缝宽16、20、24 μm。实际允许缝宽15—35 μm连续调整。')
    assert result['status'] == 'PREVIEW'
    assert result['design']['restriction']['kind'] == 'range'
    assert result['design']['closest_width_um'] == pytest.approx(25.32)


def test_confirmed_form_can_supply_only_the_ambiguous_catalog():
    result = preview('手头只有这三种宽度，别建议其他尺寸。', values={'catalog_um': [16, 20, 24]},
                     confirmed_fields=['catalog_um'])
    assert result['status'] == 'PREVIEW'
    assert result['design']['restriction']['values_um'] == [16, 20, 24]
    assert result['design']['sources']['catalog_um']['kind'] == 'USER_CORRECTION'


@pytest.fixture
def source_context():
    return {'domain': 'diffraction', 'source': {'kind': 'FORWARD_RESULT', 'record_id': '1' * 32,
            'result_sha256': 'a' * 64}, 'selected_candidate_id': 'candidate-01',
            'candidates': [{'id': 'candidate-01', 'checks_passed': True,
                'parameters': {'aperture': 'single_slit', 'wavelength_nm': 633, 'focal_length_mm': 200,
                               'slit_width_um': 25.32, 'slit_height_um': 100, 'grid_size': 256, 'window_mm': .4}}],
            'goal': {'metric': FULL_WIDTH}, 'change_policy': {'range_um': {'min': 15, 'max': 35}}}


def test_compound_wave_height_is_complete_and_axis_specific(source_context):
    result = learning_followup.predict('波长改成532 nm，同时把缝高改成120 μm，先预测。', source_context)
    assert result['can_prepare'] and result['direction'] == 'DECREASE'
    assert {item['field']: item['after'] for item in result['changes']} == {'wavelength_nm': 532, 'slit_height_um': 120}
    assert all(item['quote'] and item['state'] == 'CHANGE' for item in result['changes'])
    assert result['after_mm'] == pytest.approx(8.40442338)
    assert result['vertical']['after_mm'] < result['vertical']['before_mm']


def test_signed_intent_distinguishes_hold_negation_and_missing_change(source_context):
    assert learning_followup.predict('先别把波长改成532 nm；只解释当前结果。', source_context) is None
    assert learning_followup.predict('波长不变，先解释当前结果。', source_context) is None
    partial = learning_followup.predict('波长改成532 nm，同时改一下缝高。', source_context)
    assert not partial['can_prepare']
    assert any(item['field'] == 'slit_height_um' and item['state'] == 'REVIEW' for item in partial['changes'])
    full = learning_followup.predict('波长改成0.532 μm，焦距用150 mm，缝高不变。', source_context)
    assert full['can_prepare']
    assert full['after_mm'] == pytest.approx(8.40442338 * .75)
    assert any(item['state'] == 'KEEP' and item['field'] == 'slit_height_um' for item in full['changes'])


def test_height_only_changes_vertical_extent(source_context):
    result = learning_followup.predict('保持波长和缝宽，只把缝高改为120 μm。', source_context)
    assert result['can_prepare'] and result['direction'] == 'UNCHANGED'
    assert result['after_mm'] == pytest.approx(10)
    assert result['vertical']['after_mm'] < result['vertical']['before_mm']


def test_explicit_starting_value_must_match_the_selected_record(source_context):
    result = learning_followup.predict('波长由650 nm调到610 nm，缝高设为125 μm。', source_context)
    assert not result['can_prepare'] and '波长' in result['issue']
    assert {row['field']: row['state'] for row in result['changes']} == {
        'wavelength_nm': 'REVIEW', 'slit_height_um': 'CHANGE'}


def test_registered_prediction_rejects_opposite_model_sentence(source_context):
    prediction = learning_followup.predict('用532 nm会怎样？', source_context)
    authority = tutor.registered_conclusion('用532 nm会怎样？', source_context, prediction)
    model = {'assistant': {'content': json.dumps({'intent': 'PREDICT_CHANGE',
             'conclusion_id': authority['id'],
             'intro': '波长缩短后，两侧第一暗纹离中心更远，中央亮区会变宽。',
             'card_ids': ['slit-scale']})}}
    with pytest.raises(ValueError, match='conflicts with the saved evidence'):
        tutor._validated_explanation(model, tutor.search_cards('波长变化', 'diffraction'), authority, prediction=prediction)
    assert '减小' in authority['text'] and '向中心靠近' in authority['styles']['mechanism']


def test_model_can_cite_bound_values_in_free_explanation():
    model = {'assistant': {'content': json.dumps({'intent': 'EXPLAIN_RESULT',
             'intro': '本候选宽度为 [[candidate_value]]，可与本轮目标比较。',
             'card_ids': ['slit-width-definitions']})}}
    cards = tutor.search_cards('全宽怎么算', 'diffraction')
    explanation = tutor._validated_explanation(model, cards, references={'candidate_value': '10 mm'})
    assert '10 mm' in explanation['intro']
    with pytest.raises(ValueError):
        tutor._validated_explanation(model, cards, references={})


def test_gaussian_rejects_wrong_reference_in_generated_explanation():
    question = '高斯束的光斑半径相对什么强度？'
    authority = tutor.registered_conclusion(question, None, None)
    obj = {'intent': 'EXPLAIN_CONCEPT', 'conclusion_id': authority['id'], 'card_ids': ['gaussian-section-radius'],
           'intro': '请以入射面的光强作为屏幕半径的判定基准。'}
    cards = tutor.search_cards(question, 'gaussian')
    with pytest.raises(ValueError, match='conflicts with the saved evidence'):
        tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, authority)


def test_width_slots_distinguish_axis_full_width_and_first_zero(source_context):
    question = '把波长改为532 nm，看看横向第一暗纹。'
    prediction = learning_followup.predict(question, source_context)
    cards = tutor.search_cards(question, 'diffraction')
    authority = tutor.registered_conclusion(question, source_context, prediction)
    refs = tutor._bound_references(source_context, prediction, cards, include_vertical=True)
    assert refs['horizontal_first_zero_after'] == '4.20221 mm'
    assert refs['vertical_full_width_after'] == '2.128 mm'
    obj = {'intent': 'PREDICT_CHANGE', 'conclusion_id': authority['id'], 'card_ids': ['slit-scale'],
           'intro': '单侧第一暗纹从 [[horizontal_first_zero_before]] 变到 [[vertical_full_width_after]]。'}
    with pytest.raises(ValueError, match='conflicts with the saved evidence'):
        tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, authority, refs, prediction=prediction)
    obj['intro'] = '单侧第一暗纹从 [[horizontal_first_zero_before]] 变到 [[horizontal_first_zero_after]]。'
    validated = tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, authority, refs, prediction=prediction)
    assert '4.20221 mm' in validated['intro']
    obj['intro'] += '纵向也按相同比例变化，单侧暗纹由 [[vertical_first_zero_before]] 到 [[vertical_first_zero_after]]。'
    validated = tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, authority, refs, prediction=prediction)
    assert '1.064 mm' in validated['intro']


def test_reference_slot_cannot_assign_the_current_wavelength_to_the_previous_version():
    refs = {'current_wavelength_nm': '532 nm', 'reference_wavelength_nm': '633 nm'}
    obj = {'intent': 'EXPLAIN_RESULT', 'card_ids': ['slit-scale'], 'intro': '参考版使用 [[current_wavelength_nm]]。'}
    cards = tutor.search_cards('波长变化', 'diffraction')
    with pytest.raises(ValueError, match='conflicts with the saved evidence'):
        tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, references=refs)
    obj['intro'] = '参考版使用 [[reference_wavelength_nm]]，当前版使用 [[current_wavelength_nm]]。'
    obj['suggestion'] = ''
    result = tutor._validated_explanation({'assistant': {'content': json.dumps(obj)}}, cards, references=refs)
    assert result['intro'] == '参考版使用 633 nm，当前版使用 532 nm。'
    assert 'suggestion' not in result


def test_slit_comparison_checks_separate_trends_without_guessing_the_combined_change():
    authority = {'id': 'saved_result_comparison', 'slit_scaling': True}
    assert tutor._conflicts_with_evidence('波长变长或焦距变短都会把它推远。', authority, None)
    assert not tutor._conflicts_with_evidence('波长变长或焦距变长都会把它推远。', authority, None)
    assert not tutor._conflicts_with_evidence('波长变长，焦距缩短，两项变化叠加后，暗纹离中心更远。', authority, None)
    assert tutor._conflicts_with_evidence('焦距变大，屏幕离缝更远，同一衍射角的横向距离变大。', authority, None)
    assert not tutor._conflicts_with_evidence('同一衍射角在透镜焦平面上的横向投影随焦距增大。', authority, None)


def test_gaussian_definition_names_current_section_center():
    definition = tutor.registered_conclusion('高斯束 1/e² 光斑半径相对什么强度？', None, None)
    assert '同一横截面' in definition['text'] and '中心强度' in definition['text']
    assert '入射强度' not in definition['text']


@pytest.mark.parametrize('claim,conflict', [
    ('焦距缩短会让暗纹离中心更远', True), ('缩短焦距会让第一暗纹离中心更远', True),
    ('焦距缩短会让暗纹离中心更近', False), ('缩短焦距会让第一暗纹离中心更近', False),
    ('缩短焦距不会让第一暗纹离中心更远', False), ('缩短焦距不会让第一暗纹离中心更近', True),
    ('加长波长会使暗纹离中心更远', False), ('减小波长会使暗纹离中心更远', True),
    ('缝宽增大会使暗纹离中心更近', False), ('增大缝宽会使暗纹离中心更远', True),
])
def test_slit_direction_receives_both_word_orders_and_negation(claim, conflict):
    authority = {'id':'saved_result_comparison','slit_scaling':True,'styles':{},'text':'程序结论'}
    cards = [card for card in tutor._knowledge() if card['id']=='slit-scale']
    obj = {'intent':'EXPLAIN_RESULT','conclusion_id':authority['id'],'card_ids':['slit-scale'],
           'intro':f'固定其他条件，{claim}。'}
    reply = {'assistant':{'content':json.dumps(obj)}}
    if conflict:
        with pytest.raises(ValueError,match='conflicts with the saved evidence'):
            tutor._validated_explanation(reply,cards,authority)
    else:
        assert tutor._validated_explanation(reply,cards,authority)['intro']==obj['intro']


@pytest.mark.parametrize('start,end', [(85,140),(140,85)])
def test_screen_comparison_binds_to_requested_order(start,end):
    fixed = {'wavelength_nm':633,'focal_length_mm':130,'waist_radius_um':500,'waist_z_mm':0}
    rows = [{'id':str(pos),'status':'RUN_COMPLETED','parameters':fixed|{'screen_distance_mm':pos},
             'metrics':{'screen_radius_um':radius,'new_waist_z_mm':128.588}}
            for pos,radius in [(140,68.280),(85,176.434)]]
    context = {'domain':'gaussian','source':{'kind':'FORWARD_RESULT'},'selected_candidate_id':'140','candidates':rows}
    question = f'我把屏幕从{start} mm移到{end} mm，是不是束腰也跟着移动？'
    authority = tutor.registered_conclusion(question,context,None)
    assert f'从 {start} mm 移到 {end} mm' in authority['text']
    assert [authority['screen_comparison'][key]['candidate_id'] for key in ('start','end')]==[str(start),str(end)]
    slots = {'screen_start_position':f'{start} mm','screen_end_position':f'{end} mm'}
    cards = [card for card in tutor._knowledge() if card['id']=='gaussian-waist-screen']
    obj = {'intent':'EXPLAIN_RESULT','conclusion_id':authority['id'],'card_ids':['gaussian-waist-screen'],
           'intro':'从 [[screen_end_position]] 移到 [[screen_start_position]]，光斑截面会变。'}
    with pytest.raises(ValueError,match='conflicts with the saved evidence'):
        tutor._validated_explanation({'assistant':{'content':json.dumps(obj)}},cards,authority,slots)
    obj['intro'] = '从 [[screen_start_position]] 移到 [[screen_end_position]]，光斑截面会变。'
    assert f'从 {start} mm 移到 {end} mm' in tutor._validated_explanation({'assistant':{'content':json.dumps(obj)}},cards,authority,slots)['intro']
