import copy
import json
import pytest
from test_inverse_journeys import store,act
from photonguard.core import digest
from photonguard.workflow import WorkflowError
from photonguard.experiments import advice_contract as c,research_advice as a,research_actions as ra,observation_group as ow,inverse_models as m


def record(store):
    s=ow.create(store,{'model':m.GAUSSIAN,'teaching':True});s['acquisition_constraints']={'points':81};store.save(s);return s


def update(value=71,quote='71个点',**kw):return {'id':'U1','field':'design.points','value':value,'quote':quote,'correction':False,**kw}


def receive(store,s,obj,text='补采71个点'):
    ctx=a.capture(store,s,text)
    return a.receive(store,s,text,{'assistant':{'content':json.dumps(obj)}},ctx)


def test_contract_example_and_bad_wrapper():
    c.check(c.SCHEMA,c.public_contract()['valid_example'])
    for v in ({'response':c.fixture()}, {'updates':[],'focus':'overview','suggestions':'array'}):
        with pytest.raises(ValueError):c.check(c.SCHEMA,v,envelope=True)


@pytest.mark.parametrize('bad',[
    {'evidence_refs':['nonexistent']}, {'correction':False}, {'proposal_id':'fake'},
    {'kind':'run_solver'}, {'reason':'已经确诊，真实参数为20'}, {'approve':True}])
def test_valid_update_survives_independent_bad_suggestion(store,bad):
    s=record(store);obj=c.fixture();obj['updates']=[update()]
    other=c.fixture('refit')['suggestions'][0];other.update(bad);obj['suggestions'].append(other)
    out=receive(store,s,obj)
    assert out['acquisition_constraints']['points']==71
    assert out['inverse_ai']['validation_status']=='PARTIAL_VALID'
    assert [v['type'] for v in out['research_actions']['actions']]==['explain']
    assert ra.current(out) and out['inverse_ledger']['fit_runs_reserved']==0


def test_bad_patch_keeps_readonly_and_program_blocks_refit_without_dependency(store):
    s=record(store);obj=c.fixture();obj['updates']=[update(99)]
    obj['suggestions']+=c.fixture('refit')['suggestions']
    out=receive(store,s,obj)
    assert out['acquisition_constraints']['points']==81
    assert out['inverse_ai']['validation_status']=='FACT_PATCH_REJECTED'
    read,fit=out['research_actions']['actions']
    assert read['status']=='SUGGESTED_ONLY' and fit['status']=='NEEDS_REVIEW'
    assert 'FACT_PATCH_REJECTED' in fit['required_before_execution']
    with pytest.raises(WorkflowError):act(store,out,'inverse_research_prepare',action_id=fit['id'],suggestion_sha256=out['research_actions']['sha256'])


def test_historical_evidence_valid_but_not_current_fact_authority(store):
    s=record(store);s['parameter_sources']['old_question']='补采71个点';store.save(s)
    obj=c.fixture(evidence='E1');obj['updates']=[update()]
    out=receive(store,s,obj,'请解释已有资料')
    assert out['inverse_ai']['validation_status']=='FACT_PATCH_REJECTED'
    assert out['research_actions']['actions'][0]['evidence_refs']==['E1']
    assert out['acquisition_constraints']['points']==81


def test_stale_response_does_not_replace_latest_or_mark_accepted(store):
    s=record(store);ctx=a.capture(store,s,'解释')
    current=store.get(s['id']);current['research_goal']={'target':'waist_position','source':'USER_FORM'};store.save(current)
    out=a.receive(store,s,'解释',{'assistant':{'content':json.dumps(c.fixture())}},ctx)
    assert out==current and not out.get('inverse_ai')
    receipt=json.loads((store.folder(s['id'])/'ai-requests'/ctx['request_id']/'receipt.json').read_text())
    assert receipt['validation_status']=='STALE_CONTEXT' and not receipt['applied']


@pytest.mark.parametrize('raw',['not JSON','```json\n{}\n```','{"response":{"updates":[]}}'])
def test_invalid_json_not_salvaged(store,raw):
    s=record(store);ctx=a.capture(store,s,'补采71个点')
    out=a.receive(store,s,'补采71个点',{'assistant':{'content':raw}},ctx)
    assert out['inverse_ai']['validation_status']=='PROTOCOL_INVALID'
    assert out['acquisition_constraints']['points']==81 and not out.get('research_actions')


