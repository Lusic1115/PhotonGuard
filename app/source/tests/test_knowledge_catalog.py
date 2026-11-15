"""Acceptance for independent offline study and versioned content packs."""
import json
from pathlib import Path
from types import SimpleNamespace
from threading import Thread
from urllib.parse import urlencode

import pytest

from photonguard.experiments.knowledge_catalog import Catalog, builtin_packs, catalog
from photonguard.experiments.knowledge_learning import answer
from photonguard.experiments.knowledge_pack import update
from photonguard.workflow import WorkflowError, make_server
from validation.finals_evidence import Workbench


CASES = json.loads(Path(__file__).with_name('knowledge_acceptance_cases.json').read_text(encoding='utf-8'))


@pytest.fixture
def book(tmp_path):
    return catalog(tmp_path)


@pytest.mark.parametrize('question,expected',CASES['search'])
def test_first_five_include_all_target_articles(book,question,expected):
    ids = {article['id'] for article in book.search(question,limit=5)['items']}
    assert set(expected) <= ids


@pytest.mark.parametrize('question,expected',CASES['lessons'])
def test_cross_concept_study_returns_all_relevant_evidence(book,question,expected):
    reply = answer(None,book,{'question':question})
    assert reply['status']=='FOUND'
    assert set(expected) <= set(reply['direct'])
    order=reply['learning_order']
    articles={article['id']:article for article in reply['references']}
    assert set(order) <= set(articles)
    for ident in order:
        for prerequisite in articles[ident]['prerequisites']:
            if prerequisite in order:
                assert order.index(prerequisite) < order.index(ident)
    assert all(set(part['references']) <= set(articles) for part in reply['sections'])
    assert reply['solver_calls']==reply['model_calls']==0


@pytest.mark.parametrize('question',CASES['unsupported'])
def test_no_match_does_not_invent_support(book,question):
    assert answer(None,book,{'question':question})['status']=='NO_MATCH'


def test_catalog_facets_pagination_and_formula(book):
    first=book.search(topic='nonlinear',capability='FORMULA_EXAMPLE',limit=3)
    second=book.search(topic='nonlinear',capability='FORMULA_EXAMPLE',limit=3,page=2)
    assert first['total']==second['total']>3
    assert second['items']
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}
    assert all(item['topic']=='nonlinear' and item['has_formula'] for item in first['items'])
    article=book.article('quasi-phase-matching')
    assert 'Δk_eff' in article['formula'] and article['variables']
    assert article['sources'][0]['fingerprint'].startswith('sha256:')
    assert article['experiment'] is None
    assert book.article('slit-scale')['experiment']['url']=='/experiments#library=D01'


def test_public_knowledge_search_url_preserves_query_and_filters(tmp_path):
    with make_server(tmp_path,port=0,allow_workers=False) as server:
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            api=Workbench(server)
            query=urlencode({'q':'光纤数值孔径','topic':'guided','page':1})
            result=api.api('/knowledge/search?'+query)
            assert result['items'][0]['id']=='fiber-numerical-aperture'
            assert all(item['topic']=='guided' for item in result['items'])
            with pytest.raises(RuntimeError,match='HTTP 400'):
                api.api('/knowledge/search?page=1&page=2')
            with pytest.raises(RuntimeError,match='HTTP 400'):
                api.api('/knowledge/overview?page=1')
        finally:
            server.shutdown()
            thread.join()


def test_ambiguity_and_followup_are_explicit(book):
    result=answer(None,book,{'question':'NA'})
    assert result['status']=='AMBIGUOUS'
    assert {item['id'] for item in result['choices']}=={'objective-na','fiber-numerical-aperture'}
    first=answer(None,book,{'question':'什么是束腰'})
    follow=answer(None,book,{'question':'那它的公式呢？','previous':first['direct'],'catalog_version':book.version})
    assert follow['direct']==['gaussian-waist']
    with pytest.raises(WorkflowError):
        answer(None,book,{'question':'那它的公式呢？','previous':first['direct'],'catalog_version':'old'})


