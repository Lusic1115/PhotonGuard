"""Known-index, normal-incidence lossless single film; finite thickness modes via tmm."""
import csv
import hashlib
import io
import json
import math
import time
from ..core import digest

MODEL='single_film_known_index_v1'
PHYSICS=('film_index','incident_index','substrate_index','incidence_deg','lossless','layer_order','calibrated_reflectance')


def parse_csv(text,*,source_kind,source_name,license_name='',**unused):
    from .observations import SOURCE_KINDS,MAX_BYTES
    from ..workflow_model import wire
    if not isinstance(text,str) or len(text.encode())>MAX_BYTES or wire.credential_risk([text,source_name,license_name]):raise ValueError('CSV 内容无效')
    if source_kind not in SOURCE_KINDS or not isinstance(source_name,str) or not 1<=len(source_name.strip())<=300:raise ValueError('请声明数据来源')
    if not isinstance(license_name,str) or len(license_name)>300 or source_kind=='LICENSED_PUBLIC' and not license_name.strip():raise ValueError('公开资料需要许可')
    rows=list(csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True))
    if not rows or rows[0]!=['wavelength_nm','reflectance_calibrated'] or not 1<=len(rows)-1<=61:raise ValueError('需 wavelength_nm,reflectance_calibrated，1–61 行已校准反射率；任意亮度不能当反射率')
    values=[]
    for row in rows[1:]:
        if len(row)!=2:raise ValueError('只接受两个明确列')
        try:x,y=map(float,row)
        except (ValueError,TypeError):raise ValueError('波长和反射率须为数值') from None
        if not math.isfinite(x+y) or not 500<=x<=2000 or not 0<=y<=1:raise ValueError('波长 500–2000 nm，已校准反射率 0–1')
        values.append((x,y))
    x=[v[0] for v in values]
    if any(b<=a for a,b in zip(x,x[1:])):raise ValueError('同一 CSV 波长须严格递增；未清洗数据')
    ds={'schema':'photonguard.reflectance-observations.v1','raw_sha256':hashlib.sha256(text.encode()).hexdigest(),'rows':len(x),'columns':rows[0],
        'x_nm':x,'reflectance':[v[1] for v in values],'source_kind':source_kind,'source_name':source_name.strip(),'license':license_name or 'USER_DECLARED_LOCAL_USE','questions':[],
        'cleaning':'NONE','coordinate_unit':'nm','data_kind':'CALIBRATED_REFLECTANCE_NOT_ARBITRARY_BRIGHTNESS'}
    ds['sha256']=digest(ds);return ds


def defaults():
    return {'model':MODEL,'fixed':{'film_index':2.,'incident_index':1.,'substrate_index':1.,'incidence_deg':0.,'lossless':True,'layer_order':'incident/film/substrate','calibrated_reflectance':True},
        'bounds':{'thickness_nm':[0.,1000.]},'loss':'linear','f_scale':1.,'registration':'SHARED_REGISTERED','weighting':'EQUAL_DATASET_TRAINING_COUNT','scale':.002,'scale_source':'DECLARED_NUMERICAL_SCALE'}


