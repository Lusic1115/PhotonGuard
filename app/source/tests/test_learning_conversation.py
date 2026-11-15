import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid

import pytest

from photonguard.experiments import learning_session, service, tutor
from photonguard.workflow import WorkflowError, WorkflowStore


@pytest.fixture
def workspace(tmp_path):
    owner = WorkflowStore(tmp_path)
    yield service.store_for(owner)
    owner.close()


def turn(store, ident, question, previous=None, **fields):
    return tutor.answer(store, {'question': question, 'session_id': ident, 'turn_id': uuid.uuid4().hex,
                                'expected_turn_id': previous, **fields})


def test_anonymous_followups_change_explanation_and_keep_topic(workspace):
    ident = uuid.uuid4().hex
    first = turn(workspace, ident, '什么是瑞利长度？')
    second = turn(workspace, ident, '没懂，换一个例子。', first['session']['last_turn_id'])
    assert first['conclusion']['id'] == second['conclusion']['id'] == 'gaussian_rayleigh'
    assert second['cards'][0]['id'] == 'gaussian-rayleigh' and first['intro'] != second['intro']
    third = turn(workspace, ident, '这里为什么是平方？', second['session']['last_turn_id'])
    assert third['depth'] == 'formula' and 'πw₀²/λ' in third['intro']
    assert third['solver_calls'] == 0
    revisit = turn(workspace, ident, '回到上一个问题。', third['session']['last_turn_id'],
                   mode='live', approve_data=True)
    assert revisit['mode'] == 'SAVED_EXPLANATION'
    assert revisit['revisited_turn_id'] == second['session']['last_turn_id']
    assert revisit['intro'] == second['intro'] and revisit['prediction'] is None
    saved = learning_session.get_session(workspace, ident)
    assert saved['turn_count'] == 4 and saved['binding'] is None
    assert [item['question'] for item in saved['turns']] == ['什么是瑞利长度？', '没懂，换一个例子。',
                                                            '这里为什么是平方？', '回到上一个问题。']


def test_explicit_topic_change_and_unclear_turn_do_not_reuse_previous_answer(workspace, monkeypatch):
    ident = uuid.uuid4().hex
    first = turn(workspace, ident, '束腰加倍后瑞利长度为什么变四倍？')
    assert first['retrieval']['concept_ids'][:2] == ['gaussian-rayleigh', 'gaussian-size']
    switched = turn(workspace, ident, '刚才听懂了，换个问题，膜的界面反射相位怎么来的？',
                    first['session']['last_turn_id'])
    assert switched['topic']['id'] == 'film-interfaces' and switched['retrieval']['basis'] == 'QUESTION'
    monkeypatch.setattr(tutor, 'read_only_client', lambda *_: pytest.fail('A clarification called the model'))
    unclear = turn(workspace, ident, '明天的菜谱怎么搭配？', switched['session']['last_turn_id'])
    assert unclear['retrieval']['basis'] == 'CLARIFY' and not unclear['cards']
    continued = turn(workspace, ident, '这里能再讲简单点吗？', unclear['session']['last_turn_id'])
    assert continued['retrieval']['basis'] == 'CONVERSATION' and continued['topic']['id'] == 'film-interfaces'
    assert turn(workspace, uuid.uuid4().hex, '半高全宽是什么？')['retrieval']['basis'] == 'CLARIFY'
    choices = turn(workspace, uuid.uuid4().hex, '数值孔径是什么意思？', mode='live', approve_data=True)
    assert {item['id'] for item in choices['choices']} == {'objective-na', 'fiber-numerical-aperture'}
    assert not choices['cards']


@pytest.mark.parametrize('question,concept,answer_fragment', [
    ('束腰','gaussian-waist','半径最小'),
    ('什么是束腰','gaussian-waist','半径最小'),
    ('请问一下束腰这个词是什么意思？','gaussian-waist','半径最小'),
    ('束腰半径是啥？','gaussian-waist','最小半径'),
    ('What is a beam waist?', 'gaussian-waist','半径最小'),
    ('TEM₀₀是什么','gaussian-tem00','基横模'),
    ('M^2 是什么？','gaussian-beam-quality','衍射极限'),
    ('高斯波前曲率怎么理解？','gaussian-curvature','曲率为零'),
    ('衍射的定义','diffraction-basics','空间扩展'),
    ('什么是菲涅尔衍射','diffraction-regimes','二次项'),
    ('wavefront','optics-wavefront','相位相同'),
    ('相干光指什么','optics-coherence','相位关系'),
    ('什么叫光栅','diffraction-grating','周期性'),
    ('何谓瑞利判据','diffraction-resolution','刚好可分辨'),
    ('什么是全反射','optics-total-internal-reflection','临界角'),
    ('请解释一下增透膜','film-antireflection','降低反射'),
    ('DBR是啥','film-bragg-mirror','相长叠加'),
])
def test_reference_definitions_without_saved_experiment(workspace, question, concept, answer_fragment):
    reply = tutor.answer(workspace, {'question': question})
    assert reply['retrieval']['basis'] == 'DEFINITION' and reply['topic']['id'] == concept
    assert answer_fragment in reply['conclusion']['text']
    assert reply['conclusion_source'] == 'CHECKED_KNOWLEDGE' and reply['cards'][0]['sources']
    assert reply['solver_calls'] == 0 and reply['source'] is None


