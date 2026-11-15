"""Read-only learning across published knowledge entries."""

import re
from graphlib import TopologicalSorter
from uuid import uuid4

from ..product_phase import json_payload
from ..workflow import WorkflowError
from ..workflow_model import wire
from . import general_tutoring, knowledge_retrieval, knowledge_context, knowledge_answer_review, tutor
from .knowledge_catalog import PATHS, tokens
from .knowledge_math import calculate, contextual_calculation, slit_scope_changed


_FOLLOWUP = re.compile(r'刚才|上[一轮]|前面|那个|这一步|怎么相消|沿用|照旧|只把|还是|同一组|仍然|再怎么|'
                       r'这组|第[一二12][台路个].{0,10}(?:改|换)|\b[ab]\s*(?:设备|台)?\s*带宽.{0,8}改',re.I)
_EXPAND = re.compile(r'从.{0,10}开始|别跳步|不懂.{0,10}一步|逐步|推导|show.{0,12}steps',re.I)
_SKIP = re.compile(r'直接代入|定义.{0,8}(?:懂|跳过)|不要再讲|无需.{0,6}定义|skip.{0,12}definition',re.I)
_INTUITIVE = re.compile(r'通俗|直观|日常|类比|不带公式|不用公式|简单(?:点|讲|一些)|大白话')
_EXTENSION = re.compile(r'^(?:可选扩展|延伸(?:阅读)?|进一步阅读|拓展|extension\b)',re.I)
_THIS_SECTION = re.compile(r'这(?:一)?节|这(?:一)?步|这段|这句|刚才|前面|上[一轮]')
_RETURN_TOPIC = re.compile(r'回到|回看|重看')
_ASSUMPTION_UPDATE = re.compile(r'假设|前提|如果|条件|现在|改为|换成|改用')
_BASIS_MEASUREMENT = re.compile(r'(?:[xz]\s*基|计算基|偏振基).{0,12}测量|测量.{0,12}(?:[xz]\s*基|计算基)|忘掉结果|丢掉结果|没有保存.{0,8}结果',re.I)


def select_articles(book, question, focus=None, previous=()):
    question = knowledge_retrieval.canonical_notation(question)
    if not focus and general_tutoring.RECORD_REQUEST.search(question):
        return []
    ranked = [item['id'] for item in book.search(question,limit=20)['items']]
    # A description needs several independent clues, even without the technical name.
    query = set(tokens(question))
    evidence = {'fiber-numerical-aperture':({'光纤','纤芯'},{'太斜','斜着','入射','进不','进得','角度'}),
                'talbot-self-imaging':({'光栅','周期'},{'原来','复现','图案','重现','又出'}),
                'forster-energy-transfer':({'分子','供体','受体'},{'能量','传给','交给','转移'})}
    described = [ident for ident, clues in evidence.items() if ident in book.entries
                 and all(query & group for group in clues)]
    if described and not book.definition_matches(question,ranked):
        return list(dict.fromkeys(([focus] if focus else [])+described))
    if not focus and not book.definition_covered(question,ranked):
        return []
    exact = book.definition_matches(question,ranked)
    named = book.named(question,ranked)
    probes = []
    if (_BASIS_MEASUREMENT.search(question) or re.search(r'[xz]\s*基.{0,8}(?:测|量)',question,re.I)) and ('⟩' in question or '量子' in question or '密度矩阵' in question):
        probes.extend(('quantum-measurement-post-state','quantum-projective-measurement'))
    if re.search(r'暗场|暗图|暗帧',question) and re.search(r'相减|差分|噪声',question):
        probes.extend(('camera-temporal-noise-pair','camera-temporal-spatial-noise'))
    if re.search(r'取样|采样',question) and re.search(r'补零|折回|混叠|正弦',question):
        probes.extend(('time-sampling-alias','sampling-period-vs-window'))
    if re.search(r'回卷|卷绕|绕回|周期折回',question) and re.search(r'光斑|光场|旁瓣|计算窗',question):
        probes.extend(('fft-sampling','amplitude-aperture-edge','sampling-period-vs-window'))
    probes = [ident for ident in probes if ident in book.entries]
    if exact:
        results = exact
    elif probes:
        results = probes
    elif book.supports(question,ranked):
        # An explicit term and its context both matter in comparisons.
        # Keep lexical evidence even if another entry matches a short alias.
        results = list(dict.fromkeys(ranked[:3]+named[:3]+ranked[3:4]))
    elif previous and knowledge_retrieval.is_followup(question):
        results = list(previous)
    else:
        results = []
    return list(dict.fromkeys(([focus] if focus else [])+results))[:5]