def validate(groups,cfg):
    from .inverse_models import finite
    from .observation_contract import validate_observation
    if set(cfg)!=set(defaults()) or cfg['model']!=MODEL or set(cfg['fixed'])!=set(PHYSICS) or set(cfg['bounds'])!={'thickness_nm'}:raise ValueError('薄膜只允许估计厚度；折射率、层序及校准条件须已知')
    f=cfg['fixed'];b=cfg['bounds']['thickness_nm']
    if any(not finite(f[k]) or not 1<=f[k]<=4 for k in ('film_index','incident_index','substrate_index')) or not finite(f['incidence_deg']) or f['incidence_deg']!=0 or f['lossless'] is not True or f['calibrated_reflectance'] is not True or f['layer_order']!='incident/film/substrate':raise ValueError('仅支持法向入射、已知实折射率、无损单层及已校准反射率')
    if not isinstance(b,list) or len(b)!=2 or not all(finite(v) for v in b) or not 0<=b[0]<b[1]<=2000 or b[1]-b[0]>1000:raise ValueError('厚度为 0–2000 nm，有限搜索跨度最多 1000 nm')
    if cfg['loss']!='linear' or cfg['f_scale']!=1 or cfg['registration']!='SHARED_REGISTERED' or cfg['weighting']!='EQUAL_DATASET_TRAINING_COUNT':raise ValueError('本薄膜入口使用声明尺度的普通平方残差和等数据组权重')
    if not isinstance(groups,list) or not 1<=len(groups)<=3:raise ValueError('最多三组薄膜观测')
    for g in groups:
        validate_observation(g,MODEL)
        ds=g['dataset']
        if ds.get('schema')!='photonguard.reflectance-observations.v1' or digest({k:v for k,v in ds.items() if k!='sha256'})!=ds['sha256']:raise ValueError('反射率数据或哈希无效')
        if not 1<=ds['rows']<=61 or len(ds['x_nm'])!=ds['rows'] or len(ds['reflectance'])!=ds['rows'] or any(not finite(v) or not 500<=v<=2000 for v in ds['x_nm']) or any(not finite(v) or not 0<=v<=1 for v in ds['reflectance']) or any(b<=a for a,b in zip(ds['x_nm'],ds['x_nm'][1:])):raise ValueError('反射率数组不合法')
        if g.get('conditions')!=f or g.get('approved') is not True or not finite(g.get('scale')) or not 1e-9<=g['scale']<=1e6 or g.get('scale_source') not in ('DECLARED_NUMERICAL_SCALE','KNOWN_MEASUREMENT_SD'):raise ValueError('请分别批准每组已知条件和残差尺度')
    return True


def plan(groups,cfg,comparison=None):
    from .inverse_models import LIMITS, finite
    import copy
    import re
    validate(groups,cfg)
    p={'schema':'photonguard.film-inverse-plan.v1','model':MODEL,'operation':'fit','groups':copy.deepcopy(groups),'settings':copy.deepcopy(cfg),'budget':dict(LIMITS['joint' if len(groups)>1 else 'fit']),
       'protocol':{'grid_points':241,'local_intervals_max':48,'local_maxiter':80,'relative_cost_allowance':.1,'absolute_cost_floor':1e-8},
       'profile':None,'splits':[{'holdout_indices':[],'note':'小型波长组全部拟合，没有独立验证；T=1-R 不计作第二份证据'} for _ in groups],
       'parameter_roles':{'thickness_nm':'MODEL_ESTIMATE'},'reference_sha256':None}
    if comparison is not None:
        required={'record_id','fit_result_id','group_count','modes'}
        if (not isinstance(comparison,dict) or set(comparison)!=required
                or not re.fullmatch(r'[a-f0-9]{32}',str(comparison['record_id']))
                or not re.fullmatch(r'[a-f0-9]{64}',str(comparison['fit_result_id']))
                or type(comparison['group_count']) is not int or not 1<=comparison['group_count']<len(groups)
                or not isinstance(comparison['modes'],list) or not 1<=len(comparison['modes'])<=50):
            raise ValueError('补测前候选的来源无效')
        lo,hi=cfg['bounds']['thickness_nm']
        for mode in comparison['modes']:
            if not isinstance(mode,dict) or set(mode)!={'thickness_nm','interval_nm'}:
                raise ValueError('补测前候选字段无效')
            value, interval = mode['thickness_nm'], mode['interval_nm']
            if (not finite(value) or not lo<=value<=hi or not isinstance(interval,list) or len(interval)!=2
                    or any(not finite(x) for x in interval) or not lo<=interval[0]<=value<=interval[1]<=hi):
                raise ValueError('补测前候选范围无效')
        p['comparison']=copy.deepcopy(comparison)
    p['sha256']=digest(p);return p


def predict(wavelengths_nm,thickness_nm,fixed):
    import numpy as np
    from tmm import coh_tmm
    return np.asarray([coh_tmm('s',[fixed['incident_index'],fixed['film_index'],fixed['substrate_index']],[np.inf,thickness_nm,np.inf],0,float(w))['R'] for w in wavelengths_nm])


