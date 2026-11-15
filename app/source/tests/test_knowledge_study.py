"""Regression checks for learning, source corrections and formula answers."""
import json
from threading import Thread
from types import SimpleNamespace

import numpy as np
import pytest

from photonguard.experiments.knowledge_catalog import catalog
from photonguard.experiments.knowledge_learning import answer
from photonguard.experiments.knowledge_math import calculate
from photonguard.experiments.knowledge_experiment import _slit_input
from photonguard.experiments.slit_goal import prepare_goal
from photonguard.experiments.knowledge_server import KnowledgeHandler
from photonguard.workflow import make_server
from validation.workbench_client import Workbench


def test_foundation_packs_and_topical_sources(tmp_path):
    book = catalog(tmp_path)
    counts = {p['id']:p['published'] for p in book.overview()['packs']}
    assert len(book.entries) >= 1000
    assert [counts[i] for i in ('geometrical-imaging','fourier-imaging',
                               'laser-resonators','polarization-coherence')] == [60,61,53,35]
    assert len(book.study_topics) >= 30
    for entry in book.entries.values():
        assert entry['body'] and entry['sources']
        assert len({(s['url'],s['section']) for s in entry['sources']}) == len(entry['sources'])
    for ident, marker in (('fiber-mfd-far-field','Scan apparatus'),('camera-temporal-spatial-noise','(18)'),
                          ('detector-specific-detectivity','Equation 5'),('group-index','Table 2.4')):
        article = book.article(ident)
        assert marker in article['sources'][0]['section']
        assert any(claim['source_indices'] for claim in article['claims'])
    assert book.article('quantum-measurement-post-state')['topic'] == 'quantum'
    assert book.article('second-harmonic')['topic'] == 'nonlinear'
    assert [counts[i] for i in ('ultrafast-pulse-learning','nonlinear-coupled-waves',
                               'guided-photonics-connections')] == [45,67,35]
    assert 'κ²/g²' in book.article('chip-coupler-phase-mismatch')['formula']


def test_two_nearby_camera_articles_use_same_variance_and_units(tmp_path):
    book = catalog(tmp_path)
    pair, spatial = (book.article(ident) for ident in
                     ('camera-temporal-noise-pair','camera-temporal-spatial-noise'))
    assert pair['formula'] == spatial['formula']
    assert pair['variables'] == spatial['variables']
    assert pair['conditions'] == spatial['conditions']


def test_measurement_counterexamples_and_hom_amplitude(tmp_path):
    book = catalog(tmp_path)
    state = book.article('quantum-measurement-post-state')
    assert 'Σₐ MₐρMₐ†' in state['formula'] and '仍得 |0⟩⟨0|' in state['example']
    p0,p1 = np.diag([1,0]), np.diag([0,1])
    for rho, expected_purity in ((p0,1), (np.ones((2,2))/2,0.5)):
        result = p0 @ rho @ p0 + p1 @ rho @ p1
        assert np.trace(result@result) == pytest.approx(expected_purity)
    text = book.article('quantum-hom-interference')['body']
    assert '都透射' in text and '都反射' in text and '一透一反对应两个光子到同一个输出端' in text
    assert (1/np.sqrt(2))**2 + (1j/np.sqrt(2))**2 == pytest.approx(0)


@pytest.mark.parametrize('question,fragment',[
    ('两个已确认相邻同族谷在1548、1549 nm，缺微环半径还能换算GHz吗？','125.025422 GHz'),
    ('两幅暗图差值方差是18 DN²，单帧随机噪声标准差是多少？','3 DN'),
    ('面积4 mm²，NEP为2 pW/√Hz，D*用Jones该是多少？','1e+11 cm·√Hz/W'),
    ('半波片入射方向10°，快轴35°，输出是70°吗？','60°'),
    ('单缝波长532 nm、缝宽20 μm、屏距0.8 m，第一暗纹在哪？','21.28 mm'),
    ('若对角矩阵的对角元为 0.75、0.25，纯度是多少？','0.625'),
])
def test_deterministic_examples(question,fragment,tmp_path):
    result = answer(None,catalog(tmp_path),{'question':question})
    assert result['status'] == 'FOUND'
    assert fragment in result['calculation']['result']
    assert not result['model_calls'] and not result['solver_calls']


