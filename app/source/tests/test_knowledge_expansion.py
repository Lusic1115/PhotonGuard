"""Regression coverage for the larger offline catalog and its study entry points."""
import pytest

from photonguard.experiments.knowledge_catalog import builtin_packs, catalog
from photonguard.experiments.knowledge_learning import answer


TOPIC_PACKS = {'fiber-integrated-optics':'guided','optical-measurement':'measurement',
               'quantum-foundations':'quantum'}


@pytest.fixture
def book(tmp_path):
    return catalog(tmp_path)


def test_three_topic_packs_have_complete_published_entries(book):
    packs={pack['id']:pack for pack in builtin_packs()}
    for ident,topic in TOPIC_PACKS.items():
        pack=packs[ident]
        assert len(pack['entries'])>=75
        for article in pack['entries']:
            assert article['status']=='PUBLISHED'
            assert article['topic']==topic
            assert len(article['body'])>=65
            assert article['definition'] and article['conditions'] and article['example']
            assert article['sources']
            for key in article['sources']:
                source=pack['sources'][key]
                assert source['url'].startswith('https://')
                assert source['fingerprint'].startswith('sha256:')
                assert source['section'] and source['rights']=='REFERENCE_ONLY'
    assert book.overview()['published']==len(book.entries)


@pytest.mark.parametrize('question,ident',[
    ('光纤截断法','fiber-cutback'),
    ('模场直径 MFD','fiber-mode-field-diameter'),
    ('OTDR 表观增益','otdr-gainer'),
    ('微环临界耦合','microring-critical-coupling'),
    ('半波电压长度积','modulator-vpi-l'),
    ('噪声等效功率 NEP','detector-nep'),
    ('PRNU','camera-prnu'),
    ('A类评定','measurement-type-a'),
    ('密度矩阵','quantum-density-operator'),
    ('POVM','quantum-povm'),
    ('Fock态','quantum-number-state'),
    ('HOM 干涉','quantum-hom-interference'),
])
def test_topic_terms_are_retrievable(book,question,ident):
    assert ident in [item['id'] for item in book.search(question,limit=5)['items']]


def test_pack_filter_combines_with_search_and_pagination(book):
    first=book.search(pack='quantum-foundations',topic='quantum',limit=15)
    second=book.search(pack='quantum-foundations',topic='quantum',limit=15,page=2)
    assert first['total']==second['total']>=75
    assert {item['id'] for item in first['items']}.isdisjoint(item['id'] for item in second['items'])
    assert all(item['pack']=='quantum-foundations' for item in first['items'])
    filtered=book.search('密度矩阵',pack='fiber-integrated-optics')['items']
    assert all(item['pack']=='fiber-integrated-optics' for item in filtered)
    assert 'quantum-density-operator' not in {item['id'] for item in filtered}


def test_plain_language_question_uses_versioned_grounding(book):
    result=answer(None,book,{'question':'OTDR 是怎么从回波时间算出接头位置的？'})
    assert result['status']=='FOUND'
    assert {'otdr','otdr-group-index-distance'}<=set(result['direct'])
    assert result['catalog_version']==book.version
    assert result['solver_calls']==result['model_calls']==0


@pytest.mark.parametrize('question,expected',[
    ('反向加一点电压，光电二极管为什么通常能测得更快？',
     {'photodiode-photoconductive-mode','photodiode-junction-capacitance'}),
    ('A类是随机误差，B类就是系统误差，这么记对不对？',
     {'measurement-type-a','measurement-type-b'}),
    ('按 W/√Hz 给的噪声底能拿来直接对比两个不同带宽的光电接收器吗？',
     {'detector-nep','detector-noise-bandwidth'}),
])
def test_short_units_and_qualifiers_choose_relevant_evidence(book,question,expected):
    result=answer(None,book,{'question':question})
    assert expected <= {item['id'] for item in result['references']}


def test_specific_device_record_requires_actual_measurement_data(book):
    result=answer(None,book,{'question':'给我确认这台编号 A78 的硅光芯片标定数据和探测器序列号'})
    assert result['status']=='NO_MATCH'