def test_request_uses_exact_shared_schema_and_model_never_copies_binding(store,monkeypatch):
    from photonguard.experiments.inverse_report import guidance
    s=record(store);sent=[]
    class Client:
        def complete(self,ident,payload):sent.append(payload);return {'assistant':{'content':json.dumps(c.fixture())}}
    monkeypatch.setattr(store,'_model',lambda *args:Client())
    out=guidance(store,s,{'text':'解释已有资料','approve_data':True})
    assert out['inverse_ai']['validation_status']=='ALL_VALID'
    request=next((store.folder(s['id'])/'ai-requests').glob('*/public-input.json'))
    public=json.loads(request.read_text());assert public['schema']==c.SCHEMA
    assert 'research_input_binding' not in public['record']
    assert public['valid_example']==c.fixture() and sent


def test_goal_draft_invalidates_plan_and_recomputes_dependencies(store):
    s=record(store);p={'plans':[{'id':'baseline'}],'baseline_id':'baseline'};p['sha256']=digest(p);s['measurement_plan']=p;store.save(s)
    obj=c.fixture('choose_baseline',proposal='baseline');obj['updates']=[update('waist_position','束腰位置更重要',field='goal.target')]
    out=receive(store,s,obj,'束腰位置更重要')
    assert out['research_goal']['target']=='waist_position' and not out.get('measurement_plan')
    assert out['measurement_history']==[p]
    assert out['research_actions']['actions'][0]['status']=='STALE_PROPOSAL'


def test_valid_physical_patch_new_unapproved_draft_and_atomic_role_conflict(store):
    s=record(store);obj=c.fixture('refit');obj['updates']=[update(632.,'波长632 nm',field='fixed.wavelength_nm',correction=True)]
    out=receive(store,s,obj,'更正波长632 nm')
    assert out['inverse_settings']['fixed']['wavelength_nm']==632.
    assert not any(g['approved'] for g in out['groups']) and out['status']=='DRAFT'
    assert 'FACTS_NEED_REVIEW' in out['research_actions']['actions'][0]['required_before_execution']
    obj=c.fixture();obj['updates']=[update(80.,'束腰半径80微米',field='fixed.waist_radius_um',correction=True),update([20.,180.],'束腰半径20到180微米',id='U2',field='bounds.waist_radius_um',correction=True)]
    out2=receive(store,out,obj,'更正束腰半径80微米，束腰半径20到180微米')
    assert out2['inverse_ai']['validation_status']=='FACT_PATCH_REJECTED'
    assert out2['inverse_settings']==out['inverse_settings']


def test_exhaustion_and_asserted_calibration_never_create_execution_or_kernel(store):
    s=record(store);s['inverse_ledger']['fit_runs_reserved']=4;store.save(s)
    out=receive(store,s,c.fixture('refit'),'再拟合')
    assert 'ROOT_BUDGET_EXHAUSTED' in out['research_actions']['actions'][0]['required_before_execution']
    obj=c.fixture('request_calibration');obj['updates']=[update(True,'已经校准',field='observation.calibration')]
    out=receive(store,out,obj,'已经校准')
    assert out['inverse_ai']['validation_status']=='FACT_PATCH_REJECTED'
    assert not out.get('contrast_calibration') and out['groups'][0]['observation']=={'kind':'POINT_SAMPLE'}


@pytest.mark.parametrize('wording',['请解释数据','帮我看看已有结果，辛苦了','根据现有记录说明一下'])
def test_reason_wording_does_not_authorize_changes(store,wording):
    s=record(store);before=copy.deepcopy(s['inverse_settings']);out=receive(store,s,c.fixture(),wording)
    assert out['inverse_settings']==before and out['inverse_ai']['validation_status']=='ALL_VALID'