def _path(book, direct, known):
    selected=list(direct)
    frontier=direct
    for _ in range(2):
        frontier=list(dict.fromkeys(prior for ident in frontier if ident not in known
                                   for prior in book.entries[ident]['prerequisites'] if prior not in selected))
        selected.extend(frontier[:8-len(selected)])
        if len(selected)==8 or not frontier:break
    graph={ident:[prior for prior in book.entries[ident]['prerequisites'] if prior in selected and prior not in known]
           for ident in selected if ident not in known}
    return list(TopologicalSorter(graph).static_order())


def _match(text, question_tokens):
    return len(question_tokens.intersection(tokens(text)))


def _body_for(text, question_tokens):
    if len(text) <= 1000:
        return text
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n|(?<=。)',text) if p.strip()]
    ranked = sorted(enumerate(paragraphs),key=lambda item:-_match(item[1],question_tokens))
    return '\n'.join(paragraph for _,paragraph in sorted(ranked[:3]))[:1200]


def _evidence(book, ids, question):
    question_tokens = set(tokens(question))
    for ident in ids:
        entry=book.entries[ident]
        yield {'id':ident,'title':entry['title'],'definition':entry['definition'],
               'body':_body_for(entry['body'],question_tokens),'conditions':entry['conditions'],
               'formula':entry.get('formula'),'variables':entry.get('variables'),
               'example':entry['example'],'caution':entry['caution'],
               'prerequisites':entry['prerequisites'],
               'claims':entry.get('claims',[]),
               'source_sections':[s['section'] for s in entry['sources']]}


def _sections(raw, ids):
    value=tutor.strict_object(raw)
    if set(value)!={'sections'} or not isinstance(value['sections'],list) or not 1<=len(value['sections'])<=5:
        raise ValueError('讲解格式不完整。')
    sections = []
    missing_predecessor = False
    for section in value['sections']:
        if not isinstance(section,dict) or set(section)!={'heading','text','references'}:
            raise ValueError('讲解段落不完整。')
        heading,text,refs=(section[k] for k in ('heading','text','references'))
        if not isinstance(heading,str) or not isinstance(text,str) or not 8<=len(text.strip())<=700:
            raise ValueError('讲解段落过长或为空。')
        if (not isinstance(refs,list) or not 1<=len(refs)<=len(ids)
            or any(not isinstance(r,str) or r not in ids for r in refs) or len(set(refs))!=len(refs)):
            # Keep only complete sections whose entire citation list can be checked.
            missing_predecessor = True
            continue
        if missing_predecessor and re.match(r'^\s*(?:因此|所以|由此|据此|上述|上式|继续代入)',text):
            continue
        if wire.credential_risk([heading,text]) or re.search(r'https?://|www\.',heading+' '+text):
            raise ValueError('讲解使用了额外链接。')
        heading = ' '.join(heading.split()) or ('结论' if not sections else '说明')
        sections.append({'heading':heading, 'text':text, 'references':refs})
    if not sections:
        raise ValueError('讲解引用超出本轮资料。')
    return sections


def answer(store, book, request, *, history=(), capture=None, experiment=None):
    audit=capture if capture is not None else {}
    result=_answer(store,book,request,history=history,capture=audit,experiment=experiment)
    resolution=audit.get('context_resolution')
    if resolution:
        result['model_calls']+=1
        result['context_resolution']={k:resolution[k] for k in ('accepted','resolved_question','evidence') if k in resolution}
    result['question']=request.get('question','')
    if result.get('mode') in ('LIVE_AI','LIVE_AI_GENERAL'):
        client=tutor.read_only_client(store,request)
        raw=knowledge_answer_review.review(client,result,result['question'],history,audit)
        result['model_calls']+=1
        if raw is not None:
            try:
                value=tutor.strict_object(raw)
                if result['mode']=='LIVE_AI_GENERAL' or set(value)=={'intro'}:
                    text=general_tutoring.explanation(value)
                    checked=[{'heading':'讲解','text':text,'references':[]}]
                    result.update(mode='LIVE_AI_GENERAL',status='GENERAL',references=[],direct=[],evidence_ids=[],learning_order=[])
                    result.pop('optional_section',None)
                else:
                    checked=_sections(raw,{a['id'] for a in result['references']})
                    core=[part for part in checked if not _EXTENSION.search(part['heading'])]
                    optional=[part for part in checked if _EXTENSION.search(part['heading'])]
                    result.pop('optional_section',None)
                    if core and optional:
                        result['optional_section']=optional[0]
                    checked=core or checked[:1]
                audit['answer_review']['accepted']=True
                audit['draft_sections']=result['sections']
                result['sections']=checked
            except (ValueError,KeyError,TypeError,RuntimeError):
                audit['answer_review']['message']='Review format not accepted; preserved the draft.'
        result['answer_review']={'accepted':audit['answer_review']['accepted']}
    return result