def test_known_prerequisite_skipped_and_live_requires_consent(book):
    question={'question':'我想理解空间滤波','path':'fourier-filtering','known':['spatial-frequency'],
              'catalog_version':book.version}
    result=answer(None,book,question)
    assert 'spatial-frequency' not in result['learning_order']
    assert 'spatial-frequency' not in [section['references'][0] for section in result['sections']]
    with pytest.raises(WorkflowError,match='同意'):
        answer(SimpleNamespace(),book,question|{'mode':'live'})


def test_bad_model_citation_returns_local_evidence(book):
    class Client:
        def preflight(self):pass
        def complete(self,*args):
            return {'assistant':{'content':json.dumps({'sections':[{'heading':'内容','text':'一段超过八个字符的内容',
                        'references':['absent-id']}]})},'audit':{'live_dispatch':True}}

    store=SimpleNamespace(owner=SimpleNamespace(),client=Client())
    result=answer(store,book,{'question':'什么是束腰','mode':'live','approve_data':True})
    assert result['mode']=='LOCAL_AFTER_AI_REVIEW' and result['solver_calls']==0
    assert result['direct']==['gaussian-waist']


def test_valid_sections_survive_an_unverifiable_citation(book):
    class Client:
        def preflight(self):
            pass

        def complete(self, *_):
            return {'assistant':{'content':json.dumps({'sections':[
                {'heading':'错误引用','text':'这一段的来源编号没有登记。','references':['made-up-id']},
                {'heading':'直观解释','text':'沿传播方向寻找半径最小的地方。','references':['gaussian-waist']},
            ]})},'audit':{'live_dispatch':True}}

    store=SimpleNamespace(owner=SimpleNamespace(),client=Client())
    answer_with_sources=answer(store,book,{'question':'什么是束腰','mode':'live','approve_data':True})
    assert answer_with_sources['mode']=='LIVE_AI'
    assert [section['heading'] for section in answer_with_sources['sections']]==['直观解释']
    assert '引用编号' in answer_with_sources['message']


def test_install_update_and_withdraw_local_pack(tmp_path):
    base=catalog(tmp_path)
    core=next(pack for pack in builtin_packs() if pack['id']=='optics-core')
    entry=core['entries'][0]|{'id':'training-polarization','title':'偏振补充练习',
                              'terms':['偏振补充练习'],'prerequisites':['polarization']}
    incoming=tmp_path/'incoming.json'
    pack={'id':'practice-pack','version':'1.0.0','sources':core['sources'],
          'entries':[entry,entry|{'id':'draft-practice','title':'未发布偏振练习','status':'DRAFT'}]}
    def write():
        incoming.write_text(json.dumps(pack,ensure_ascii=False),encoding='utf-8')
    write()
    proof=update(tmp_path,incoming=incoming)
    loaded=catalog(tmp_path)
    assert loaded.version==proof['catalog_version']!=base.version
    assert loaded.search('偏振补充练习',limit=1)['items'][0]['id']==entry['id']
    assert 'draft-practice' not in loaded.entries
    pack['entries'][0]['example']+='校订。'
    write()
    with pytest.raises(ValueError,match='新'):
        update(tmp_path,incoming=incoming)
    pack['version']='1.0.1'
    pack['entries'][0]['related']=['missing']
    write()
    with pytest.raises(ValueError,match='关系'):
        update(tmp_path,incoming=incoming)
    assert catalog(tmp_path).version==loaded.version
    update(tmp_path,remove='practice-pack')
    assert catalog(tmp_path).version==base.version
    assert loaded.article(entry['id'])['version']=='1.0.0'


def test_builtin_content_can_upgrade_and_restore_without_updating_app(tmp_path):
    base=catalog(tmp_path)
    pack=next(pack for pack in builtin_packs() if pack['id']=='optics-core')
    pack['version']='9.0.0'
    incoming=tmp_path/'updated-core.json'
    incoming.write_text(json.dumps(pack,ensure_ascii=False),encoding='utf-8')
    update(tmp_path,incoming=incoming)
    upgraded=catalog(tmp_path)
    assert upgraded.article('polarization')['version']=='9.0.0'
    assert upgraded.article('gaussian-waist')['version']==base.article('gaussian-waist')['version']
    update(tmp_path,remove='optics-core')
    assert catalog(tmp_path).version==base.version