def _observation_impact(comparison, groups, fixed, modes, flags):
    prefix=comparison['group_count']
    old_waves={w for group in groups[:prefix] for w in group['dataset']['x_nm']}
    observations=[]
    for group in groups[prefix:]:
        data=group['dataset']
        observations.extend({'wavelength_nm':w,'reflectance':value,'dataset_sha256':data['sha256'],
                             'source_kind':data['source_kind']}
                            for w,value in zip(data['x_nm'],data['reflectance']))
    if len(observations)>12:
        indices=[round(i*(len(observations)-1)/11) for i in range(12)]
        selected=[observations[i] for i in indices]
    else:
        selected=observations
    waves=[point['wavelength_nm'] for point in selected]
    fitted=[row['parameters']['thickness_nm'] for row in modes]
    incomplete=bool({'UNRESOLVED_FILM_INTERVALS','NO_CONVERGENCE'} & set(flags))
    rows=[]
    for mode in comparison['modes']:
        lo,hi=mode['interval_nm']
        matched=[thickness for thickness in fitted if lo-1e-4<=thickness<=hi+1e-4]
        status='MATCHED_JOINT_INTERVAL' if matched else 'INCOMPLETE' if incomplete else 'NO_JOINT_MODE_IN_INTERVAL'
        predictions=predict(waves,mode['thickness_nm'],fixed).tolist()
        rows.append({**mode,'predicted_reflectance':predictions,'matching_joint_modes_nm':matched,'status':status})
    for i,point in enumerate(selected):
        values=[row['predicted_reflectance'][i] for row in rows]
        point['prior_prediction_range']=[min(values),max(values)]
        point['repeats_prior_wavelength']=point['wavelength_nm'] in old_waves
    return {'source':{key:comparison[key] for key in ('record_id','fit_result_id')},
            'prior_mode_count':len(rows),'joint_mode_count':len(fitted),'joint_modes_nm':fitted,
            'new_point_count':len(observations),'points':selected,'prior_modes':rows,
            'same_wavelength_only':all(point['wavelength_nm'] in old_waves for point in observations),
            'possible_mismatch':'POSSIBLE_MODEL_MISMATCH' in flags,'incomplete_search':incomplete,
            'tmm_evaluations':len(rows)*len(selected)}


