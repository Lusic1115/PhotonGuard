import hashlib

import pytest

from photonguard.experiments import csv_intake, observation_group, service
from photonguard.experiments.inverse_models import FILM, GAUSSIAN, SLIT
from photonguard.workflow import WorkflowError, WorkflowStore


def test_gaussian_mapping_keeps_bom_crlf_and_revises_only_the_analysis(tmp_path):
    text = '\ufeffscan (um),width (um),sigma (um),note\r\n0,130,.8,seed\r\n1000,132,.8,seed\r\n2000,134,.8,seed\r\n'
    mapping = {'x':0,'y':1,'x_unit':'um','y_unit':'um','sigma':2,'sigma_unit':'um','width_kind':'radius','meaning_confirmed':True}
    args = {'csv_text':text,'model':GAUSSIAN}
    first = csv_intake.preview({**args,'mapping':mapping})['mapped']
    owner = WorkflowStore(tmp_path)
    try:
        store = service.store_for(owner)
        body = {**args,'source_kind':'SYNTHETIC_TEACHING','source_name':'synthetic validation',
                'csv_mapping':mapping,'csv_preview_sha256':first['sha256']}
        with pytest.raises(WorkflowError, match='重新预览'):
            observation_group.create(store,{**body,'csv_preview_sha256':'0'*64})
        parent = observation_group.create(store,body)
        assert parent['inverse_settings']['fixed'] == {}
        revised = {**mapping,'width_kind':'diameter'}
        second = csv_intake.preview({**args,'mapping':revised})['mapped']
        request = {'type':'inverse_remap','expected_revision':parent['revision'],'group_index':0,
                   'approve':True,'csv_mapping':revised,'csv_preview_sha256':second['sha256']}
        child = store.action(parent['id'],request)
        assert store.action(parent['id'],request)['id'] == child['id']
        assert child['groups'][0]['dataset']['radius_um'] == [65,66,67]
        assert child['groups'][0]['dataset']['sd_um'] == [.4,.4,.4]
        assert child['groups'][0]['approved'] is False and child['parent_id'] == parent['id']
        assert store.get(parent['id'])['groups'][0]['dataset']['radius_um'] == [130,132,134]
        assert child['data_interpretation']['original_sha256'] == hashlib.sha256(text.encode()).hexdigest()
        for record in (parent,child):
            assert observation_group.original_file(store,record,0).read_bytes() == text.encode()
            assert store.verified_case(record['id'])['status'] == 'DRAFT'
        original = observation_group.original_file(store,child,0)
        original.write_bytes(b'changed')
        with pytest.raises(WorkflowError,match='原始 CSV'):
            store.verified_case(child['id'])
    finally:
        owner.close()


def test_film_diagnostics_require_explicit_sort_and_omission():
    text = 'lambda (μm);Reflectance (%);label\n.813;24;a\n.749;17;b\n.749;17;b\n.722;;c\n'
    args = {'csv_text':text,'model':FILM}
    mapping = {'x':0,'y':1,'x_unit':'um','y_unit':'percent','meaning_confirmed':True}
    initial = csv_intake.preview({**args,'mapping':mapping})['mapped']
    assert not initial['can_save'] and {row for issue in initial['issues'] for row in issue['rows']} == {3,4,5}
    with pytest.raises(WorkflowError,match='理由'):
        csv_intake.preview({**args,'mapping':{**mapping,'sort':True,'exclude_rows':[4,5]}})
    after = csv_intake.preview({**args,'mapping':{**mapping,'sort':True,'exclude_rows':[4,5],'exclude_reason':'重复记录和缺测'}})['mapped']
    assert after['can_save'] and after['original_rows'] == 4 and after['mapped_rows'] == 2
    assert after['excluded_rows'] == [4,5] and [entry['values'] for entry in after['samples']] == [[749,.17],[813,.24]]
    assert after['original_sha256'] == initial['original_sha256']


def test_pixel_and_fwhm_labels_require_physical_observations():
    with pytest.raises(WorkflowError,match='像素'):
        csv_intake.preview({'model':SLIT,'csv_text':'pixel,relative intensity\n1,.5\n',
                           'mapping':{'x':0,'y':1,'x_unit':'mm','y_unit':'relative'}})
    with pytest.raises(WorkflowError,match='半高全宽'):
        csv_intake.preview({'model':GAUSSIAN,'csv_text':'z_mm,FWHM (um)\n0,11\n',
                           'mapping':{'x':0,'y':1,'x_unit':'mm','y_unit':'um','width_kind':'diameter','meaning_confirmed':True}})
