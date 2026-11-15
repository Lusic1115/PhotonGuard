"""Durable tutoring turns, separate from experiment revisions and tool approval."""

import json
import re

from ..core import digest
from ..workflow import WorkflowError, atomic_json
from .knowledge_retrieval import general_followup, is_followup

_ID = re.compile(r'[a-f0-9]{32}')
_REVISIT = re.compile(r'^\s*(?:请|我想)?(?:回到|回看|重看|打开)(?:上一个问题|上一问|上一张图)[。！\s]*$')
DEPTHS = {'short', 'formula', 'result'}


def _id(value, title):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise WorkflowError(title + '无效。')
    return value


def _path(store, ident):
    return store.owner.safe('learning-sessions', _id(ident, '学习会话') + '.json')


def _binding(context):
    return ({key: context['source'][key] for key in ('record_id', 'result_sha256')} if context else None)


def saved_turns(store, ident, turn_id):
    _id(turn_id,'学习轮次')
    with store.lock:
        path = _path(store,ident)
        turns = json.loads(path.read_bytes())['turns'] if path.is_file() else []
        for i,turn in enumerate(turns):
            if turn['id']==turn_id:
                return turns[max(0,i-7):i+1]
    raise WorkflowError('找不到这轮助教回答。',404)


def _public(session):
    turns = session['turns']
    return {'id': session['id'], 'binding': session['binding'], 'learning_task_id': session.get('learning_task_id'),
            'last_turn_id': turns[-1]['id'] if turns else None, 'turn_count': len(turns),
            'turns': [{'id': turn['id'], 'question': turn['request']['question'], 'answer': turn['answer']['intro'],
                       'conclusion': turn['answer'].get('conclusion'), 'topic': turn['topic'],
                       'depth': turn['answer']['depth'],
                       'source': turn['answer'].get('source'),
                       'figure': turn['answer'].get('figure'),
                       'comparison_source': (turn['answer'].get('comparison') or {}).get('reference'),
                       'candidate_id': (turn['answer'].get('result') or {}).get('selected_candidate_id'),
                       'view': turn['answer']['view'], 'mode': turn['answer']['mode']} for turn in turns[-10:]]}


def get_session(store, ident):
    with store.lock:
        path = _path(store, ident)
        if not path.is_file():
            raise WorkflowError('没有找到这段学习对话。', 404)
        return _public(json.loads(path.read_text(encoding='utf-8')))


def depth_for(question, requested, previous='short'):
    if re.search(r'没懂|不太懂|再简单|简单点|换(?:一)?个例子|类比|用图|(?:不要|不用|先别|暂不)(?:公式|推导)', question):
        return 'short'
    if re.search(r'推导|公式怎么来|为什么.{0,5}平方|展开公式', question):
        return 'formula'
    if re.search(r'结合.{0,10}(?:结果|参数)|那组参数|当前参数|这条曲线|这张图', question):
        return 'result'
    return requested or previous


def _prior_summary(turns, candidate_id, binding=None, reference=None, *, task=False):
    recent = []
    for turn in [item for item in turns if not item['answer'].get('revisited_turn_id')][-10:]:
        item = {'topic': turn['topic']}
        same_candidate = turn['request']['candidate_id'] == candidate_id
        if task:
            previous = turn['request'].get('binding')
            item.update(turn_id=turn['id'], result=previous, candidate_id=turn['request']['candidate_id'])
            allowed = (previous == binding and same_candidate or reference and
                       previous == reference['binding'] and turn['request']['candidate_id'] == reference['candidate_id'])
        else:
            allowed = same_candidate
        if allowed:
            item['question'] = turn['request']['question'][:180]
            item['answer'] = turn['answer']['intro'][:240]
            if turn['answer'].get('explanation_source') == 'LLM_GENERAL_EXPLANATION':
                item['general_explanation'] = True
                item['continued_general'] = turn['answer'].get('continued_general',False)
                item['question'] = turn['request']['question'][:700]
            item['compared_slit_changes'] = bool(
                task and previous == binding and same_candidate and reference
                and turn['request'].get('reference') == reference
                and (turn['answer'].get('comparison') or {}).get('causes'))
        recent.append(item)
    return recent


