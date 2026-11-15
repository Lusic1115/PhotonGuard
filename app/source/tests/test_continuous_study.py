import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from photonguard.experiments import knowledge_learning as learning, knowledge_math, knowledge_sessions
from photonguard.experiments.knowledge_catalog import catalog
from photonguard.experiments.knowledge_routes import StudyStore
from photonguard.workflow import WorkflowStore


@pytest.mark.parametrize('word,absolute,relative', [('4倍','2','0.5'),('1/4','0.5','2')])
def test_shot_noise_uses_current_over_rms_for_amplitude_snr(tmp_path,word,absolute,relative):
    q=f'固定带宽，平均电流增为{word}，散粒噪声怎么变？'
    ans=learning.answer(None,catalog(tmp_path),{'question':q})
    assert ans['calculation']['id']=='shot-noise-scale'
    assert f'绝对噪声为 {absolute} 倍，相对噪声为 {relative} 倍，信号与噪声幅度之比为 {absolute} 倍' in ans['sections'][0]['text']
    assert not knowledge_math.calculate('电流增为4倍但带宽也变，散粒噪声怎样？')


def test_fiber_card_and_math_have_different_roles(tmp_path):
    book=catalog(tmp_path)
    lesson=learning.answer(None,book,{'question':'光纤里光从太斜的方向进不去，这叫什么？'})
    assert lesson['direct'][0]=='fiber-numerical-aperture'
    assert 'fiber-numerical-aperture' not in learning.select_articles(book,'光纤接头贵不贵？')
    old=knowledge_math.calculate('直阶跃多模光纤，纤芯 1.49，包层 1.46，空气中接受半角是多少？')
    new=knowledge_math.calculate('外部介质改为 1.33，接受半角怎样变？',old)
    assert old['inputs']|{'outside_index':1.33}==new['inputs']
    assert '12.925' in new['result'] or '12.924' in new['result']


def test_missing_reference_drops_dependent_paragraph():
    sections=[{'heading':'假设','text':'这一段是缺失的必要前提。','references':['absent']},
              {'heading':'结果','text':'因此可以直接推出这个结果。','references':['valid']},
              {'heading':'定义','text':'这一段自己给出了完整定义。','references':['valid']}]
    raw={'assistant':{'content':json.dumps({'sections':sections})}}
    assert [row['heading'] for row in learning._sections(raw,{'valid'})]==['定义']


def test_general_replay_preserves_first_user_condition(tmp_path):
    class Client:
        def preflight(self):
            pass

        def complete(self,_ident,payload):
            payloads.append(json.loads(payload['messages'][1]['content']))
            return {'assistant':{'content':'{"intro":"这轮解释只用于检查对话前文传递。"}'}}

    payloads=[]
    owner=WorkflowStore(tmp_path)
    owner.product_client=Client()
    store=StudyStore(owner)
    book=catalog(tmp_path)
    session=uuid4().hex
    last=None
    def turn(q,resume=None):
        nonlocal last
        body={'question':q,'mode':'live','approve_data':True,'session_id':session,'turn_id':uuid4().hex,'expected_turn_id':last}
        if resume:
            body['resume_turn_id']=resume
        last=knowledge_sessions.ask(store,book,body,learning.answer)['session']['last_turn_id']
        return last
    try:
        q='什么是 Sagnac 效应？我这里的角速度是 0.8 rad/s。'
        turn(q)
        turn('再简单讲讲。')
        selected=turn('那换个例子讲？')
        assert payloads[-1]['previous']['earlier_user_words']==[q]
        turn('换个话题，什么是 Mollow 三重峰？')
        assert 'previous' not in payloads[-1]
        turn('那把原先的转速加倍呢？',selected)
        assert q in payloads[-1]['previous']['earlier_user_words']
        assert not (tmp_path/'experiments').exists()
    finally:
        owner.close()
