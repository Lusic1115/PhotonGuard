"""Run the finals journeys through the local HTTP and worker entry points.

Pass --live only for the dated, owner-approved ¥10 validation batch. The key
is read into memory. The charge ledger is independent of the output directory.
"""

import argparse
import getpass
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from photonguard.backend import ApprovedRates, BackendAuthorization
from photonguard.cny_budget import CnyApproval
from photonguard.core import digest
from photonguard.experiments.ai_binding import account_check
from photonguard.experiments.inverse_models import FILM, SLIT
from photonguard.gui_batch_ledger import GuiBatchLedger
from photonguard.workflow import WorkflowError, atomic_json, make_server, write_once
from photonguard.workflow_model import wire

PRICE_DATE = '2026-11-11'
CNY_PRICES = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
USD_PRICES = 'https://api-docs.deepseek.com/quick_start/pricing/'
MODEL = 'deepseek-flash'
MAX_REQUESTS = 24
MAX_CNY = '10'
AUTH_REFERENCE = 'photon-guard-finals-20261111-owner-approved'
GOAL_TEXT = ('我要按目标设计单缝。光源是氦氖激光的红线（633 nm），薄透镜规格 f=200 mm。'
             '中央亮区全宽要 10±0.2 mm，缝宽限制15—35 μm。采用教学模型。')
LOCAL_GOAL_TEXT = ('我要按目标设计单缝。波长633 nm，焦距200 mm，中央亮区全宽10±0.2 mm，'
                   '缝宽15—35 μm。采用教学模型。')


class FinalsLedger(GuiBatchLedger):
    """Reuse audited reservation and settlement with a distinct authorization."""

    def _binding(self):
        approval = self.authorization
        expected = (approval.approved is True and approval.reference == AUTH_REFERENCE and approval.model == MODEL
                    and approval.max_calls == MAX_REQUESTS and self.cny.total_budget_cny == MAX_CNY
                    and self.path.name == 'finals-charge-ledger.json')
        if not expected:
            raise ValueError('Finals approval or ledger name differs')
        return digest({'reference': AUTH_REFERENCE, 'model': MODEL, 'attempt_limit': MAX_REQUESTS,
                       'cny': vars(self.cny), 'usd': vars(approval.rates), 'usd_cap': approval.total_budget_usd})


def charge_ledger():
    directory = Path.home() / '.local/share/photonguard-finals-20261111'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    rates = ApprovedRates('0.3', '1.2', PRICE_DATE, USD_PRICES)
    approval = BackendAuthorization(True, AUTH_REFERENCE, MODEL, '1.50', MAX_REQUESTS, rates)
    cny = CnyApproval(MAX_CNY, '2', '8', PRICE_DATE, CNY_PRICES)
    ledger = FinalsLedger(approval, directory / 'finals-charge-ledger.json', cny)
    marker = directory / 'approval.json'
    binding = ledger._binding()
    with ledger._locked():
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8'))['binding'] != binding:
                raise ValueError('Stored finals approval differs')
            ledger._read(require_existing=True)
        elif ledger.path.exists():
            raise ValueError('Finals ledger lacks its original approval record')
        else:
            write_once(marker, {'binding': binding, 'budget_cny': MAX_CNY, 'reference': AUTH_REFERENCE})
            ledger._write(ledger._empty())
    return ledger


class FinalsClient:
    def __init__(self, key, receipts, ledger):
        self._key = key
        self.ledger = ledger
        self.receipts = receipts
        self.receipts.mkdir()

    def preflight(self):
        state = self.ledger.snapshot()
        _, worst = self.ledger.worst_quote()
        if (state['halted'] or state['pending'] or state['attempts'] >= MAX_REQUESTS
                or Decimal(state['reserved_cny']) + worst > Decimal(MAX_CNY)):
            raise WorkflowError('真实验收的额度或扣费状态需核对。', 409)

    def status(self):
        return {'live_enabled': True, 'model': MODEL, 'mode': 'FINALS_VALIDATION'}

    def complete(self, request_id, payload):
        self.preflight()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id) or wire.credential_risk(payload, self._key):
            raise WorkflowError('请求资料未通过发送前核对。', 422)
        if payload.get('tools') or payload.get('tool_choice'):
            raise WorkflowError('这批验收只使用文本和结构化结果。', 422)
        folder = self.receipts / request_id
        folder.mkdir()
        write_once(folder / 'request.json', payload)
        result = wire.complete(payload, self._key, self.ledger, require_existing_ledger=True)
        write_once(folder / 'receipt.json', result)
        if result['audit']['status'] != 'ASSISTANT_RETURNED':
            raise WorkflowError('真实请求未完整返回，已保留预留和回执。', 502)
        return result


