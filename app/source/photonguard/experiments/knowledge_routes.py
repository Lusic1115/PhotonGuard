"""Read-only routes for the standalone knowledge page."""

from urllib.parse import parse_qs, urlsplit

from ..workflow import WorkflowError
from .knowledge_catalog import catalog


class StudyStore:
    def __init__(self, owner):
        self.owner = owner
        self.lock = owner.lock

    @property
    def client(self):
        binding = getattr(self.owner,'ai_binding',None)
        return binding.client if binding and binding.managed else getattr(self.owner,'product_client',None)


def get(handler, parts):
    store = StudyStore(handler.store)
    if len(parts)==4 and parts[0]=='sessions' and parts[2]=='turns':
        from .knowledge_sessions import replay
        handler.json(replay(store,parts[1],parts[3]))
        return True
    if len(parts)==3 and parts[0]=='sessions' and parts[2]=='summary':
        from .knowledge_sessions import summary
        handler.json(summary(store,parts[1]))
        return True
    if parts not in (['overview'],['search'],['bootstrap']) and not (len(parts)==2 and parts[0] in ('articles','topics','sessions')):
        return False
    if parts==['bootstrap']:
        client=store.client
        result = {'csrf':handler.store.csrf,'ai_connected':bool(client and client.status().get('live_enabled'))}
        if getattr(handler,'knowledge_only',False):
            result.update(standalone=True,ai=handler.store.ai_binding.status())
        handler.json(result)
        return True
    if parts[0]=='sessions':
        from .knowledge_sessions import latest
        handler.json(latest(store,parts[1]))
        return True
    book=catalog(handler.store.root)
    if parts==['overview']:
        handler.json(book.overview())
    elif parts==['search']:
        args=parse_qs(urlsplit(handler.path).query,keep_blank_values=True,max_num_fields=8)
        if set(args)-{'q','topic','level','license','capability','pack','page'} or any(len(values)!=1 for values in args.values()):
            raise WorkflowError('筛选参数无效。')
        def arg(name,default=''):
            return args.get(name,[default])[0]
        handler.json(book.search(arg('q'),topic=arg('topic'),level=arg('level'),license=arg('license'),
                                 capability=arg('capability'),pack=arg('pack'),page=int(arg('page','1'))))
    elif parts[0] == 'topics':
        if parts[1] not in book.study_topics:
            raise WorkflowError('该专题未在当前知识库中发布。', 404)
        topic = book.study_topics[parts[1]]
        references = {ident:book.entries[ident]['title']
                      for section in topic['sections'] for ident in section['references']}
        handler.json(topic | {'catalog_version':book.version,
                             'links':[book.brief(ident) for ident in topic['prerequisites']],
                             'reference_titles':references,
                             'conventions':book.conventions_for(references)})
    else:
        if parts[1] not in book.entries:raise WorkflowError('该条目未在当前知识包中发布。',404)
        handler.json(book.article(parts[1]))
    return True


def ask(handler, data):
    from .knowledge_learning import answer
    from .knowledge_sessions import ask as ask_session
    request=dict(data)
    experiment_id=request.pop('experiment_id',None)
    experiment=None
    if experiment_id is not None:
        if getattr(handler,'knowledge_only',False):
            raise WorkflowError('请在完整工作台中查看实验结果。',404)
        from .knowledge_experiment import result_context
        from .service import store_for
        experiment=result_context(store_for(handler.store),request.get('session_id'),experiment_id)
    handler.json(ask_session(StudyStore(handler.store),catalog(handler.store.root),request,answer,
                             experiment=experiment))
    return True


def expand_tutor(handler, data):
    from . import knowledge_sessions, learning_session
    from .service import store_for

    if set(data)!={'session_id','turn_id'}:
        raise WorkflowError('请选择要展开的助教回答。')
    store = store_for(handler.store)
    book = catalog(handler.store.root)
    tutor_turns = learning_session.saved_turns(store, data['session_id'],data['turn_id'])
    handler.json(knowledge_sessions.import_tutor(StudyStore(handler.store),book,tutor_turns))
    return True
