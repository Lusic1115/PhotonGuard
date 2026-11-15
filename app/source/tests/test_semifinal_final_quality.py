import math
import json
from types import SimpleNamespace
import pytest
from photonguard.experiments.knowledge_math import calculate
from photonguard.experiments.knowledge_catalog import catalog
from photonguard.experiments.knowledge_learning import select_articles

@pytest.mark.parametrize('source,target,expected',[
 ('1 mW/nm','mW/THz',8.013877387),
 ('1000 μW/nm','W/Hz',8.013877387e-15),
 ('1 W/μm','mW/THz',8.013877387),
 ('1 mW/nm','mW/GHz',.008013877387),
 ('8.013877387 mW/THz','mW/nm',1),
])
def test_density_unit_conversion_conserves_local_power(source,target,expected):
 r=calculate(f'真空波长1550 nm处，谱密度{source}换成{target}多少？')
 assert r['id']=='spectral-density-jacobian'
 assert r['inputs']['converted_value']==pytest.approx(expected,rel=1e-9)

def test_density_jacobian_and_frequency_gap_are_reciprocal():
 d=calculate('1550 nm处1 mW/nm谱密度换成mW/THz')
 gap=calculate('1550纳米附近，1纳米的波长间隔换成频率间隔。')
 assert d['inputs']['converted_value']*gap['inputs']['slope_ghz_per_nm']/1000==pytest.approx(1)
 assert gap['id']=='wavelength-local-gap'

@pytest.mark.parametrize('q',[
 '1 mW/nm换成mW/THz',
 '1550 nm处谱密度如何换频率轴？',
 '1550 nm处-1 mW/nm换mW/THz',
 '1550 nm和780 nm处1 mW/nm换mW/THz',
 '1550 nm处1 mW/nm和2 mW/nm分别换成mW/THz',
])
def test_incomplete_density_input_never_falls_into_frequency_only_tool(q):
 assert calculate(q) is None

@pytest.mark.parametrize('q',[
 '两帧同步漂移，差图方差18 DN²，单帧多少？',
 '相关帧的差分方差18 DN²能除二吗？',
 '两帧有协方差，差图方差18 DN²。',
])
def test_correlated_camera_frames_require_covariance(q):
 assert calculate(q) is None

def test_cars_retrieves_actual_mechanism_source(tmp_path):
 b=catalog(tmp_path)
 assert 'nonlinear-polarization-expansion' in select_articles(b,'CARS的非共振背景是什么？')
 e=b.entries['nonlinear-polarization-expansion']
 assert '电子非共振' in e['body'] and '干涉项' in e['body']
 assert any('pub_id=936436' in source['url'] for source in e['sources'])

def test_camera_article_separates_average_image_from_time_statistics(tmp_path):
 e=catalog(tmp_path).entries['camera-temporal-spatial-noise']
 assert '只有平均图无法恢复' in e['body'] and 'Cov(A,B)' in e['body']

@pytest.mark.parametrize('failure',['timeout','truncated'])
def test_incomplete_review_keeps_draft_and_unaccepted_status(tmp_path,failure):
 from photonguard.experiments.knowledge_learning import answer
 class Client:
  def preflight(self):pass
  def complete(self,ident,payload):
   if ident.startswith('knowledge-review-'):
    if failure=='timeout':raise RuntimeError('temporary service failure')
    return {'assistant':{'content':'{"sections":['},'audit':{'live_dispatch':True}}
   body=json.loads(payload['messages'][1]['content'])
   return {'assistant':{'content':json.dumps({'sections':[{'heading':'讲解','text':'光纤的导光与折射率结构和传播模式有关，可结合本轮资料阅读。','references':[body['articles'][0]['id']]}]})},'audit':{'live_dispatch':True}}
 result=answer(SimpleNamespace(owner=SimpleNamespace(),client=Client()),catalog(tmp_path),{'question':'光纤为什么能导光？','mode':'live','approve_data':True})
 assert result['answer_review']['accepted'] is False
 assert '光纤的导光' in result['sections'][0]['text']
