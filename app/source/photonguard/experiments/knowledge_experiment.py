"""Connect an existing study turn to an approval-bound experiment."""
from ..core import digest
from ..workflow import WorkflowError
from . import knowledge_sessions, slit_goal, tutor


SLIT_REFS = {'slit-scale','slit-width-definitions','diffraction-basics'}


def origin(store, session, turn, *, domain):
    answer = knowledge_sessions.saved_turn(store,session,turn)
    relevant = SLIT_REFS if domain=='diffraction' else {'film-ambiguity','film-retained-modes','film-reflectance-input'}
    used = [item for item in answer.get('references',[]) if item['id'] in relevant]
    if answer['status']!='FOUND' or not used:
        raise WorkflowError('请从相关的知识问答开启这项实验。',409)
    return {'session_id':session,'turn_id':turn,'question':answer['question'],
            'catalog_version':answer['catalog_version'],
            'references':[{key:item[key] for key in ('id','title','pack','version')} for item in used],
            'prediction':answer.get('calculation')}, answer


def _slit_input(answer, teaching):
    calculation=answer.get('calculation') or {}
    inputs=calculation.get('inputs',{})
    if calculation.get('id')=='single-slit' and inputs.get('geometry','free_space')=='free_space':
        raise WorkflowError('学习记录采用自由空间远场。请先明确薄透镜焦平面与焦距，再准备该验证。',409)
    text=answer['question']
    values={}
    if calculation.get('id')=='single-slit' and inputs.get('geometry')=='focal_plane':
        parsed=slit_goal.parse_goal(text)
        wave=inputs['wavelength_m']
        width=inputs['width_m']
        focal=inputs['distance_m']
        candidates={'wavelength_nm':wave*1e9,'focal_length_mm':focal*1e3}
        if not {'min_um','max_um','catalog_um'}.intersection(parsed['values']):
            candidates['catalog_um']=[width*1e6]
        if 'target_mm' not in parsed['values']:
            full='全宽' in text or '两侧暗纹' in text
            candidates.update(metric='central_zero_to_zero_width_mm' if full else 'first_zero_mm',
                              target_mm=wave*focal/width*1e3*(2 if full else 1),tolerance_mm=0)
        values={key:value for key,value in candidates.items()
                if key not in parsed['values'] and key not in parsed['conflicts']}
    # The saved registered calculation read an explicit single width, so its
    # one-member directory can disambiguate the intake's unlabelled width clause.
    confirmed=['catalog_um'] if 'catalog_um' in values else []
    return {'text':text,'values':values,'confirmed_fields':confirmed,'teaching':teaching}


def slit(store, data):
    if set(data)-{'session_id','turn_id','teaching','preview_sha256'} or type(data.get('teaching')) is not bool:
        raise WorkflowError('请明确选择单缝验证所用的资料。')
    with store.lock:
        trace, answer=origin(store,data.get('session_id'),data.get('turn_id'),domain='diffraction')
        body=_slit_input(answer,data['teaching'])
        preview=slit_goal.prepare_goal(body)
        if preview['status']!='PREVIEW':
            return preview
        token=digest([trace['session_id'],trace['turn_id'],preview['preview_sha256']])
        if 'preview_sha256' not in data:
            return preview | {'preview_sha256':token,'study_origin':trace}
        if data['preview_sha256']!=token:
            raise WorkflowError('请核对本轮学习条件并重新预览。',409)
        for path in store.root.glob('*/case.json'):
            old=store.get(path.parent.name)
            if old.get('study_origin',{}).get('handoff_sha256')==token:
                return {'status':'CREATED','case':store.public_record(old)}
        return slit_goal.save_goal(store,body | {'preview_sha256':preview['preview_sha256']},
                                   study_origin=trace | {'handoff_sha256':token})


def slit_form(store, binding):
    if not isinstance(binding,dict) or set(binding)!={'session_id','turn_id'}:
        raise WorkflowError('请选择本轮单缝学习记录。')
    return origin(store,binding['session_id'],binding['turn_id'],domain='diffraction')


def result_context(store, session_id, record_id):
    record=store.get(record_id)
    trace=store.study_origin(record)
    if not trace or trace['session_id']!=session_id or record['status']!='COMPLETED':
        raise WorkflowError('请选择从本段学习接续的已完成实验。',409)
    context=tutor.read_result(store,record_id)
    if context['source']['kind']=='FORWARD_RESULT':
        detail={key:context[key] for key in ('context_kind','goal','axes','selected_candidate_id','candidates')}
    else:
        fields=('data_kind','retained_modes','observed_wavelengths_nm','next_wavelengths_nm',
                'thickness_bounds_nm','acquisition_comparison')
        detail={key:value for key,value in context['facts'].items() if key in fields}
    reference=None
    parent_id=record.get('parent_id')
    if parent_id and (record.get('slit_followup') or record.get('learning_comparison')):
        comparison=tutor.read_result(store,parent_id)
        reference={key:comparison[key] for key in ('source','selected_candidate_id','candidates','axes')}
    return {'origin':trace,'source':context['source'],'title':record['title'],'domain':context['domain'],
            'status':context['status'],'model':(record.get('plan') or {}).get('assumptions',[]),
            'detail':detail,'comparison':reference,
            'prediction':record.get('slit_followup') or record.get('learning_comparison')}
