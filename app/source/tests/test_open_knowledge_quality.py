"""Source corrections and conversational boundaries from live review."""
import pytest
import json
from types import SimpleNamespace
from photonguard.experiments.knowledge_catalog import catalog,tokens
from photonguard.experiments.knowledge_learning import select_articles
from photonguard.experiments.knowledge_math import calculate
from photonguard.experiments.knowledge_math import contextual_calculation
from photonguard.experiments.knowledge_retrieval import switches_topic
from photonguard.experiments.knowledge_context import validate
from photonguard.experiments.knowledge_retrieval import requested_definition

@pytest.mark.parametrize('state',['|+>','∣+⟩','| + 〉','|+⟩'])
def test_equivalent_typed_states_reach_same_evidence_and_probability(tmp_path,state):
    book=catalog(tmp_path);question=f'输入{state}沿Z基测量，不记录结果，状态怎样？'
    assert 'quantum-measurement-post-state' in select_articles(book,question)
    result=calculate(question)
    assert result['inputs']['state']=='+' and result['inputs']['basis']=='z'
    assert 'I/2' in result['result']

@pytest.mark.parametrize('question,changed',[
    ('换个内容，什么是光子？',True),
    ('不要继续量子问题了。什么是群折射率？',True),
    ('别再讨论这个话题了，聊光纤。',True),
    ('不要公式了，用日常例子讲刚才那一步。',False),
    ('先不讨论损耗了，只问同一根光纤的接受角。',False),
])
def test_explicit_topic_change_preserves_same_subject_style_updates(question,changed):
    assert switches_topic(question) is changed

def test_source_conditions_do_not_require_wavelength_sized_fiber(tmp_path):
    entry=catalog(tmp_path).entries['dielectric-waveguide']
    assert '横向尺寸必须与波长可比' not in ' '.join(entry['conditions'])
    assert '不要求横向尺寸必须与波长可比' in entry['body']
    assert any('ocw.mit.edu' in source['url'] for source in entry['sources'])

def test_discarding_measurement_record_is_not_undoing_measurement(tmp_path):
    entry=catalog(tmp_path).entries['quantum-measurement-post-state']
    assert '仅删除经典结果' in entry['body'] and '不会恢复' in entry['body']
    assert entry['sources']

def test_contextual_names_do_not_turn_ring_radius_into_gaussian_evidence(tmp_path):
    book=catalog(tmp_path)
    assert not book.named('刚才是半径250微米，不是周长。',['gaussian-size'],contextual=True)
    assert book.named('高斯光斑半径改成250微米。',['gaussian-size'],contextual=True)==['gaussian-size']

def test_ring_geometry_is_not_used_as_a_wavelength():
    assert calculate('环周长250 μm，群折射率3.8，频率FSR多少？') is None
    assert calculate('真空波长250 μm，对应频率多少？')['id']=='wavelength-frequency'

@pytest.mark.parametrize('center,gap,expected',[(1550,1,124.7835413),(1550,.08,9.9826833),(780,.08,39.4204416)])
def test_center_and_interval_preserve_si_dimensions(center,gap,expected):
    result=calculate(f'{center}纳米附近，{gap}纳米的波长间隔换成频率间隔。')
    inputs=result['inputs']
    assert result['id']=='wavelength-local-gap'
    assert inputs['slope_hz_per_m'] == pytest.approx(299792458/(center*1e-9)**2)
    assert inputs['slope_ghz_per_nm']*gap == pytest.approx(expected,rel=1e-7)
    assert '10⁻¹⁸' in result['result']

def test_definition_with_excluded_previous_topic_uses_new_subject(tmp_path):
    question='那我只问束腰是什么，不谈量子了。'
    assert requested_definition(question)=='束腰'
    assert 'gaussian-waist' in select_articles(catalog(tmp_path),question)

def test_sequential_quantum_measurement_cannot_reuse_original_pure_state():
    first=calculate('初态|+>，沿Z基测量，忽略结果。')
    assert calculate('接着沿X基测量呢？',first) is None
    assert calculate('不做前一步，直接沿X基测量。',first)['inputs']['basis']=='x'
    reset=calculate('重新准备|+>，取消Z基操作，直接做X基测量。',first)
    assert reset['inputs']['basis']=='x' and '+ 概率 1' in reset['result']

def test_alphabetic_device_names_keep_separate_conditions():
    first='A探测器2 pW/√Hz，白噪声带宽25 Hz。'
    second='B探测器3 pW/√Hz，带宽9 Hz，哪个小？'
    result=contextual_calculation(second,[first])
    assert [d['bandwidth_hz'] for d in result['inputs']]==[25,9]
    result=contextual_calculation('只把B带宽变成16 Hz，A不动。',[first,second])
    assert [d['bandwidth_hz'] for d in result['inputs']]==[25,16]

def test_context_resolution_requires_original_quotes_and_current_turn():
    turns=['第一片前为平行于轴的线偏振光，第二片60度。','把第二片转到30度，其他不变。']
    value={'resolved_question':'第一片前为平行于轴的线偏振光，第二片30度，透过率多少？',
           'evidence':[{'turn':1,'quote':turns[0]},{'turn':2,'quote':turns[1]}]}
    assert validate(value,turns)==value['resolved_question']
    with pytest.raises(ValueError):validate(value|{'resolved_question':'波长532nm，第二片30度，透过率多少？'},turns)
    with pytest.raises(ValueError):validate(value|{'evidence':[{'turn':1,'quote':turns[1]}]},turns)
    with pytest.raises(ValueError):validate(value|{'evidence':[value['evidence'][0]]},turns)

def test_negated_side_task_is_not_a_new_named_topic(tmp_path):
    book=catalog(tmp_path)
    assert not book.named('别做仪器校准，只说明需要什么统计假设。',['measurement-calibration'],contextual=True)

def test_adjacent_scripts_do_not_hide_an_unknown_acronym(tmp_path):
    assert tokens('CARS成像')==['cars','成像']
    book=catalog(tmp_path)
    assert 'nonlinear-polarization-expansion' in select_articles(book,'CARS成像为啥叫相干反斯托克斯拉曼？给新生讲。')
    assert 'forster-energy-transfer' in select_articles(book,'FRET效率怎么算？')

def test_review_can_remove_unsupported_citations_without_losing_answer(tmp_path):
    from photonguard.experiments.knowledge_learning import answer
    class Client:
        def preflight(self):pass
        def complete(self,ident,payload):
            body=json.loads(payload['messages'][1]['content'])
            if ident.startswith('knowledge-review-'):
                value={'intro':'这是通识解释，本轮材料只涉及一般概念，具体机制需要另外的来源。'}
            else:
                value={'sections':[{'heading':'讲解','text':'这里先给出一段可阅读的概念说明，随后核对实际支持的资料。',
                                     'references':[body['articles'][0]['id']]}]}
            return {'assistant':{'content':json.dumps(value)},'audit':{'live_dispatch':True}}
    result=answer(SimpleNamespace(owner=SimpleNamespace(),client=Client()),catalog(tmp_path),
                  {'question':'光纤为什么能导光？','mode':'live','approve_data':True})
    assert result['mode']=='LIVE_AI_GENERAL' and result['status']=='GENERAL'
    assert not result['references'] and not result['sections'][0]['references']
    assert result['answer_review']['accepted'] and result['model_calls']==2
