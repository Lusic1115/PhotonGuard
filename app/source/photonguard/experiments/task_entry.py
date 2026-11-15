"""Small, explicit goal/data/teaching router, not a new workflow engine."""
import re
from ..core import digest
from ..workflow import WorkflowError
from . import inverse_models as m


def route(text,mode=None):
    if mode not in (None,'forward','inverse'):raise WorkflowError('请选择比较已知参数或从观测反推')
    inverse=bool(re.search(r'反演|反推|拟合|CSV|csv|已有数据|测量数据|观测数据|补测|补采|未知.{0,8}(?:缝宽|膜厚|束腰)',text))
    forward=bool(re.search(r'已知参数|正向|比较.{0,8}(?:镜片|透镜|焦距|设计)|设计目标',text))
    choice=mode or ('inverse' if inverse and not forward else 'forward' if forward and not inverse else None)
    found=[model for pattern,model in ((r'束腰|高斯|光束',m.GAUSSIAN),(r'薄膜|膜厚|反射率',m.FILM),(r'双缝|缝宽|缝距|干涉',m.SLIT)) if re.search(pattern,text)]
    return {'mode':choice,'model':found[0] if len(found)==1 else None,'source':'PROGRAM_ROUTING_NOT_AI',
            'reason':'根据明确的任务词判断；没有判断参数值或补充物理事实。'}


def data_followups(record):
    """Compatible analysis entry points for each checked forward candidate."""
    if record['status'] != 'COMPLETED' or not record.get('result'):
        return {}
    models = {}
    for row in record['result']['candidates']:
        if row.get('all_checks_passed') is not True:
            continue
        p = row.get('parameters') or {}
        if record['domain'] == 'gaussian':
            models[row['id']] = m.GAUSSIAN
        elif record['domain'] == 'diffraction' and p.get('aperture') == 'double_slit':
            models[row['id']] = m.SLIT
        elif record['domain'] == 'film' and len(p.get('layers', [])) == 1:
            models[row['id']] = m.FILM
    return models


def _data_source(store, data):
    origin = data['from_result']
    if not isinstance(origin, dict) or set(origin) != {'record_id', 'result_sha256', 'candidate_id'} or not all(
            isinstance(value, str) for value in origin.values()):
        raise WorkflowError('请明确选择要接续的保存结果与候选。')
    if data.get('mode') != 'inverse' or data.get('csv_text') is None or data.get('teaching'):
        raise WorkflowError('接续结果时请导入一份观测文件。')
    parent = store.verified_case(origin['record_id'])
    if not parent.get('result') or digest(parent['result']) != origin['result_sha256']:
        raise WorkflowError('来源结果已变化，请打开保存的来源版本。', 409)
    model = data_followups(parent).get(origin['candidate_id'])
    if model is None or data.get('model') != model:
        raise WorkflowError('所选候选与当前数据分析模型不匹配。', 409)
    return parent


def start(store,data):
    from .observation_group import create
    from ..workflow_model import wire
    csv_fields={'csv_text','source_kind','source_name','license','position_unit','diameter_conversion','csv_mapping','csv_preview_sha256'}
    allowed={'text','mode','model','teaching','from_result'}|csv_fields
    if not isinstance(data,dict) or set(data)-allowed:raise WorkflowError('开始任务的字段无效')
    text=data.get('text','')
    if not isinstance(text,str) or len(text)>6000 or wire.credential_risk(text):raise WorkflowError('请填写不含密钥的目标（最多6000字）')
    if 'teaching' in data and type(data['teaching']) is not bool:raise WorkflowError('教学模式需明确选择')
    parent = _data_source(store, data) if 'from_result' in data else None
    if data.get('teaching'):
        if data.get('model') not in m.MODELS or data.get('csv_text') is not None:raise WorkflowError('请明确选择教学任务；真实数据不能自动替换为教学对象')
        s=create(store,{'model':data['model'],'teaching':True});decision={'mode':'inverse','model':data['model'],'source':'EXPLICIT_TEACHING_SELECTION'}
    else:
        decision=route(text,data.get('mode'))
        from .builtin_examples import match
        example=match(text) if data.get('mode') in (None,'forward') and data.get('csv_text') is None else None
        if example:
            decision.update(mode='forward',model=None,case_id=example['case_id'],source=example['source'],example=example,
                            reason='采用与完整原文一致的内置单缝示例；整理计划后仍需核对并批准计算。')
        elif data.get('csv_text') is None and data.get('mode') in (None,'forward') and decision['mode'] != 'inverse':
            from .slit_goal import recognizes_goal
            if recognizes_goal(text):
                return {'status':'SLIT_GOAL', 'text':text, 'route':{'mode':'forward','model':'single_slit'}}
        if data.get('csv_text') is not None:
            if data.get('mode')=='forward':raise WorkflowError('数据反向分析与正向比较分开开始')
            decision['mode']='inverse'
        if decision['mode'] is None:
            return {'status':'NEEDS_INTENT','question':'这次是比较已知参数的方案，还是从已有观测反推未知参数？','text':text,'route':decision}
        if decision['mode']=='inverse':
            model=data.get('model') or decision['model']
            if data.get('csv_text') is None:return {'status':'NEEDS_DATA','text':text,'route':decision,'model':model,'question':'导入已有观测；未知物理条件会留待核对。'}
            if model not in m.MODELS:raise WorkflowError('请明确数据对应的光学场景')
            s=create(store,{k:v for k,v in data.items() if k in csv_fields}|{'model':model})
        else:s=store.create({'kind':'specified','text':text})
    s['task_goal_text']=text;s['task_entry']={'route':decision,'teaching_explicit':data.get('teaching') is True}
    if text:s['text']=text
    if parent:
        s.update(parent_id=parent['id'], family_id=parent['family_id'])
        s['task_entry']['from_result'] = data['from_result']
    store.save(s)
    if parent:
        store.event(s,'DATA_CONTINUED_FROM_RESULT',data['from_result'])
    return {'status':'CREATED','case':store.public_record(s),'route':decision,'model_calls':0}