def run(p,progress=None):
    import numpy as np
    from scipy.optimize import minimize_scalar
    groups=p['groups'];cfg=p['settings'];protocol=p['protocol'];began=time.monotonic();calls=0;tmm_calls=0;starts=[];gridrows=[]
    comparison=p.get('comparison')
    if p!=plan(groups,cfg,comparison):raise ValueError('薄膜计划被修改')
    class Stop(Exception):pass
    def cost(t):
        nonlocal calls,tmm_calls
        if calls>=p['budget']['max_residual_calls'] or time.monotonic()-began>=p['budget']['seconds']-1:raise Stop()
        calls+=1;total=0.
        for g in groups:
            ds=g['dataset'];r=(predict(ds['x_nm'],t,cfg['fixed'])-ds['reflectance'])/g['scale'];tmm_calls+=ds['rows'];total+=float(np.mean(r*r))/2
        return total
    lo,hi=cfg['bounds']['thickness_nm'];grid=np.linspace(lo,hi,protocol['grid_points']);stopped=False
    for t in grid:
        try:gridrows.append({'value':float(t),'cost':cost(t),'success':True})
        except Stop:gridrows.append({'value':float(t),'cost':None,'success':False});stopped=True
    intervals=[i for i in range(1,len(grid)-1) if all(gridrows[j]['success'] for j in (i-1,i,i+1)) and gridrows[i]['cost']<=min(gridrows[i-1]['cost'],gridrows[i+1]['cost'])]
    for index in intervals[:protocol['local_intervals_max']]:
        try:
            r=minimize_scalar(cost,bounds=(grid[index-1],grid[index+1]),method='bounded',options={'maxiter':protocol['local_maxiter'],'xatol':1e-8})
            starts.append({'success':bool(r.success),'cost':float(r.fun),'parameters':{'thickness_nm':float(r.x)},'interval':[float(grid[index-1]),float(grid[index+1])]})
        except Stop:starts.append({'success':False,'status':'RESOURCE_STOP','interval':[float(grid[index-1]),float(grid[index+1])]});stopped=True
        if progress:progress({'starts':starts,'actual_residual_calls':calls,'plan_sha256':p['sha256']})
    candidates=[r for r in starts if r['success']]+[{'success':True,'cost':r['cost'],'parameters':{'thickness_nm':r['value']},'boundary':True} for r in (gridrows[0],gridrows[-1]) if r['success']]
    candidates.sort(key=lambda r:r['cost']);best=candidates[0] if candidates else None;minimum=best['cost'] if best else None
    modes=[]
    if best:
        for r in candidates:
            if r['cost']-minimum<=max(minimum*.1,1e-8) and all(abs(r['parameters']['thickness_nm']-v['parameters']['thickness_nm'])>1e-4 for v in modes):modes.append(r)
    flags=[]
    if stopped or len(intervals)>48 or any(not r['success'] for r in starts):flags.append('UNRESOLVED_FILM_INTERVALS')
    if len(modes)>1:flags.append('MULTIPLE_THICKNESS_MODES')
    if len({w for g in groups for w in g['dataset']['x_nm']})==1:flags.append('SINGLE_WAVELENGTH_PERIODIC_AMBIGUITY')
    if best and best.get('boundary'):flags.append('BOUNDARY_HIT')
    if not best:flags.append('NO_CONVERGENCE')
    data=[]
    if best:
        t=best['parameters']['thickness_nm']
        for g in groups:
            ds=g['dataset'];prediction=predict(ds['x_nm'],t,cfg['fixed']);tmm_calls+=ds['rows'];err=np.asarray(ds['reflectance'])-prediction;rmse=float(np.sqrt(np.mean(err*err)))
            data.append({'x':ds['x_nm'],'x_unit':'nm','observed':ds['reflectance'],'predicted':prediction.tolist(),'residual':err.tolist(),'holdout':[False]*ds['rows'],'training_rmse':rmse,'holdout_rmse':None,'dataset_sha256':ds['sha256']})
            if rmse>max(.02,3*g['scale']):flags.append('POSSIBLE_MODEL_MISMATCH')
    impact=None
    if comparison and time.monotonic()-began<p['budget']['seconds']-2:
        impact=_observation_impact(comparison,groups,cfg['fixed'],modes,flags)
        tmm_calls+=impact['tmm_evaluations']
    result={'schema':'photonguard.film-inverse-result.v1','operation':'fit','model':MODEL,'plan_sha256':p['sha256'],'source_kind':'MODEL_ESTIMATE',
       'status':'NEEDS_REVIEW' if flags else 'FINITE_THICKNESS_ESTIMATE','usable_for_forward':False,'warnings':sorted(set(flags)),
       'estimated_parameters':best['parameters'] if best else None,'best_cost':minimum,'modes':modes,'grid':gridrows,'starts':starts,'data':data,'confidence_interval':None,
       'diagnostics':{'finite_intervals':len(intervals),'period_nm_at_first_wavelength':groups[0]['dataset']['x_nm'][0]/(2*cfg['fixed']['film_index'])},
       'resources':{'actual_residual_calls':calls,'local_optimizations':len(starts),'tmm_evaluations':tmm_calls,'elapsed_seconds':time.monotonic()-began,'model_calls':0,'fft_calls':0,'fdtd_calls':0},
       'limitations':['已知实折射率、法向入射、无损单层，只有厚度待估','有限网格与局部区间；未证明全局唯一，单波长周期歧义保留','所有反射率用于拟合；没有独立验证或置信区间','不反演 n、吸收、增益、背景；T=1-R 不构成独立观测']}
    if comparison:
        result['observation_impact']=impact
    result['id']=digest(result);return result


