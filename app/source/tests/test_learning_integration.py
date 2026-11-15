"""Formula results must reach an explicitly requested explanation, without a solve."""
import json
from types import SimpleNamespace

from photonguard.experiments import knowledge_learning, tutor
from photonguard.experiments.knowledge_catalog import catalog
import pytest


def test_current_fret_values_reach_explanation_but_local_mode_stays_offline(tmp_path, monkeypatch):
    sent=[]
    def complete(ident, payload):
        sent.append(payload)
        return {'assistant':{'content':json.dumps({'sections':[{
            'heading':'这两个距离', 'text':'在固定条件下，较远的第二点转移效率更低。',
            'references':['forster-energy-transfer']} ]})}}
    monkeypatch.setattr(tutor,'read_only_client',lambda *args: SimpleNamespace(complete=complete))
    book=catalog(tmp_path)
    question='FRET：供体、受体、取向和环境固定，归一化距离 r/R₀ 从 0.8 改到 1.3。请用两三句话解释当前效率比较，并区分转移速率与效率。'
    local=knowledge_learning.answer(None,book,{'question':question,'mode':'local'})
    assert local['calculation']['inputs']=={'ratio_a':.8,'ratio_b':1.3}
    assert local['model_calls']==0 and not sent
    explained=knowledge_learning.answer(None,book,{'question':question,'mode':'live','approve_data':True})
    assert explained['mode']=='INJECTED_AI' and explained['model_calls']==1
    assert explained['calculation']==local['calculation'] and explained['solver_calls']==0
    assert len(sent)==1 and '79.23%' in str(sent[0]) and '17.16%' in str(sent[0])
    numeric=knowledge_learning.answer(None,book,{'question':'FRET归一化距离从0.8改到1.3，效率差多少？','mode':'live'})
    assert numeric['model_calls']==0 and len(sent)==1


@pytest.mark.parametrize('joiner',['、','和','，'])
def test_width_slots_follow_each_local_label(joiner):
    slot=r'\[\[([a-z0-9_]+)\]\]'
    proper='原全宽 [[horizontal_full_width_before]]'+joiner+'单侧 [[horizontal_first_zero_before]]'
    assert not tutor._reference_conflict(proper,slot)
    wrong='原全宽 [[horizontal_full_width_before]]'+joiner+'单侧 [[horizontal_full_width_before]]'
    assert tutor._reference_conflict(wrong,slot)
    assert tutor._reference_conflict('单侧暗纹从 [[horizontal_first_zero_before]] 变到 [[horizontal_full_width_after]]',slot)