@pytest.mark.parametrize('question,concept', [
    ('啥叫偏振呀？', 'polarization'),
    ('什么是群折射率？', 'group-index'),
    ('焦深是啥', 'depth-of-focus-criterion'),
    ('什么是光子？', 'quantum-photon-energy'),
    ('光纤为啥能把光关在里面', 'dielectric-waveguide'),
    ('光在两种材料的界面反射，什么情况下反射光会只剩一个偏振方向？', 'brewster-zero-reflection'),
    ('什么是谐振腔', 'optical-resonator'),
    ('激光腔是啥？', 'optical-resonator'),
    ('激光', 'laser-basics'),
    ('泵浦是什么', 'laser-pumping'),
    ('什么是增益介质', 'laser-gain-medium'),
    ('激光横模是啥', 'laser-transverse-modes'),
    ('换个话题，啥叫受抑全反射？', 'frustrated-total-internal-reflection'),
    ('我又想问个别的，光纤数值孔径是啥？', 'fiber-numerical-aperture'),
    ('激光腔是啥？为啥要放两面镜子？', 'optical-resonator'),
    ('什么是非线性光学中的光学整流？', 'optical-rectification-envelope'),
    ('光子拖曳效应是啥？', 'photon-drag-current'),
    ('啥叫 Talbot 自成像？', 'talbot-self-imaging'),
    ('FRET 是什么？', 'forster-energy-transfer'),
])
def test_basic_concepts_share_the_full_catalog(workspace, question, concept):
    from photonguard.experiments.knowledge_catalog import catalog
    from photonguard.experiments.knowledge_learning import answer

    sidebar = tutor.answer(workspace, {'question':question})
    study = answer(None, catalog(workspace.owner.root), {'question':question})
    assert sidebar['topic']['id'] == study['direct'][0] == concept
    assert sidebar['cards'][0]['sources'] and sidebar['solver_calls'] == study['model_calls'] == 0


def test_catalog_concept_can_be_explained_in_a_followup(workspace):
    ident = uuid.uuid4().hex
    first = turn(workspace, ident, '什么是群折射率？')
    second = turn(workspace, ident, '没懂，再简单讲一次。', first['session']['last_turn_id'])
    assert first['topic'] == second['topic'] and second['retrieval']['basis'] == 'CONVERSATION'


@pytest.mark.parametrize('entry', ['tutor','knowledge'])
def test_live_unmatched_question_can_use_general_ai_without_inventing_sources(workspace, monkeypatch, entry):
    from photonguard.experiments import knowledge_learning, knowledge_routes
    from photonguard.experiments.knowledge_catalog import catalog

    class Client:
        text = '自注入锁定可以用频率选择性的反馈来稳住激光振荡频率。'
        requests = []

        def preflight(self):
            pass

        def complete(self, _id, payload):
            if not _id.startswith(('knowledge-context-','knowledge-review-')):
                self.requests.append(json.loads(payload['messages'][1]['content']))
            return {'assistant':{'content':json.dumps({'intro':self.text})},
                    'audit':{'status':'ASSISTANT_RETURNED','live_dispatch':True}}

    client = Client()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    if entry == 'tutor':
        ask = lambda body: tutor.answer(workspace, body)
        content = lambda result: result['intro']
    else:
        book = catalog(workspace.owner.root)
        ask = lambda body: knowledge_learning.answer(knowledge_routes.StudyStore(workspace.owner),book,body)
        content = lambda result: result['sections'][0]['text']
    request = {'question':'什么是自注入锁定'}

    assert ask(request)['mode'] == 'LOCAL_REFERENCE' and client.requests == []
    with pytest.raises(WorkflowError, match='同意'):
        ask(request | {'mode':'live'})
    assert client.requests == []
    live = request | {'mode':'live','approve_data':True}
    answer = ask(live)
    assert answer['mode'] == 'LIVE_AI_GENERAL' and answer['solver_calls'] == 0
    assert not answer.get('references') and not answer.get('cards') and not answer.get('source')
    assert content(answer) == client.text and client.requests == [request]
    lookup = ask(live | {'question':'请核对编号 A78 的设备序列号'})
    assert lookup['mode'] == 'LOCAL_REFERENCE' and client.requests == [request]
    client.text = '请参考这个无依据的链接 https://example.com'
    assert ask(live)['mode'] in ('GENERAL_AFTER_AI_FAILURE','LOCAL_REFERENCE_AFTER_AI_FAILURE')