def propose(groups,cfg,fit,constraints):
    import numpy as np
    from .inverse_models import finite
    validate(groups,cfg)
    if not isinstance(constraints,dict) or set(constraints)!={'wavelengths_nm'} or not isinstance(constraints['wavelengths_nm'],list) or not 2<=len(constraints['wavelengths_nm'])<=9 or any(not finite(w) or not 500<=w<=2000 for w in constraints['wavelengths_nm']) or len(set(constraints['wavelengths_nm']))!=len(constraints['wavelengths_nm']):raise ValueError('请给出 2–9 个不同且可采集的候选波长（500–2000 nm）')
    modes=fit.get('modes',[])
    if not modes:raise ValueError('先完成有限厚度分析')
    plans=[]
    for w in constraints['wavelengths_nm']:
        curves=[float(predict([w],r['parameters']['thickness_nm'],cfg['fixed'])[0]) for r in modes]
        score=float(np.var(curves)) if len(curves)>1 else 0.
        plans.append({'id':'wavelength-'+format(w,'.8g'),'wavelengths_nm':[w],'points':1,'score':score,'prediction_envelope':{'min':min(curves),'max':max(curves)},'source_kind':'PREDICTION_NOT_OBSERVATION','conditions':cfg['fixed'],'cost':{'wavelength_settings':1,'observations':1},'risks':['单一保留模式不能证明唯一'] if len(modes)==1 else []})
    best=max(plans,key=lambda r:r['score']);result={'schema':'photonguard.film-acquisition.v1','constraints':constraints,'plans':plans,'recommended_id':best['id'],'fit_result_id':fit['id'],
        'recommendation_kind':'FINITE_MODE_DISCRIMINATION' if len(modes)>1 else 'FIXED_BASELINE_NOT_ADAPTIVE','candidate_count':len(modes),'score_definition':'有限厚度候选在可用波长处的已校准反射率分歧；无真值输入',
        'limitations':['候选谱差异不是保证；实际补测反射率后需联合重算','单波长厚度周期 λ/(2n)，反射率还可出现对称分支','不把 T=1-R 当第二份独立证据'],
        'resources':{'tmm_evaluations':len(modes)*len(plans),'model_calls':0,'fdtd_calls':0}}
    result['sha256']=digest(result);return result


def teaching_create(private_root,ident):
    from pathlib import Path
    import secrets
    import numpy as np
    from ..workflow import atomic_json
    seed=secrets.randbits(32);obj={'model':MODEL,'seed':seed,'thickness_nm':float(np.random.default_rng(seed).uniform(180,350)),'noise_sd':.001,'film_index':2.}
    folder=Path(private_root)/ident;folder.mkdir(parents=True,exist_ok=False);atomic_json(folder/'object.json',obj)
    return {'commitment_sha256':digest(obj),'source_kind':'SYNTHETIC_TEACHING','note':'独立冻结单层膜对象，独立 Airy 公式生成合成反射率'}


def teaching_acquire(private_root,ident,commitment,index,wavelengths):
    from pathlib import Path
    import numpy as np
    from .inverse_models import finite
    if type(index) is not int or not 0<=index<=2 or not 1<=len(wavelengths)<=61 or any(not finite(w) or not 500<=w<=2000 for w in wavelengths):raise ValueError('薄膜教学补采额度或波长无效')
    obj=json.loads((Path(private_root)/ident/'object.json').read_text())
    if digest(obj)!=commitment:raise ValueError('教学对象承诺改变')
    n=obj['film_index'];r=((n-1)/(n+1))**2;phase=2*np.pi*n*obj['thickness_nm']/np.asarray(wavelengths)
    y=4*r*np.sin(phase)**2/((1-r)**2+4*r*np.sin(phase)**2)+np.random.default_rng(obj['seed']+index*1009).normal(0,obj['noise_sd'],len(wavelengths))
    if np.any((y<0)|(y>1)):raise ValueError('教学含噪反射率越界，保留失败，不截断或重试')
    out=io.StringIO();np.savetxt(out,np.c_[wavelengths,y],delimiter=',',header='wavelength_nm,reflectance_calibrated',comments='');return out.getvalue()