def _answer(store, book, request, *, history=(), capture=None, experiment=None):
    question=request.get('question','')
    user_question=question
    if not isinstance(question,str) or not 1<=len(question.strip())<=1000 or wire.credential_risk(question):
        raise WorkflowError('请填写光学问题，长度不超过 1000 字。')
    mode=request.get('mode','local')
    if mode not in ('local','live'):raise WorkflowError('请选择本地学习或 AI 讲解。')
    if set(request)-{'question','mode','approve_data','focus','previous','known','catalog_version',
                    'path','lesson','section','selection','reading'}:
        raise WorkflowError('学习问答仅接受知识资料。')
    reading = request.get('reading','auto')
    selection = request.get('selection','')
    if (reading not in ('auto','substitution','step_by_step') or not isinstance(selection,str)
        or len(selection)>240 or wire.credential_risk(selection)):
        raise WorkflowError('请选取当前专题中的一小段文字，或选择讲解方式。')
    selection = ' '.join(selection.split())
    previous,known=request.get('previous',[]),request.get('known',[])
    if any(not isinstance(v,list) or len(v)>8 or any(not isinstance(i,str) for i in v) for v in (previous,known)):
        raise WorkflowError('知识条目编号无效。')
    if (previous or known or request.get('catalog_version')) and request.get('catalog_version')!=book.version:
        raise WorkflowError('此学习记录对应上一版知识库，请从当前目录开启新问题。',409)
    focus=request.get('focus')
    path_id=request.get('path')
    lesson_id=request.get('lesson')
    section_id=request.get('section')
    if (focus is not None and (not isinstance(focus,str) or focus not in book.entries)
        or path_id is not None and (not isinstance(path_id,str) or path_id not in PATHS)
        or lesson_id is not None and (not isinstance(lesson_id,str) or lesson_id not in book.study_topics)
        or section_id is not None and (not isinstance(section_id,str) or lesson_id is None
                                      or section_id not in {part['id'] for part in book.study_topics[lesson_id]['sections']})
        or any(i not in book.entries for i in previous+known)):
        raise WorkflowError('关联条目在当前知识库中不可用。',404)
    if selection:
        part = next((part for part in book.study_topics.get(lesson_id,{}).get('sections',[])
                     if part['id']==section_id),None)
        if not part or not any(selection in ' '.join((part.get(key) or '').split())
                               for key in ('title','text','formula','conditions')):
            raise WorkflowError('所选文字须来自当前专题小节。')
    resumed = next((i for i,turn in enumerate(history) if turn.get('resume')),None)
    if resumed is not None:
        history=history[:resumed+1]
    returning = bool(_RETURN_TOPIC.search(question))
    switching = (knowledge_retrieval.switches_topic(question) or
                 bool(history and knowledge_retrieval.requested_definition(question)
                      and not knowledge_retrieval.is_followup(question)))
    latest_general = bool(history and history[-1]['answer']['status']=='GENERAL')
    followup = bool(history and not switching and
                    (resumed is not None or returning or knowledge_retrieval.is_followup(question) or _FOLLOWUP.search(question)
                     or re.match(r'^(?:只用|给我|用).{0,12}(?:概括|总结|比较)',question)
                     or latest_general and knowledge_retrieval.general_followup(question)
                     or _THIS_SECTION.search(question) or _ASSUMPTION_UPDATE.search(question) and
                     re.search(r'上述|之前|上一|最新|前次|这个|该|刚才|其他|其余|原来|仍|沿用',question)))
    active_history = []
    if followup:
        for turn in reversed(history):
            active_history.append(turn)
            if knowledge_retrieval.switches_topic(turn['question']) or turn['answer']['status']=='GENERAL':
                break
        active_history.reverse()
    # Every turn may refer to earlier user facts without an explicit pronoun.
    # Keep a bounded conversational window separate from retrieval/parameter
    # inheritance, which still requires the existing same-subject checks.
    conversation_turns = []
    if not switching:
        for turn in reversed(history[-6:]):
            conversation_turns.append(turn)
            if (knowledge_retrieval.switches_topic(turn['question']) or
                    knowledge_retrieval.requested_definition(turn['question']) and
                    not knowledge_retrieval.is_followup(turn['question'])):
                break
        conversation_turns.reverse()
    capture['active_user_turns']=[turn['question'] for turn in conversation_turns]
    context_turns=history if returning else active_history
    prior = next((turn['answer'] for turn in reversed(context_turns) if turn['answer']['status']=='FOUND'),{})
    if returning and not request.get('lesson'):
        query = set(tokens(question))
        candidates = [turn['answer'] for turn in reversed(history) if turn['answer'].get('lesson')]
        if candidates:
            prior=max(candidates,key=lambda answer:_match(book.study_topics[answer['lesson']]['title'],query))
    if lesson_id is None and prior.get('lesson'):
        lesson_id = prior['lesson']
        if returning or _THIS_SECTION.search(question):
            section_id = prior.get('section')
            selection = prior.get('selection','')
    lesson = book.study_topics.get(lesson_id)
    same_subject = not request.get('lesson') or not prior or request['lesson']==prior.get('lesson')
    prior_calculation = prior.get('calculation') if followup and same_subject else None
    if followup and same_subject and not prior_calculation:
        # Explanatory turns can continue the most recent calculation in the same subject.
        current_anchors=set(prior.get('direct',[]))
        prior_calculation=next((calc for turn in reversed(context_turns)
                                if (calc:=turn['answer'].get('calculation'))
                                and current_anchors.intersection(calc['references'])),None)
    restricted = bool(general_tutoring.RECORD_REQUEST.search(question))
    if mode=='live' and request.get('approve_data') is True and conversation_turns and not restricted and not selection:
        client=tutor.read_only_client(store,request)
        resolved=knowledge_context.resolve(client,question,conversation_turns,capture)
        if resolved:
            question=resolved
            request=request|{'question':resolved}
            if latest_general and not knowledge_retrieval.requested_definition(user_question):
                followup=True
    calculation = None if restricted else calculate(question,prior_calculation)
    slit_boundary = (not restricted and prior_calculation and prior_calculation['id']=='single-slit'
                     and prior_calculation['inputs'].get('geometry','free_space')=='free_space'
                     and slit_scope_changed(question))
    if calculation and any(ref not in book.entries for ref in calculation['references']):
        calculation = None
    direct=select_articles(book,question,focus,[] if switching or latest_general else previous) if not restricted else []
    if not restricted and conversation_turns:
        contextual = contextual_calculation(question,[turn['question'] for turn in conversation_turns])
        named = set(book.named(question,direct,contextual=True))
        if (contextual and (not named or named.intersection(contextual['references']))
                and (not calculation or calculation['id'] == contextual['id'])):
            calculation = contextual
    contextual_refs = []
    if conversation_turns and not restricted and not (focus or path_id or lesson or calculation):
        # A short continuation often has no new named concept. In that case
        # rank the current subject ahead of incidental words such as "unit",
        # "order", or "measurement", rather than silently changing the subject.
        if not book.named(question,direct,contextual=True) and not knowledge_retrieval.requested_definition(question):
            # Preserve the subjects of a comparison across intervening
            # explanations, not just the citations in the latest reply.
            anchors=list(dict.fromkeys(ref for turn in conversation_turns[-3:]
                         for ref in turn['answer'].get('evidence_ids',[])[:2]))[:5]
            if anchors:
                contextual_refs = anchors
                direct = anchors
    if (latest_general and followup and not (focus or path_id or lesson or calculation or experiment)
            and not book.definition_matches(question,direct)
            and not knowledge_retrieval.requested_definition(question)):
        direct = []
    if prior and not restricted and same_subject:
        previous_ids=prior.get('evidence_ids',prior.get('direct',[]))[:2]
        named=book.named(question,direct,contextual=True)
        keep_subject=returning or not named or bool(set(named).intersection(prior.get('direct',[])))
        if keep_subject or not knowledge_retrieval.requested_definition(question) and re.match(r'^(?:那|所以|这样|它|只用)',question):
            if re.match(r'^(?:那|所以|这样|它|只用)',question) and not calculation and not knowledge_retrieval.requested_definition(question):
                direct=previous_ids+direct[:2]
            top = previous_ids+direct if not named and not calculation else direct[:1]+previous_ids+direct[1:]
            direct=list(dict.fromkeys(top+prior.get('direct',[])))[:6]
    if calculation:
        direct=list(dict.fromkeys(calculation['references']+direct))[:5]
    if slit_boundary:
        direct=list(dict.fromkeys(prior_calculation['references']+['fresnel-vs-fraunhofer-phase']+direct))[:6]
    if path_id:
        direct=[i for i in PATHS[path_id][1] if i in book.entries]
    topic_sections = []
    if lesson and not restricted:
        query = set(tokens(question))
        ranked = sorted(lesson['sections'],key=lambda part:-_match(part['title']+' '+part['text'],query))
        topic_sections = [part for part in ranked if part['id']==section_id] if section_id else ranked[:2]
        focus_refs = [ref for part in topic_sections for ref in part['references']]
        direct = list(dict.fromkeys(focus_refs+direct))[:6]
    experiment_refs=[]
    if experiment and not restricted:
        experiment_refs=[row['id'] for row in experiment['origin']['references'] if row['id'] in book.entries]
        direct=list(dict.fromkeys(experiment_refs+direct))[:6]
    exact=book.definition_matches(question,direct)
    base={'mode':'LOCAL_REFERENCE','question':question,'catalog_version':book.version,'solver_calls':0,'model_calls':0}
    if len(exact)>1 and not focus and not path_id and not lesson and not calculation and not experiment:
        return base|{'status':'AMBIGUOUS','message':'该术语对应多个概念。选择你要了解的方向。','choices':[book.brief(i) for i in exact]}
    if not direct:
        missing = base|{'status':'NO_MATCH','message':'当前知识库未找到足够资料。可以按主题浏览相关章节。','paths':book.overview()['paths']}
        if mode != 'live':
            return missing
        if restricted:
            return missing|{'message':'请先提供设备或实验的记录，再核对具体结果。本轮未发送给 AI。'}
        latest = conversation_turns[-1] if conversation_turns else None
        previous_general = (general_tutoring.context(latest['question'],
                            '\n'.join(part['text'] for part in latest['answer'].get('sections',[])[:2]),
                            [turn['question'] for turn in conversation_turns])
                            if latest else None)
        return _general_answer(store,request,missing,previous_general,capture)
    order=_path(book,direct,set(known))
    related=list(dict.fromkeys(ident for root in direct[:3] for ident in book.entries[root]['related']
                               if ident not in direct))
    ids=list(dict.fromkeys(direct+related[:2]+order))[:8]
    order=[i for i in order if i in ids]
    sections=[{'heading':book.entries[i]['title'],
               'text':book.entries[i]['definition']+'\n'+book.entries[i]['example'],
               'references':[i]} for i in direct if i not in known]
    if topic_sections:
        sections=[{'heading':part['title'],'text':part['text'],
                   'references':[ref for ref in part['references'] if ref in ids]} for part in topic_sections]
    references=[book.article(i) for i in ids]
    reply=base|{'status':'FOUND','sections':sections,'references':references,'learning_order':order,'direct':direct}
    reply['conventions'] = book.conventions_for(direct)
    if calculation:
        reply['calculation']=calculation
        if calculation['id']=='pulse-time-shift':
            reply['active_convention']='合成核 exp('+calculation['inputs']['inverse_sign']+'iΩt)，传递 exp(+iφ)，固定实验室时间轴。'
    elif prior_calculation and prior_calculation['id']=='pulse-time-shift' and re.search(r'相位|时移|延迟|合成',question):
        reply['active_convention']='合成核 exp('+prior_calculation['inputs']['inverse_sign']+'iΩt)，传递 exp(+iφ)，固定实验室时间轴。'
    if prior_calculation and calculation and prior_calculation.get('inputs')!=calculation.get('inputs'):
        reply['previous_calculation']=prior_calculation
    if slit_boundary:
        reply['previous_calculation']=prior_calculation
        reply['message']='最新一次计算采用自由空间远场。当前已撤销远场条件，这个数值只保留作原条件下的参考。'
        reply['sections']=[{
            'heading':'前一次结果的适用条件',
            'text':prior_calculation['result']+' 这是原条件下的解析预测。当前需要与近场传播条件匹配的模型或实测图样，才能得到新的宽度。',
            'references':['slit-scale','fresnel-vs-fraunhofer-phase'],
        }]
    if lesson:
        reply['lesson']=lesson_id
    if section_id:
        reply['section']=section_id
    if selection:
        reply['selection']=selection
    if experiment:
        reply['experiment']=experiment
    # Section and calculation references take precedence. General search results
    # remain visible locally, while the model sees only relevant background.
    primary = list(dict.fromkeys(experiment_refs+(calculation or {}).get('references',[]) + contextual_refs +
                                [ref for part in topic_sections for ref in part['references']]))
    if not primary:
        query = set(tokens(question))
        named = book.named(question,direct,contextual=True)
        primary = list(dict.fromkeys(direct[:2] + named[:3] +
                         [ident for ident in direct[2:] if _match(book.entries[ident]['title'],query)>=2]))[:5]
        if not primary:
            primary = direct[:3]
    reply['evidence_ids']=primary
    if calculation and calculation['id'] in ('shot-noise-scale','fiber-acceptance-angle','fret-efficiency'):
        reply['sections']=[{'heading':'程序核算','text':calculation['result'],
                            'references':calculation['references']}]
        if mode != 'live' or not re.search(r'解释|为什么|为何|区分|讲解|直观|怎么理解', question):
            return reply
    if slit_boundary and re.search(r'只说|仅说明|还适|还能|能否|测量结果',question):
        reply['evidence_ids']=reply['sections'][0]['references']
        return reply
    if mode!='live':return reply
    client=tutor.read_only_client(store,request)
    anchors = set(primary)
    prerequisites = {ref for ident in primary for ref in book.entries[ident]['prerequisites']}
    model_ids = [ident for ident in ids if ident in anchors or ident in prerequisites]
    if focus and focus not in model_ids:
        model_ids.append(focus)
    automatic_depth = ('substitution' if _SKIP.search(user_question) else
                       'step_by_step' if _EXPAND.search(user_question) else 'focused')
    if reading=='auto' and _INTUITIVE.search(user_question):
        automatic_depth='intuitive'
    if reading=='auto' and re.search(r'(?:一|两三|两|三|[123])\s*句(?:话)?|只(?:回答|要|说).{0,12}(?:单位|结果|结论)',user_question):
        automatic_depth='brief'
    response_plan = {'direct':primary, 'depth':automatic_depth if reading=='auto' else reading,
                     'optional_extension':False}
    prompt=('你是 PhotonGuard 光学学习助教。基于本轮资料作答。允许展示代入、量纲换算和推导。'
            '有程序计算时，沿用其结果、每一步的单位和假设。不要声称做过仿真。'
            '历史用于理解指代和用户给定条件，物理结论以本轮资料为据。'
            '第一段直接回答，随后只写完成本题所需的推导与条件。一般一至三段。'
            '如果有 experiment，结合其中原学习问题、已核对的条件与实际结果作答。明确区分预测、解析指标、数值图样、教学数据和实测，'
            '只报告给定候选与数据。可以比较保存的来源与当前条件。切勿把从模型得出的量说成图中测量值。'
            'selected_text 只是当前小节中的原句或公式，优先解释其中的符号及用户指定的一步。'
            '遵守 response_plan 的深度和主要依据。substitution 直接代入，step_by_step 展示中间步骤。'
            '给出问题要求的关键中间步骤；区分严格模型、测量拟合、条件未知三种情况。'
            '涉及公式，沿用资料的符号和适用条件。区分归一化的指标与给定条件下的积分量。'
            '涉及 conventions 中的不同约定，先交代换算或符号映射；保持来源自身的符号。'
            '问符号时分清观测量与坐标、全差与半差、输入归一与输出再归一。'
            'active_convention 是本轮仍沿用的约定，优先于资料中的默认约定。'
            '已知范围外的结论请指出缺哪一项物理假设或数据。'
            '对已撤销条件的数值，若需要引用，只引用同一学习对象最近一次更新后的计算；更早数值只在比较历史时使用。'
            '分清修改采样点密度、扩大实际计算域与改变物理输入；单纯补零或裁切显示不能作为周期伪影的充分判据。'
            '比较物理量的大小，只回答资料能推出的关系。相速度与群速度的大小关系单独不能确定二阶色散符号。'
            '推导或比较多个量时，先确认分子和分母、方向词及数字，避免首句与代入相互矛盾。'
            '省略旁支应用和未要求的延伸，即使资料包含它们。依据不足时只说明缺少什么。'
            '输出 JSON 对象，唯一顶层字段 sections，数组含一到五段。每段有 heading、text、references。'
            'references 是本轮直接支持该段的条目 id 数组，每段至少一条，去除重复。'
            '正文不写链接，不声称引用之外的结论已核对。'
            '逐段复核首句、公式和数值是否一致。有登记计算时，首句必须与 result 一致，'
            '当 calculation.id 为 qubit-basis-measurement 时，直接沿用登记的概率和状态，'
            '明确分开条件态和忽略结果后的状态，勿将上一轮的基底套入本轮。'
            '不用另猜其他定义。涉及不同物理量时，优先采用最贴近题目的直接依据，'
            '跳过只在一般词语上相似的资料。references 仅复制 articles 的 id，不要把约定编号当条目。')
    prompt += ('intuitive 时只写一段日常例子和与问题有关的结论，省略公式和扩展。'
               '公式可用双美元符号包住；公式内用可读 Unicode 或基础 TeX，JSON 字符串里的反斜线必须转义为两个。'
               '散粒噪声的相对量是 i_rms/I，信噪幅度比是 I/i_rms。固定带宽下两者变化方向相反。'
               'Talbot 半个未平移复现距离与半周期平移是两个量。'
               'FRET 转移速率与 r 的负六次方成正比，效率 E=1/[1+(r/R0)^6]；'
               '效率随距离增加而下降，但不能说在所有距离上越远下降越快。'
               '用户要求两三句话时，只解释本轮两点和它们的差别，省略扩展例子。')
    prompt += ('recent 是同会话的上下文，不代表当前问题一定沿用旧主题。先根据本轮要求判断对象；'
               '简短追问、单位修正、比较总结可沿用用户之前给定的条件，不要要求用户重复已给资料。'
               'current question 明确换主题时不继承旧参数；新问题出现新术语也优先回答新对象。'
               '用户原话中的对象与参数优先于历史 AI 的转述；只改第二台的条件时，第一台保持原来的条件。'
               '登记计算的 inputs 只覆盖列明对象，未列出的设备不能复制同一组数值。'
               '用户只纠正单位或概括比较时，仍回答原来的物理量和对象，不转向资料中其他量。'
               '用户参数与条目示例不同，必须用用户参数；条目中的示例数值不能代替本轮条件。'
               '只选直接支持本题的资料；即使其他条目也出现在 articles，也不要附带无关的术语区别。')
    prompt += ('本轮文章是可选依据，不是必须逐篇讲解的清单。每段都必须回答用户明确提出的问题；'
               '用户仅要求数值、单位、确认定义或简短比较时，一段给出结论和必要条件即可。'
               '不要用“若日后”“本题不涉及”引出未问的内容，也不反复解释已确认定义。'
               '示例装置、常见近似和一般定理的必要条件必须区分：不能把示例条件升级为普遍要求。'
               '存在不同语境的同名量时，沿用用户明确语境；没有依据时指出该区别，不自行选择另一语境。')
    prompt += ('immediate_previous 是紧接本轮的问答。用户只要求缩短、改讲法、去公式或总结比较时，'
               '改写这次问答的结论和已更新数字，不回退到最初定义，也不改变比较对象。'
               '正文以一至两段完成直接答案；只有用户明确要求推导或分步时才增加段落。'
               '一般背景和相似词条不自动支持本题，引用须支持实际结论，不靠引用旧AI答复代替依据。')
    prompt += ('question 是用户当前问题，resolved_context 只把必要前文补全以供理解；'
               '只回答 question，不能把历史计算请求全部重答。保持最新条件和用户指定的操作时序。')
    prompt += '可用引用编号只有以下几项，请逐字复制：' + '、'.join(model_ids) + '。'
    if response_plan['depth']=='brief':
        prompt += ('本轮明确要求简短：sections 只写一个元素，text 按用户要求写一到三句话。'
                   '先回答当前结论与关键数字，再给一句原因或区别；代入过程和公式由页面的展开依据提供，正文不展开推导，不举新例子。')
    recent = [{'question':turn['question'],
               'answer':[part['text'][:240] for part in turn['answer'].get('sections',[])[:2]],
               'calculation':turn['answer'].get('calculation')} for turn in conversation_turns]
    immediate_previous = ({'question':conversation_turns[-1]['question'],
                           'answer':[part['text'] for part in conversation_turns[-1]['answer'].get('sections',[])[:2]]}
                          if conversation_turns else None)
    payload=json_payload(prompt,{'question':user_question,'resolved_context':question if question!=user_question else None,'focus':focus,'previous':previous,'known':known,
                                'response_plan':response_plan,'selected_text':selection,
                                'conventions':book.conventions_for(model_ids),
                                'learning_order':[ident for ident in order if ident in model_ids],
                                'articles':list(_evidence(book,model_ids,question)),
                                'recent':recent,'immediate_previous':immediate_previous,'calculation':calculation,'topic_sections':topic_sections,
                                'previous_calculation':prior_calculation,'active_convention':reply.get('active_convention'),
                                **({'experiment':experiment} if experiment else {})})
    try:
        raw=client.complete('knowledge-'+uuid4().hex,payload)
        if capture is not None:
            capture['assistant'] = raw.get('assistant')
            capture['transport'] = {key:raw.get('audit',{}).get(key)
                                    for key in ('status','live_dispatch','provider_model','request_sha256','response_sha256')}
        explained=_sections(raw,set(model_ids))
        if any(re.search(r'相对(?:噪声|波动|标准差)(?:为|是|写作|定义为|\s|[=：:（(\\$])*I\s*[/／]\s*i[_ᵣr]',part['text'])
               for part in explained) and 'photodiode-shot-noise' in model_ids:
            raise ValueError('散粒噪声比值方向不符。')
        live=raw.get('audit',{}).get('live_dispatch') is True
        core = [part for part in explained if not _EXTENSION.search(part['heading'])]
        optional = [part for part in explained if _EXTENSION.search(part['heading'])]
        result = reply|{'mode':'LIVE_AI' if live else 'INJECTED_AI','model_calls':1,
                        'sections':core or explained[:1]}
        if len(explained) < len(tutor.strict_object(raw)['sections']):
            result['message']='部分段落的引用编号无法对应本轮资料。'
        if core and optional:
            result['optional_section'] = optional[0]
        if capture is not None:
            capture['presentation_adjusted'] = explained != tutor.strict_object(raw)['sections'] or bool(optional)
        return result
    except WorkflowError as exc:
        return reply|{'mode':'LOCAL_AFTER_AI_REVIEW','model_calls':1,
                      'message':str(exc)+' 下方显示本地学习依据。'}
    except (ValueError,KeyError,TypeError,RuntimeError):
        if capture is not None:
            capture['application_status']='LOCAL_AFTER_AI_REVIEW'
        return reply|{'mode':'LOCAL_AFTER_AI_REVIEW','model_calls':1,
                      'message':'AI 本次回复未通过依据核对，下方显示本地学习依据。'}