@pytest.mark.parametrize('entry', ['tutor','knowledge'])
def test_general_followups_use_only_the_active_topic(workspace, monkeypatch, entry):
    from photonguard.experiments import knowledge_learning, knowledge_routes, knowledge_sessions
    from photonguard.experiments.knowledge_catalog import catalog

    class Client:
        def __init__(self):
            self.requests = []

        def preflight(self):
            pass

        def complete(self, _id, payload):
            if not _id.startswith(('knowledge-context-','knowledge-review-')):
                self.requests.append(json.loads(payload['messages'][1]['content']))
            return {'assistant':{'content':json.dumps({'intro':'这是一段用来核查上下文的讲解。'})},
                    'audit':{'status':'ASSISTANT_RETURNED','live_dispatch':True}}

    client = Client()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    book = catalog(workspace.owner.root)
    store = knowledge_routes.StudyStore(workspace.owner)
    session = uuid.uuid4().hex
    last = None

    def ask(question):
        nonlocal last
        data = {'question':question,'session_id':session,'turn_id':uuid.uuid4().hex,'expected_turn_id':last,
                'mode':'live','approve_data':True}
        if entry == 'tutor':
            result = tutor.answer(workspace,data)
        else:
            result = knowledge_sessions.ask(store,book,data,knowledge_learning.answer)
        last = result['session']['last_turn_id']
        assert result['mode'] == 'LIVE_AI_GENERAL'
        return client.requests[-1]

    first = '换个话题，什么是 Sagnac 效应？'
    assert ask(first) == {'question':first}
    assert ask('那反过来转会怎样？')['previous']['question'] == first
    switched = '什么是 Mollow 三重峰？能讲得口语一点吗？'
    assert ask(switched) == {'question':switched}
    assert ask('用不带公式的说法再说一遍呗。')['previous']['question'] == switched
    assert ask('什么叫光子雪崩？') == {'question':'什么叫光子雪崩？'}
    assert ask('光学中的相位恢复是什么？') == {'question':'光学中的相位恢复是什么？'}


def test_waist_ai_uses_the_same_reference_and_rejects_a_reversed_definition(workspace, monkeypatch):
    class Client:
        intro = '沿着光传播的方向观察，经过最窄处后，光束会逐渐向外展开。'

        def preflight(self):
            pass

        def complete(self, _ident, payload):
            self.public = json.loads(payload['messages'][1]['content'])
            obj = {'intent':'EXPLAIN_CONCEPT','conclusion_id':'gaussian-waist','intro':self.intro,
                   'card_ids':['gaussian-waist']}
            return {'assistant':{'content':json.dumps(obj)}}

    client = Client()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    question = {'question':'什么是束腰'}
    local = tutor.answer(workspace,question)
    live = tutor.answer(workspace,question|{'mode':'live','approve_data':True})
    assert live['mode']=='INJECTED_AI' and live['intro']==client.intro
    assert client.public['registered_conclusion']==local['conclusion']==live['conclusion']
    assert client.public['cards'][0]['sources']==local['cards'][0]['sources']==live['cards'][0]['sources']
    client.intro = '束腰处可以用该截面中心强度的 1/e² 确定光斑半径。'
    assert tutor.answer(workspace,question|{'mode':'live','approve_data':True})['mode']=='INJECTED_AI'
    client.intro = '束腰处半径等于 90 微米。'
    assert tutor.answer(workspace,question|{'mode':'live','approve_data':True})['mode']=='LOCAL_REFERENCE_AFTER_AI_FAILURE'
    client.intro = '不同截面都以同一个中心强度来确定光斑半径。'
    assert tutor.answer(workspace,question|{'mode':'live','approve_data':True})['mode']=='LOCAL_REFERENCE_AFTER_AI_FAILURE'
    client.intro = '束腰是高斯光束半径最大的截面。'
    rejected = tutor.answer(workspace,question|{'mode':'live','approve_data':True})
    assert rejected['mode']=='LOCAL_REFERENCE_AFTER_AI_FAILURE' and rejected['conclusion']==local['conclusion']


class ExplanationClient:
    def __init__(self):
        self.payloads = []

    def preflight(self):
        pass

    def complete(self, _ident, payload):
        self.payloads.append(json.loads(payload['messages'][1]['content']))
        return {'audit': {'live_dispatch': False, 'status': 'ASSISTANT_RETURNED'},
                'assistant': {'content': json.dumps({'intent': 'EXPLAIN_CONCEPT', 'conclusion_id': 'gaussian_rayleigh',
                                                     'intro': '先找到光束最细的截面，再看向前走多远才会达到结论中的半径。',
                                                     'card_ids': ['gaussian-rayleigh']})}}