def test_explicit_read_only_request_rejects_a_model_plan_and_fact_change(store):
    s=record(store)
    obj=c.fixture('explain')
    obj['updates']=[update()]
    proposed=c.fixture('compare_acquisition')['suggestions'][0]
    proposed['id']='S2'
    obj['suggestions'].append(proposed)
    obj['primary_suggestion_id']='S2'
    text='只解释上次为何补采71个点，不要准备新计划。'
    out=receive(store,s,obj,text)
    assert out['acquisition_constraints']==s['acquisition_constraints']=={'points':81}
    assert [item['type'] for item in out['research_actions']['actions']]==['explain']
    assert out['research_actions']['primary_action_id'] is None
    assert not out.get('task_plan') and out['inverse_ledger']['fit_runs_reserved']==0
    local=ra.fallback(out,text,ledger=out['inverse_ledger'])
    assert [item['type'] for item in local['actions']]==['explain']


@pytest.mark.parametrize('scope,text,accepted', [
    (None, '以前的可用波长目录是704、831 nm，现在是751、863 nm。帮我回顾这个选择的原因，所有资料保持原样，暂时不做操作。', False),
    ('PLAN', '以前的可用波长目录是704、831 nm，现在是751、863 nm。回顾这次选择的原因。', False),
    ('PLAN', '上次把可用波长目录改为704、831 nm。帮我回顾这个选择。', False),
    ('EXPLAIN', '现在把可用波长目录改为704、831 nm。', False),
    ('PLAN', '现在把可用波长目录从751、863 nm改为704、831 nm。', True),
])
def test_replacing_the_active_catalog_requires_scope_and_current_authorization(store, scope, text, accepted, monkeypatch):
    monkeypatch.setattr('secrets.randbits',lambda bits:0)
    s=ow.create(store,{'model':m.FILM,'teaching':True})
    current={'wavelengths_nm':[751,863]}
    measurement={'plans':[{'id':'wavelength-751'}]};measurement['sha256']=digest(measurement)
    task={'sha256':'current-task'}
    s.update(acquisition_constraints=current,measurement_plan=measurement,task_plan=task)
    store.save(s)
    ledger=copy.deepcopy(s['inverse_ledger'])
    obj=c.fixture();obj['updates']=[update([704,831],'704、831 nm',field='design.wavelengths_nm')]
    ctx=a.capture(store,s,text,scope)
    out=a.receive(store,s,'现在把可用波长目录改为704、831 nm。',{'assistant':{'content':json.dumps(obj)}},ctx)
    assert out['inverse_ledger']==ledger
    if accepted:
        assert out['acquisition_constraints']['wavelengths_nm']==[704,831]
        assert 'measurement_plan' not in out and 'task_plan' not in out
        assert out['measurement_history'][-1]==measurement and out['task_plan_history'][-1]==task
    else:
        assert out['acquisition_constraints']==current
        assert out['measurement_plan']==measurement and out['task_plan']==task
        assert out['inverse_ai']['validation_status']=='FACT_PATCH_REJECTED'


def test_planning_without_execution_accepts_a_plan_suggestion(store):
    s=record(store)
    text='不要执行，但可以先准备计划给我看。'
    out=receive(store,s,c.fixture('compare_acquisition'),text)
    assert out['inverse_ai']['request_scope']=='PLAN'
    assert out['research_actions']['actions'][0]['type']=='compare_acquisition'
    assert not out['research_actions']['executed'] and not out.get('measurement_plan')
    assert out['inverse_ledger']==s['inverse_ledger']


def test_model_step_prepare_read_only_is_auditable_and_does_not_fit(store):
    s=receive(store,record(store),c.fixture())
    b=s['research_actions'];out=act(store,s,'inverse_research_prepare',suggestion_sha256=b['sha256'],action_id=b['actions'][0]['id'])
    assert out['research_origin']['status']=='READ_ONLY_REVIEWED' and not out.get('inverse_plan')
    assert out['inverse_ledger']['fit_runs_reserved']==0


def test_suggestion_can_reference_same_declared_preferences_as_its_updates(store):
    s=record(store);obj=c.fixture('compare_acquisition');obj['updates']=[update('BASELINE_FIRST','基础方案优先',field='preference.mode')]
    obj['suggestions'][0].update(fields=['preference.mode'],depends_on=['U1'])
    out=receive(store,s,obj,'基础方案优先')
    assert out['inverse_ai']['validation_status']=='ALL_VALID'
    assert out['research_actions']['actions'][0]['fact_fields']==['preference.mode']
    assert out['decision_preferences']['mode']=='BASELINE_FIRST'
