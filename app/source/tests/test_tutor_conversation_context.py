"""Regressions from live short follow-ups and split-device statements."""
import json
from types import SimpleNamespace
import pytest
from photonguard.experiments.knowledge_catalog import catalog
from photonguard.experiments.knowledge_learning import answer
from photonguard.experiments.knowledge_math import calculate, contextual_calculation


class CaptureClient:
    def __init__(self):
        self.payloads=[]
    def preflight(self):
        pass
    def complete(self, ident, payload):
        content=json.loads(payload['messages'][-1]['content'])
        self.payloads.append(content)
        if 'articles' in content:
            refs=[row['id'] for row in content['articles']]
            result={'sections':[{'heading':'解释','text':'当前问题沿用用户给定条件，再核对对应公式和单位。','references':refs[:1]}]}
        else:
            result={'intro':'按用户提供的光学条件说明概念，具体数据由所述对象确定。'}
        return {'assistant':{'content':json.dumps(result)},'audit':{'live_dispatch':True}}


@pytest.mark.parametrize('followup',[
    '我要给同学讲，三句话分清这两个角。',
    '别做完整实验，先解释趋势。',
    '单位别写成纳米，我问的是频率。',
])
def test_short_turn_keeps_previous_user_facts(tmp_path,followup):
    book=catalog(tmp_path)
    first='环周长200 μm，群折射率4，频率FSR约多少？'
    previous=answer(None,book,{'question':first})
    client=CaptureClient()
    answer(SimpleNamespace(owner=SimpleNamespace(),client=client),book,
           {'question':followup,'mode':'live','approve_data':True},
           history=[{'question':first,'answer':previous}])
    payload=next(p for p in reversed(client.payloads) if 'recent' in p or 'previous' in p)
    assert first in json.dumps(payload.get('recent',payload.get('previous')),ensure_ascii=False)


def test_explicit_new_topic_drops_prior_numeric_context(tmp_path):
    book=catalog(tmp_path);first='腔长30 cm，空气折射率1，纵模间隔多少？'
    old=answer(None,book,{'question':first});client=CaptureClient()
    answer(SimpleNamespace(owner=SimpleNamespace(),client=client),book,
           {'question':'换个话题，束腰是什么？','mode':'live','approve_data':True},
           history=[{'question':first,'answer':old}])
    p=client.payloads[-1]
    assert not p.get('recent') and not p.get('previous')


def test_single_second_device_is_not_relabelled_first():
    assert calculate('第二台5 pW/√Hz，带宽4 Hz，哪台更小？') is None
    comparison=calculate('比较3 pW/√Hz、16 Hz与5 pW/√Hz、4 Hz，白噪声。')
    assert '12 pW' in comparison['result'] and '10 pW' in comparison['result']
    updated=calculate('第二台带宽改成9 Hz呢？',comparison)
    assert '12 pW' in updated['result'] and '15 pW' in updated['result']


def test_split_user_conditions_keep_distinct_devices():
    history=['我看探测器写3 pW/√Hz，什么意思？','白噪声，等效带宽16 Hz，总功率多少？']
    q='第二台5 pW/√Hz，带宽4 Hz，哪台更小？'
    result=contextual_calculation(q,history)
    assert [d['bandwidth_hz'] for d in result['inputs']]==[16,4]
    assert '12 pW' in result['result'] and '10 pW' in result['result']
    updated=contextual_calculation('只把第二台带宽改成9 Hz呢？',history+[q])
    assert '12 pW' in updated['result'] and '15 pW' in updated['result']


def test_reversed_fret_uses_registered_formula():
    result=contextual_calculation('把顺序反过来，从1.6回到0.7呢？',
                                 ['FRET，r/R0从0.7到1.6，请解释'])
    assert result['inputs']=={'ratio_a':1.6,'ratio_b':.7}
    assert '+1490.59%' in result['result']


def test_fiber_user_indices_survive_outside_medium_change():
    q='直阶跃多模光纤，芯1.49、包层1.46、空气入射。估算接受半角并讲单位。'
    first=calculate(q)
    assert first['inputs']=={'core_index':1.49,'clad_index':1.46,'outside_index':1}
    updated=contextual_calculation('如果只把空气换成1.33的介质呢？芯包参数保留。',[q])
    assert updated['inputs']=={'core_index':1.49,'clad_index':1.46,'outside_index':1.33}


def test_plain_language_slit_goal_has_reviewable_plan_inputs():
    from photonguard.experiments.slit_goal import parse_goal, FULL_WIDTH
    result=parse_goal('做个教学单缝实验。用633纳米光，焦距200毫米的透镜，缝高100微米不动。'
                      '我想两侧第一暗纹相隔10毫米，允许误差0.2毫米。只调缝宽，15到35微米，其余用教学设置。')
    assert result['teaching'] is True
    assert result['values']['target_mm']==10
    assert result['values']['metric']==FULL_WIDTH
    assert result['values']['wavelength_nm']==633
    assert not result['pending'] and not result['conflicts']


def test_unit_followup_uses_current_topic_not_spectral_density(tmp_path):
    book=catalog(tmp_path);q='环周长200 μm，群折射率4，频率FSR约多少？'
    old=answer(None,book,{'question':q});client=CaptureClient()
    answer(SimpleNamespace(owner=SimpleNamespace(),client=client),book,
           {'question':'单位别写成纳米，我问的是频率。','mode':'live','approve_data':True},
           history=[{'question':q,'answer':old}])
    payload=next(p for p in reversed(client.payloads) if 'response_plan' in p)
    assert set(payload['response_plan']['direct'])==set(old['evidence_ids'][:2])
