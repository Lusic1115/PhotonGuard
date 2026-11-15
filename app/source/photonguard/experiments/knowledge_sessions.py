"""Version-bound study conversations with no experiment state or tool approval."""
import json
from uuid import uuid4

from ..core import digest
from ..workflow import WorkflowError, atomic_json
from .learning_session import _id


def _path(store, ident):
    return store.owner.safe('knowledge-sessions', _id(ident, '知识学习会话') + '.json')


def _read(path):
    return json.loads(path.read_bytes()) if path.is_file() else None


def _public(state, answer):
    turns = state['turns']
    return answer | {'session': {'id':state['id'], 'last_turn_id':turns[-1]['id'],
        'turns':[{'id':turn['id'],'question':turn['question'], 'status':turn['answer']['status']}
                 for turn in turns[-6:]]}}


def latest(store, ident):
    with store.lock:
        state = _read(_path(store, ident))
    if state is None:
        raise WorkflowError('本地没有这段学习对话。',404)
    return _public(state, state['turns'][-1]['answer'])


def saved_turn(store, ident, turn_id):
    turn_id = _id(turn_id, '学习轮次')
    with store.lock:
        state = _read(_path(store, ident))
    if state is not None:
        for turn in state['turns']:
            if turn['id'] == turn_id:
                return turn['answer']
    raise WorkflowError('找不到这轮学习记录。请回到原问题再接续。',404)


def replay(store, ident, turn_id):
    answer = saved_turn(store, ident, turn_id)
    with store.lock:
        state = _read(_path(store,ident))
    return _public(state,answer)|{'viewed_turn_id':turn_id}


def import_tutor(store, book, tutor_turns):
    """Read-only study copy of saved sidebar explanations and references."""
    state = {'id':uuid4().hex,'catalog_version':book.version,'turns':[]}
    for original in tutor_turns:
        answer = original['answer']
        ids = [card['id'] for card in answer['cards'] if card['id'] in book.entries]
        general = answer.get('explanation_source')=='LLM_GENERAL_EXPLANATION'
        source = answer.get('source')
        sections = [{'heading':'助教讲解','text':answer['intro'],'references':ids}]
        if source:
            sections.append({'heading':'保存的实验摘要','text':'\n'.join(answer.get('focused_facts') or
                              answer.get('grounded_facts') or ['已保存实验 '+source['record_id']]),'references':[]})
        copy = {'question':answer['question'],'mode':answer['mode'],'status':'GENERAL' if general else 'FOUND',
                'catalog_version':book.version,'sections':sections,'references':[book.article(i) for i in ids],
                'direct':ids,'learning_order':[],'continued_general':answer.get('continued_general',False),
                'imported_result':source,'solver_calls':0,'model_calls':0}
        state['turns'].append({'id':uuid4().hex,'question':answer['question'],'answer':copy,
                               'fingerprint':digest(original['id'])})
    with store.lock:
        path=_path(store,state['id'])
        path.parent.mkdir(exist_ok=True)
        atomic_json(path,state)
    return _public(state,state['turns'][-1]['answer'])


def summary(store, ident):
    with store.lock:
        state = _read(_path(store, ident))
    if state is None:
        raise WorkflowError('本地没有这段学习对话。',404)
    items = []
    for turn in state['turns']:
        reply = turn['answer']
        references = {part['id']:part for part in reply.get('references',[])}
        used = list(dict.fromkeys(ident for part in reply.get('sections',[]) for ident in part['references']))
        items.append({'question':turn['question'],'status':reply['status'],'mode':reply['mode'],
                      'message':reply.get('message'),'sections':reply.get('sections',[]),
                      'selection':reply.get('selection'),'calculation':reply.get('calculation'),
                      'previous_calculation':reply.get('previous_calculation'),
                      'conventions':reply.get('conventions',[]),'active_convention':reply.get('active_convention'),
                      'experiment':reply.get('experiment'),
                      'references':[{key:references[ident][key] for key in ('id','title','pack','version','sources')}
                                    for ident in used if ident in references]})
    return {'catalog_version':state['catalog_version'],'turns':items}


def ask(store, book, data, generate, *, experiment=None):
    fields = {'session_id','turn_id','expected_turn_id','resume_turn_id'}
    if not fields.intersection(data):
        if experiment is not None:
            raise WorkflowError('请从实验返回原学习会话。',409)
        return generate(store, book, data)
    ident = _id(data.get('session_id'), '知识学习会话')
    turn_id = _id(data.get('turn_id'), '学习轮次')
    expected = data.get('expected_turn_id')
    resume = data.get('resume_turn_id')
    if resume is not None:
        _id(resume,'要接续的学习轮次')
    if expected is not None:
        _id(expected, '上一轮记录')
    path = _path(store, ident)
    fingerprint = digest([data,experiment['source']['record_id'],experiment['source']['result_sha256']]) if experiment else digest(data)
    request = {key:value for key,value in data.items() if key not in fields}
    with store.lock:
        state = _read(path) or {'id':ident, 'catalog_version':book.version, 'turns':[]}
        if state['catalog_version'] != book.version:
            raise WorkflowError('知识库版本已更新，请开始新问题。',409)
        turns = state['turns']
        for turn in turns:
            if turn['id'] == turn_id:
                if turn['fingerprint'] != fingerprint:
                    raise WorkflowError('此轮已绑定另一条问题。',409)
                return _public(state, turn['answer'])
        if expected != (turns[-1]['id'] if turns else None):
            raise WorkflowError('这段学习对话已有更新，请读取最新回复。',409)
        if resume and not any(turn['id']==resume for turn in turns):
            raise WorkflowError('这段会话中找不到要接续的轮次。',404)
        pending = getattr(store.owner, '_pending_knowledge_turns', None)
        if pending is None:
            pending = store.owner._pending_knowledge_turns = set()
        if ident in pending:
            raise WorkflowError('这段学习对话正在回复。',409)
        pending.add(ident)
    try:
        history = [{'question':turn['question'], 'answer':turn['answer'],
                    **({'resume':True} if turn['id']==resume else {})} for turn in turns]
        capture = {}
        options = {'experiment':experiment} if experiment is not None else {}
        answer = generate(store,book,request,history=history,capture=capture,**options)
        with store.lock:
            turn = {'id':turn_id, 'fingerprint':fingerprint, 'question':request['question'], 'answer':answer}
            if capture:
                turn['model_record'] = capture
            turns.append(turn)
            state['turns'] = turns[-24:]
            path.parent.mkdir(exist_ok=True)
            atomic_json(path,state)
        return _public(state,answer)
    finally:
        with store.lock:
            pending.discard(ident)