def test_calculation_does_not_guess_unsupported_quantities():
    assert calculate('NEP 2 pW/√Hz，带宽-9 Hz 的噪声') is None
    assert calculate('NEP 2 pW/√Hz，带宽9 Hz，不是白噪声，实际怎么求？') is None
    assert calculate('已知对角元 0.75、0.25，纯度是多少？') is None
    assert calculate('两张噪声相关的暗图相减方差为 18 DN²') is None
    mixed = calculate('1080 nm 和 1620 nm 和频与差频的波长是多少')
    assert '648 nm' in mixed['result'] and '3240 nm' in mixed['result']


def test_strong_retrieval_keeps_physical_evidence(tmp_path):
    book = catalog(tmp_path)
    for question,expected in (
        ('用示波器均匀取样，信号频率很高，补零后还能救回来吗？','time-sampling-alias'),
        ('我先把 |+⟩ 送进去做 Z 基测量，出来的结果忘掉了，接下来是什么？','quantum-measurement-post-state'),
        ('两光子从两个输入进平衡分束器，互相抵消的是哪两路？','quantum-hom-interference'),
        ('我的准直高斯光脉冲带二阶谱相位，是怎样展宽的？','ultrafast-gaussian-gdd-broadening'),
    ):
        reply = answer(None,book,{'question':question})
        assert expected in reply['direct']


@pytest.mark.parametrize('question,result',[
    ('变换极限 Gaussian 强度宽 120 fs，加 3000 fs² 二阶谱相位后输出多少？','138.58 fs'),
    ('均匀小信号晶体长 12 mm，相位失配主瓣半高全宽和第一零点是多少？','463.833 m⁻¹'),
    ('无损参量下转换，泵 320 THz，信号 200 THz，信号增加 0.50 mW 时闲频增加多少？','0.3 mW'),
    ('两处都是向前的无损平面波，峰值电场一样，甲的波阻抗 300 Ω，乙的 600 Ω。谁的平均能流更大？','甲的平均能流更大'),
])
def test_registered_advanced_calculations(question,result):
    assert result in calculate(question)['result']


def test_slit_calculation_retains_values_for_width_revision_only():
    first = calculate('单缝用 520 nm、缝宽 32 μm、屏距 1.2 m，求中心到一侧第一暗纹的距离。')
    assert '19.5 mm' in first['result'] and '39 mm' in first['result']
    assert calculate('补充：不满足远场，刚才全宽还能当成实测吗？',first) is None
    revised = calculate('回到原来理想远场，仅把缝宽改成 40 μm，全宽是多少？',first)
    assert revised['inputs']['distance_m'] == first['inputs']['distance_m']
    assert '31.2 mm' in revised['result']


def test_qubit_basis_switch_changes_unrecorded_state():
    initial = calculate('比较 |+⟩ 在 Z 基测量时保留与忘掉结果的状态')
    assert '最大混合态' in initial['result']
    changed = calculate('输入改为 |0⟩，还是忘掉结果。',initial)
    assert '纯态 |0⟩⟨0|' in changed['result']
    switched = calculate('沿用 |0⟩ 输入，改用 X 基测量并忘掉结果。',changed)
    assert '最大混合态' in switched['result']
    selected = calculate('现在保存测量记录，只看到 X 的正结果。',switched)
    assert '所选条件态是 |+⟩' in selected['result']
    assert '最大混合态' in selected['result']