class Workbench:
    def __init__(self, server, *, bootstrap='/bootstrap'):
        self.base = f'http://127.0.0.1:{server.server_port}'
        self.opener = build_opener(ProxyHandler({}))
        self.csrf = self.api(bootstrap)['csrf']

    def api(self, path, data=None):
        headers, body = {}, None
        if data is not None:
            headers = {'Origin': self.base, 'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'}
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        req = Request(self.base + '/api/experiments' + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=70) as response:
                payload = response.read()
                return payload if response.headers.get_content_type() == 'application/zip' else json.loads(payload)
        except HTTPError as exc:
            message = json.loads(exc.read())['error']
            raise RuntimeError(f'HTTP {exc.code} {path}: {message}') from None

    def action(self, case, kind, **data):
        return self.api('/cases/' + case['id'] + '/action', {'type': kind, 'expected_revision': case['revision'], **data})

    def settled(self, case):
        for _ in range(320):
            if case['status'] != 'RUNNING':
                if case['status'] == 'FAILED':
                    raise RuntimeError('求解器报告失败，证据保存在本轮工作区。')
                return case
            time.sleep(.2)
            case = self.api('/cases/' + case['id'])
        raise RuntimeError('计算尚未在验收等待窗口内完成。')

    def solve(self, draft, inverse=False):
        plan = draft['inverse_plan' if inverse else 'plan']
        return self.settled(self.action(draft, 'inverse_run' if inverse else 'run', approve=True, plan_sha256=plan['sha256']))

    def ask(self, question, case=None, live=False):
        data = {'question': question, 'mode': 'live' if live else 'local'}
        if case:
            data['record_id'] = case['id']
        if live:
            data['approve_data'] = True
        return self.api('/learning/ask', data)


def ask_and_record(api, log, name, question, case, live):
    reply = api.ask(question, case, live)
    log[name] = reply
    if live and reply['mode'] != 'LIVE_AI':
        raise RuntimeError(name + ' 未通过实时回答核对；已保存模型回执。')
    if reply['solver_calls'] != 0:
        raise RuntimeError('助教答复意外产生了求解。')
    return reply


def fit_observations(api, case):
    entries = [{key: group[key] for key in ('conditions', 'scale', 'scale_source', 'observation') if key in group}
               | {'approved': True} for group in case['groups']]
    draft = api.action(case, 'inverse_plan', settings=case['inverse_settings'], entries=entries)
    return api.solve(draft, inverse=True)


def forward_summary(case):
    result = case['result']
    return {'id': case['id'], 'plan_sha256': case['plan']['sha256'], 'result_sha256': digest(result),
            'resources': result['resources'], 'winner_id': result['winner_id'],
            'candidates': [{key: row.get(key) for key in ('id', 'parameters', 'goal', 'checks', 'metrics', 'engine')}
                           for row in result['candidates']]}