def test_repeated_turn_replays_and_history_is_bounded(workspace, monkeypatch):
    client = ExplanationClient()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    ident = uuid.uuid4().hex
    latest = turn(workspace, ident, '什么是瑞利长度？')
    for _ in range(5):
        latest = turn(workspace, ident, '再简单一点。', latest['session']['last_turn_id'])
    data = {'question': '没懂，再讲一次。', 'session_id': ident, 'turn_id': uuid.uuid4().hex,
            'expected_turn_id': latest['session']['last_turn_id'], 'mode': 'live', 'approve_data': True}
    first = tutor.answer(workspace, data)
    second = tutor.answer(workspace, data)
    assert first['mode'] == 'INJECTED_AI' and second['reused_turn']
    assert first['explanation_source'] == 'LLM_GROUNDED_EXPLANATION'
    assert len(client.payloads) == 1 and len(client.payloads[0]['recent_lessons']) == 3
    assert first['session']['last_turn_id'] == second['session']['last_turn_id']
    with pytest.raises(WorkflowError, match='已有新回复'):
        turn(workspace, ident, '再来一遍。', latest['session']['last_turn_id'])
    with pytest.raises(WorkflowError, match='已绑定'):
        tutor.answer(workspace, {**data, 'question': '换一个问题。'})


def test_session_cannot_attach_saved_conversation_to_different_result(workspace, monkeypatch):
    ident = uuid.uuid4().hex
    first = turn(workspace, ident, '什么是瑞利长度？')
    fake = {'source': {'record_id': 'f' * 32, 'result_sha256': '0' * 64}, 'domain': 'gaussian'}
    monkeypatch.setattr(tutor, 'read_result', lambda *args: fake)
    with pytest.raises(WorkflowError, match='另一版结果'):
        turn(workspace, ident, '用刚才那组参数解释。', first['session']['last_turn_id'], record_id='f' * 32)


def test_same_turn_with_interrupted_provider_receipt_is_not_sent_twice(workspace, monkeypatch):
    class WorkerInterrupted(BaseException):
        pass

    class InterruptedClient(ExplanationClient):
        def complete(self, ident, payload):
            super().complete(ident, payload)
            raise WorkerInterrupted

    client = InterruptedClient()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    data = {'question': '瑞利长度是什么？', 'session_id': uuid.uuid4().hex, 'turn_id': uuid.uuid4().hex,
            'expected_turn_id': None, 'mode': 'live', 'approve_data': True}
    with pytest.raises(WorkerInterrupted):
        tutor.answer(workspace, data)
    recovered = tutor.answer(workspace, data)
    assert recovered['mode'] == 'LOCAL_REFERENCE_AFTER_AI_FAILURE' and recovered['session']['turn_count'] == 1
    assert tutor.answer(workspace, data)['reused_turn'] and len(client.payloads) == 1


def test_concurrent_tabs_cannot_dispatch_two_turns_with_the_same_predecessor(workspace, monkeypatch):
    entered, release = Event(), Event()

    class SlowClient(ExplanationClient):
        def complete(self, ident, payload):
            entered.set()
            assert release.wait(5)
            return super().complete(ident, payload)

    client = SlowClient()
    monkeypatch.setattr(workspace.owner, 'product_client', client, raising=False)
    data = {'question': '瑞利长度是什么？', 'session_id': uuid.uuid4().hex, 'turn_id': uuid.uuid4().hex,
            'expected_turn_id': None, 'mode': 'live', 'approve_data': True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(tutor.answer, workspace, data)
        try:
            assert entered.wait(5)
            with pytest.raises(WorkflowError, match='正在生成回复'):
                tutor.answer(workspace, {**data, 'question': '另一页又问一次瑞利长度。', 'turn_id': uuid.uuid4().hex})
        finally:
            release.set()
        assert first.result(timeout=5)['session']['turn_count'] == 1
    assert len(client.payloads) == 1


def test_study_history_keeps_version_and_candidate_scope():
    a, b = ({'record_id': char * 32, 'result_sha256': char * 64} for char in ('a', 'b'))

    def row(ident, binding, candidate, text, revisit=None):
        return {'id': ident, 'topic': None, 'request': {'binding': binding, 'candidate_id': candidate, 'question': text},
                'answer': {'intro': text, 'revisited_turn_id': revisit}}

    turns = [row('a1',a,'c1','来源条件'), row('a2',a,'c2','另一候选'),
             row('b1',b,'c1','当前条件'), row('b2',b,'c1','回到来源图','a1')]
    prior = learning_session._prior_summary(turns, 'c1', b, {'binding':a,'candidate_id':'c1'}, task=True)
    assert [entry.get('question') for entry in prior] == ['来源条件', None, '当前条件']
    assert [entry['result'] for entry in prior] == [a, a, b]