def test_topic_selects_relevant_section_instead_of_first_text(tmp_path):
    book = catalog(tmp_path)
    result = answer(None,book,{'question':'线性折射率的 B 项怎样相消？','lesson':'phase-group-dispersion'})
    assert result['sections'][0]['heading'] == '把线性有效折射率代进去'
    assert 'A+Bλ₀−λ₀B=A' in result['sections'][0]['text']
    focused = answer(None,book,{'question':'讲讲这里','lesson':'phase-group-dispersion','section':'measurement'})
    assert len(focused['sections']) == 1 and '曲率' in focused['sections'][0]['text']
    chosen = answer(None,book,{'question':'讲一下这个式子','lesson':'phase-group-dispersion',
                  'section':'linear-cancellation','selection':'A+Bλ₀−λ₀B=A'})
    follow = answer(None,book,{'question':'这一步还能拆开解释吗？'},
                    history=[{'question':chosen['question'],'answer':chosen}])
    assert follow['selection'] == chosen['selection']
    assert follow['section'] == 'linear-cancellation'


def test_model_heading_wraps_and_optional_section_keeps_main_answer(tmp_path):
    heading = '说明波长之间的间隔与频率之间的间隔为什么不能直接使用同一单位做数值相减'
    paragraph = '两个真空波长先分别换算为频率，频率差约为 124.70 GHz。'

    class Client:
        def preflight(self):
            pass

        def complete(self, *_):
            sections = [{'heading':heading,'text':paragraph,'references':['microring-fsr']},
                        {'heading':'延伸阅读','text':'同族相邻时才能解释成 FSR。','references':['microring-fsr']}]
            return {'assistant':{'content':json.dumps({'sections':sections})},
                    'audit':{'live_dispatch':True}}

    store = SimpleNamespace(owner=SimpleNamespace(),client=Client())
    capture = {}
    result = answer(store,catalog(tmp_path),
                    {'question':'微环两个谷为 1550 nm 和 1551 nm，FSR 用 GHz 怎样算？',
                     'mode':'live','approve_data':True},capture=capture)
    assert result['mode'] == 'LIVE_AI'
    assert result['sections'] == [{'heading':heading,'text':paragraph,'references':['microring-fsr']}]
    assert result['optional_section']['heading'] == '延伸阅读'
    assert capture['presentation_adjusted'] and paragraph in json.loads(capture['assistant']['content'])['sections'][0]['text']


def test_new_topics_are_grouped_with_search_hits(tmp_path):
    book = catalog(tmp_path)
    result = book.search('准相位匹配 相干长度')
    assert 'nonlinear-qpm-from-shg' in {lesson['id'] for lesson in result['study_topics']}
    assert not book.search('不可读取的 ZZQ8876 装置')['study_topics']


def test_learning_calculations_retain_the_physical_convention():
    start = calculate('波长 1.55 μm，有效折射率 2.42，斜率 −1.1 /μm，求群折射率。')
    revised = calculate('其余保持，只把斜率改成 −0.8 /μm。', start)
    assert start['inputs']['slope_per_nm'] == pytest.approx(-.0011)
    assert revised['inputs'] == {'index': 2.42, 'wavelength_nm': 1550., 'slope_per_nm': -.0008}
    assert '= 3.66' in revised['result']
    initial = calculate('包络延后 80 fs，合成核用 exp(-iΩt)，谱相位斜率多少？')
    switched = calculate('保持延迟，合成核改用 exp(+iΩt)。', initial)
    assert switched['inputs'] == {'shift_fs':80., 'inverse_sign':'+'}
    assert 't−80 fs' in switched['result'] and '斜率为 -80 fs' in switched['result']
    scaled = calculate('理想单缝焦平面，波长变成原来的 1.2 倍、焦距改为原来的六分之五，缝宽不变，第一暗纹怎样变？')
    assert '(1.2) × (5/6) ÷ (1) = 1 倍' in scaled['result']


def test_saved_learning_calculation_prepares_only_the_stated_slit():
    question = '单缝用 532 nm，缝宽 20 μm，焦距 180 mm，缝高 100 μm。求第一暗纹离中心的距离。'
    calculation = calculate(question)
    preview = prepare_goal(_slit_input({'question':question,'calculation':calculation},False))
    assert preview['status'] == 'PREVIEW'
    assert preview['plan']['spec']['allowed_variables']['slit_width_um'] == [20.]
    assert preview['plan']['goal']['target'] == pytest.approx(4.788)