def journeys(api, root, log, live):
    if live:
        ask_and_record(api, log, 'smoke_concept', '什么是瑞利长度？', None, True)
        ask_and_record(api, log, 'smoke_width', '请区分中央亮区全宽、单侧第一暗纹和半高全宽。', None, True)

    text = GOAL_TEXT if live else LOCAL_GOAL_TEXT
    intake = api.api('/learning/slit/inspect', {'text': text, 'mode': 'live' if live else 'local', 'approve_data': live})
    log['slit_intake'] = intake
    if live and (intake['source'] != 'LIVE_AI_REVIEWED_QUOTES' or intake['values'].get('wavelength_nm') != 633
                 or intake['values'].get('focal_length_mm') != 200):
        raise RuntimeError('AI 目标整理缺少来源核对通过的波长或焦距。')
    body = {'text': text, 'teaching': True, 'values': intake['values'], 'excerpts': intake['quotes']}
    preview = api.api('/learning/slit/preview', body)
    log['slit_preview'] = preview
    if preview['status'] != 'PREVIEW' or preview['design']['ideal_width_um'] != 25.32:
        raise RuntimeError('单缝解析候选不符合验收条件。')
    created = api.api('/learning/slit/plan', {**body, 'preview_sha256': preview['preview_sha256']})['case']
    first = api.solve(created)
    log['slit_first'] = forward_summary(first)
    ask_and_record(api, log, 'slit_explanation', '为什么缝越宽，中央亮区越窄？峰值更亮的候选为何没选？', first, live)
    question = '532 nm 会怎样？先解释，不要计算。'
    p = ask_and_record(api, log, 'slit_prediction', question, first, live)['prediction']
    assert p['can_prepare'] and abs(p['scale'] - 532 / 633) < 1e-12
    assert api.api('/cases/' + first['id'])['revision'] == first['revision']
    question = '现在实际试532 nm，其他不变。'
    change = api.api('/learning/followup', {'question': question, **p['source'], 'candidate_id': p['candidate_id'],
                     'prediction_sha256': p['sha256'], 'confirm_change': True})['case']
    assert change['status'] == 'PLANNED' and change['result'] is None
    second = api.solve(change)
    log['slit_comparison'] = forward_summary(second)
    assert log['slit_comparison']['candidates'][0]['parameters']['wavelength_nm'] == 532
    (root / 'slit-family.zip').write_bytes(api.api('/cases/' + second['id'] + '/family-export'))

    film = fit_observations(api, api.api('/inverse', {'model': FILM, 'teaching': True}))
    film = api.action(film, 'inverse_measurement_plan', constraints={'wavelengths_nm': [660, 780, 850]})
    ask_and_record(api, log, 'film_explanation', '四分之一波膜的厚度怎样定义？为什么本次单波长反射率有多个膜厚，如何补测？', film, live)
    plan = film['measurement_plan']
    child = api.action(film, 'inverse_append', teaching=True, approve=True,
                       proposal_id=plan['recommended_id'], proposal_sha256=plan['sha256'])
    combined = fit_observations(api, child)
    log['film_comparison'] = {'first_id': film['id'], 'second_id': combined['id'],
                              'initial_modes': len(film['inverse_result']['modes']),
                              'joint_modes': len(combined['inverse_result']['modes']),
                              'wavelengths_nm': [group['dataset']['x_nm'] for group in combined['groups']],
                              'first_resources': film['inverse_result']['resources'],
                              'joint_resources': combined['inverse_result']['resources']}
    (root / 'film-family.zip').write_bytes(api.api('/cases/' + combined['id'] + '/family-export'))
    joined = ask_and_record(api, log, 'film_joint_explanation', '为什么加入新波长的反射率后，膜厚候选变了？', combined, live)
    prior = joined['result']['facts']['acquisition_comparison']
    assert prior['source']['record_id'] == film['id'] and prior['previous_modes'] == log['film_comparison']['initial_modes']

    gaussian = api.api('/cases', {'kind': 'teaching', 'template_id': 'gaussian-catalog'})
    gaussian = api.solve(api.action(gaussian, 'plan', mode='rules'))
    ask_and_record(api, log, 'gaussian_explanation', '为什么三片焦距不同的透镜在同一屏幕上有不同光斑？本次的束腰、半径和直径怎样理解？', gaussian, live)
    log['gaussian_run'] = forward_summary(gaussian)
    (root / 'gaussian-family.zip').write_bytes(api.api('/cases/' + gaussian['id'] + '/family-export'))

    double = api.api('/cases', {'kind': 'teaching', 'template_id': 'diffraction-phase'})
    double = api.solve(api.action(double, 'plan', mode='rules'))
    log['double_slit_forward'] = forward_summary(double)
    explanation = ask_and_record(api, log, 'double_slit_explanation', '双缝的缝距和相对相位分别怎样改变条纹？', double, False)
    assert explanation['result']['candidates'][0]['parameters']['aperture'] == 'double_slit'
    assert any('解析条纹周期' in line for line in explanation['grounded_facts'])
    (root / 'double-slit-forward.zip').write_bytes(api.api('/cases/' + double['id'] + '/family-export'))
    reverse = fit_observations(api, api.api('/inverse', {'model': SLIT, 'teaching': True}))
    explanation = ask_and_record(api, log, 'double_slit_inverse', '反演出的双缝宽度和缝距为什么分别影响包络和条纹？', reverse, False)
    assert {'slit_width_um', 'slit_separation_um'} <= explanation['result']['facts']['estimate'].keys()
    (root / 'double-slit-inverse.zip').write_bytes(api.api('/cases/' + reverse['id'] + '/family-export'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    ledger = client = None
    log = {'status': 'STARTED', 'mode': 'REAL_DEEPSEEK' if args.live else 'LOCAL',
           'started_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    try:
        if args.live:
            if datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat() != PRICE_DATE:
                raise ValueError('已核对价格的日期变化，需在现有账本上更新报价。')
            print('READY_FOR_KEY', flush=True)
            key = getpass.getpass('DeepSeek API Key: ') if sys.stdin.isatty() else sys.stdin.readline().strip()
            if not re.fullmatch(r'sk-[A-Za-z0-9]{20,100}', key):
                raise ValueError('未收到有效格式的内存凭据。')
            account_check(key)
            ledger = charge_ledger()
            log['ledger_before'] = ledger.snapshot()
            client = FinalsClient(key, out / 'provider-requests', ledger)
            del key
        with make_server(out / 'workspace', port=0, allow_workers=True) as server:
            if client:
                server.store.product_client = client
            loop = threading.Thread(target=server.serve_forever, daemon=True)
            loop.start()
            try:
                journeys(Workbench(server), out, log, args.live)
                log['status'] = 'PASS'
            finally:
                server.shutdown()
                loop.join()
    except (WorkflowError, ValueError, RuntimeError, AssertionError, OSError) as exc:
        log.update(status='FAIL', failure_type=type(exc).__name__, failure=str(exc))
    finally:
        if ledger:
            log['ledger_after'] = ledger.snapshot()
        atomic_json(out / 'journeys.json', log)
        print(json.dumps({key: log[key] for key in ('status', 'mode', 'source_commit', 'failure', 'ledger_after') if key in log},
                         ensure_ascii=False), flush=True)
    return 0 if log['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