def _general_answer(store, request, missing, previous, capture):
    client = tutor.read_only_client(store, request)
    try:
        raw = client.complete('knowledge-' + uuid4().hex, general_tutoring.payload(missing['question'], previous))
        if capture is not None:
            capture['transport'] = {key:raw.get('audit',{}).get(key)
                                    for key in ('status','live_dispatch','provider_model','request_sha256','response_sha256')}
        text = general_tutoring.explanation(tutor.strict_object(raw))
        live = raw.get('audit',{}).get('live_dispatch') is True
        return missing|{'status':'GENERAL', 'mode':'LIVE_AI_GENERAL' if live else 'INJECTED_AI_GENERAL',
                        'model_calls':1, 'message':'暂无匹配的本地出处。',
                        'continued_general':bool(previous),
                        'sections':[{'heading':'讲解', 'text':text, 'references':[]}],
                        'references':[], 'learning_order':[], 'direct':[]}
    except WorkflowError as exc:
        message = str(exc)
    except (ValueError,KeyError,TypeError,RuntimeError):
        message = 'AI 本次未返回可显示的讲解，可以重新提问。'
    if capture is not None:
        capture['application_status']='GENERAL_AFTER_AI_FAILURE'
    return missing|{'mode':'GENERAL_AFTER_AI_FAILURE','model_calls':1,'message':message}