def run_turn(store, data, context, question, generate, *, reference=None):
    ident = _id(data.get('session_id'), '学习会话')
    turn_id = _id(data.get('turn_id'), '学习轮次')
    expected = data.get('expected_turn_id')
    if expected is not None:
        _id(expected, '上一轮学习记录')
    binding = _binding(context)
    task_id = data.get('learning_task_id')
    candidate = context.get('selected_candidate_id') if context else None
    request = {'question': question, 'mode': data.get('mode', 'local'), 'approved_data': data.get('approve_data') is True,
               'depth': data.get('depth'), 'view': data.get('view', 'physical'), 'expected_turn_id': expected,
               'candidate_id': candidate}
    if task_id:
        request['binding'] = binding
        request['reference'] = ({'binding': _binding(reference), 'candidate_id': reference.get('selected_candidate_id')}
                                if reference else None)
    if 'figure_id' in data:
        request['figure_id'] = data['figure_id']
    path = _path(store, ident)
    fingerprint = digest(request)
    with store.lock:
        session = (json.loads(path.read_text(encoding='utf-8')) if path.is_file()
                   else {'id': ident, 'binding': None if task_id else binding, 'learning_task_id': task_id, 'turns': []})
        if session.get('learning_task_id') != task_id or not task_id and session['binding'] != binding:
            raise WorkflowError('这段对话属于另一版结果。请打开对应版本或开始新对话。', 409)
        turns = session['turns']
        prior = next((turn for turn in turns if turn['id'] == turn_id), None)
        if prior:
            if prior['request_sha256'] != fingerprint:
                raise WorkflowError('此轮编号已绑定另一条问题。', 409)
            return {**prior['answer'], 'session': _public(session), 'reused_turn': True}
        last = turns[-1] if turns else None
        if expected != (last['id'] if last else None):
            raise WorkflowError('对话已有新回复。请读取最新一轮后再提问。', 409)
        pending = getattr(store.owner, '_pending_learning_sessions', None)
        if pending is None:
            pending = store.owner._pending_learning_sessions = set()
        if ident in pending:
            raise WorkflowError('这段对话正在生成回复。', 409)
        pending.add(ident)
    try:
        depth = depth_for(question, data.get('depth'), last['answer']['depth'] if last else 'short')
        if _REVISIT.fullmatch(question) and turns:
            # Skip a previous revisit when looking for the actual earlier topic.
            original = [turn for turn in turns if not turn['answer'].get('revisited_turn_id')]
            earlier = original[-2] if len(original) > 1 else original[-1]
            answer = {**earlier['answer'], 'question': question, 'prediction': None,
                      'mode': 'SAVED_EXPLANATION', 'solver_calls': 0, 'next_step': None, 'suggestion': None,
                      'revisited_turn_id': earlier['id'], 'request_id': None}
            topic = earlier['topic']
        else:
            latest_general = last and last['answer'].get('explanation_source') == 'LLM_GENERAL_EXPLANATION'
            followup = bool(last and (reference or is_followup(question)
                                      or latest_general and general_followup(question)))
            prior_topic = last['topic'] if followup else None
            history = _prior_summary(turns, candidate, binding, request.get('reference'), task=bool(task_id)) if followup else []
            answer = generate(depth, prior_topic, history)
            topic = answer.get('topic')
            if not topic and last and answer.get('explanation_source') != 'LLM_GENERAL_EXPLANATION':
                topic = last['topic']
        with store.lock:
            turns.append({'id': turn_id, 'request': request, 'request_sha256': fingerprint,
                          'topic': topic, 'answer': answer})
            path.parent.mkdir(exist_ok=True)
            atomic_json(path, session)
            return {**answer, 'session': _public(session), 'reused_turn': False}
    finally:
        with store.lock:
            pending.discard(ident)