def test_withdrawing_far_field_uses_latest_width_without_requesting_ai(tmp_path):
    book=catalog(tmp_path)
    initial='按远场，单缝用 500 nm、缝宽 25 μm、屏距 0.8 m，求中央亮区全宽。'
    first=answer(None,book,{'question':initial})
    update='其他照旧，只把缝宽换成 40 μm，中央亮区全宽呢？'
    history=[{'question':initial,'answer':first}]
    second=answer(None,book,{'question':update},history=history)
    reply=answer(None,book,{'question':'现在确认不满足远场近似。只说最新一次的宽度还适不适用。','mode':'live'},
                 history=history+[{'question':update,'answer':second}])
    assert reply['mode']=='LOCAL_REFERENCE' and reply['model_calls']==0
    assert '自由空间远场' in reply['message']
    assert '20 mm' in reply['sections'][0]['text'] and '32 mm' not in reply['sections'][0]['text']


def test_knowledge_only_server_isolated_from_experiments(tmp_path):
    with make_server(tmp_path,port=0,allow_workers=False,handler=KnowledgeHandler) as server:
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            api = Workbench(server,bootstrap='/knowledge/bootstrap')
            connection = api.api('/knowledge/bootstrap')
            assert connection['standalone'] and not connection['ai_connected']
            reply = api.api('/knowledge/ask',{'question':'半波片入射方向10°，快轴35°，输出是70°吗？'})
            assert '60°' in reply['calculation']['result']
            article = api.api('/knowledge/articles/fourier-transform-pair')
            assert article['conventions'][0]['id'] == 'fourier'
            with pytest.raises(RuntimeError,match='HTTP 404'):
                api.api('/bootstrap')
            with pytest.raises(RuntimeError,match='HTTP 404'):
                api.api('/cases',{'kind':'teaching','domain':'gaussian'})
            assert not (tmp_path/'experiments').exists()
        finally:
            server.shutdown()
            thread.join()


def test_local_study_turns_restore_keep_values_and_isolate_experiments(tmp_path):
    with make_server(tmp_path,port=0,allow_workers=False) as server:
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            api = Workbench(server)
            session_id = 'a'*32
            first = {'session_id':session_id,'turn_id':'b'*32,'expected_turn_id':None,
                     'question':'比较3 pW/√Hz、16 Hz与5 pW/√Hz、4 Hz，均为白噪声。'}
            start = api.api('/knowledge/ask',first)
            assert '12 pW' in start['calculation']['result'] and '10 pW' in start['calculation']['result']
            second = first | {'turn_id':'c'*32,'expected_turn_id':'b'*32,'question':'第二台带宽改成9 Hz呢？'}
            follow = api.api('/knowledge/ask',second)
            assert '12 pW' in follow['calculation']['result'] and '15 pW' in follow['calculation']['result']
            assert api.api('/knowledge/ask',second) == follow
            assert api.api('/knowledge/sessions/'+session_id) == follow
            summary = api.api('/knowledge/sessions/'+session_id+'/summary')
            assert len(summary['turns']) == 2
            assert '15 pW' in summary['turns'][1]['calculation']['result']
            assert all('model_record' not in turn for turn in summary['turns'])
            third = second | {'turn_id':'e'*32,'expected_turn_id':'c'*32,
                              'question':'刚才第一台为什么还是12 pW，第二台改用原来的4 Hz会怎样？'}
            revised = api.api('/knowledge/ask',third)
            assert '12 pW' in revised['calculation']['result'] and '10 pW' in revised['calculation']['result']
            with pytest.raises(RuntimeError,match='HTTP 409'):
                api.api('/knowledge/ask',second|{'turn_id':'d'*32})
            assert not list(tmp_path.glob('experiments/*/case.json'))
        finally:
            server.shutdown()
            thread.join()
