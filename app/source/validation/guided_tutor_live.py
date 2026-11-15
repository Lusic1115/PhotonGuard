"""Check generated explanations through HTTP on two saved teaching results."""

import argparse
from datetime import datetime
from decimal import Decimal
import getpass
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from zoneinfo import ZoneInfo

from photonguard.backend import BackendAuthorization
from photonguard.core import digest
from photonguard.gui_batch_ledger import GuiBatchLedger
from photonguard.workflow import WorkflowError, atomic_json, make_server
from validation.finals_evidence import FinalsClient, MAX_CNY, MODEL, PRICE_DATE, Workbench, account_check, charge_ledger


class GuidedLedger(GuiBatchLedger):
    def __init__(self, authorization, path, cny, prior):
        super().__init__(authorization, path, cny)
        self.prior_sha256 = digest(prior)

    def _binding(self):
        return digest({'authorization': vars(self.authorization) | {'rates': vars(self.authorization.rates)},
                       'cny': vars(self.cny), 'carried_window': self.prior_sha256})


def continued_ledger():
    original = charge_ledger()
    prior = original.snapshot()
    if prior['pending'] or prior['halted'] or prior['attempts'] != original.authorization.max_calls:
        raise ValueError('The previous request window must be settled and closed before continuing.')
    authorization = BackendAuthorization(True, 'photonguard-finals-guided-existing-cny-cap', MODEL,
                                         original.authorization.total_budget_usd, 1_000_000, original.authorization.rates)
    path = original.path.with_name('guided-finals-cumulative.json')
    ledger = GuidedLedger(authorization, path, original.cny, prior)
    origin = ledger._empty() | {key: prior[key] for key in prior if key != 'approval_sha256'}
    with ledger._locked():
        if not path.exists():
            ledger._write(origin)
        current = ledger._read(require_existing=True)
        if current['attempts'] < prior['attempts'] or Decimal(current['settled_cny']) < Decimal(prior['settled_cny']):
            raise ValueError('The cumulative ledger is missing the previous window.')
    return ledger, prior


class WindowClient(FinalsClient):
    def __init__(self, key, receipts, ledger, limit):
        super().__init__(key, receipts, ledger)
        self.ceiling = ledger.snapshot()['attempts'] + limit
        ledger.reservation_guard = self.guard

    def guard(self, state, next_cny):
        if state['attempts'] >= self.ceiling or next_cny > Decimal(MAX_CNY):
            raise WorkflowError('This model window has reached its limit.', 409)

    def preflight(self):
        state = self.ledger.snapshot()
        _, worst = self.ledger.worst_quote()
        if state['halted'] or state['pending']:
            raise WorkflowError('The previous model request requires reconciliation.', 409)
        self.guard(state, Decimal(state['reserved_cny']) + worst)


QUESTIONS = (
    ('直观', '换成532 nm时这幅图会怎样？我刚学光学，用直观的说法，先别写公式。'),
    ('推导', '如果把波长改成532 nm，能用当前理想焦平面关系推导图样为什么会缩小吗？'),
    ('读图', '如果改用532 nm，请只解释图上右侧第一暗纹会去哪。'),
    ('纠错', '波长改成532 nm后，我以为暗纹离中心更远。错在什么地方？'),
    ('类比', '如果改成532 nm，能用生活类比讲变化，并说明类比哪里不适用？'),
    ('具体参数', '若用532 nm，保持当前候选的缝宽和焦距，用保存条件和解析预览给具体例子。'),
    ('两版对照', '这张实算图为什么比上一版窄？根据两版保存结果解释。'),
    ('继续追问', '还是没懂，用横轴上两侧暗纹的位置关系讲，再给一个可以观察的办法。'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ui_workspace', type=Path)
    parser.add_argument('browser_evidence', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--start', type=int, choices=range(len(QUESTIONS)), default=0)
    parser.add_argument('--count', type=int, choices=range(1, len(QUESTIONS) + 1), default=len(QUESTIONS))
    args = parser.parse_args()
    questions = QUESTIONS[args.start:args.start + args.count]
    args.output.mkdir(parents=True, exist_ok=False)
    output = {'requested': [label for label, _ in questions], 'turns': [],
              'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    ledger = None
    try:
        if datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat() != PRICE_DATE:
            raise ValueError('Check today’s provider tariffs before live dispatch.')
        ledger, output['historical_window'] = continued_ledger()
        output['ledger_before'] = ledger.snapshot()
        ids = json.loads(args.browser_evidence.read_text(encoding='utf-8'))['desktop']
        records = [ids['first'], ids['child']]
        root = args.output / 'workspace'
        for record in records:
            if not re.fullmatch('[a-f0-9]{32}', record):
                raise ValueError('Invalid saved record identity.')
            shutil.copytree(args.ui_workspace / 'experiments' / record, root / 'experiments' / record)
        print('READY_FOR_KEY', flush=True)
        key = getpass.getpass('DeepSeek API Key: ')
        if not re.fullmatch(r'sk-[A-Za-z0-9]{20,100}', key):
            raise ValueError('No valid in-memory key was provided.')
        account_check(key)
        client = WindowClient(key, args.output / 'provider-requests', ledger, len(questions))
        del key
        with make_server(root, port=0, allow_workers=True) as server:
            server.store.product_client = client
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                api = Workbench(server)
                original, changed = (api.api('/cases/' + ident) for ident in records)
                assert original['family_id'] == changed['family_id'] and original['id'] != changed['id']
                sid, previous = uuid.uuid4().hex, None
                for label, question in questions:
                    cross = label in ('两版对照', '继续追问')
                    active = changed if cross else original
                    body = {'question': question, 'mode': 'live', 'approve_data': True, 'record_id': active['id'],
                            'result_sha256': digest(active['result']), 'learning_task_id': active['family_id'],
                            'session_id': sid, 'turn_id': uuid.uuid4().hex, 'expected_turn_id': previous}
                    if cross:
                        body['compare_to'] = {'record_id': original['id'], 'result_sha256': digest(original['result'])}
                    reply = api.api('/learning/ask', body)
                    previous = reply['session']['last_turn_id']
                    output['turns'].append({'label': label, 'request': body, 'response': reply})
                    print(label, reply['mode'], reply.get('explanation_source'), flush=True)
                    if reply['mode'] != 'LIVE_AI' or reply['explanation_source'] != 'LLM_GROUNDED_EXPLANATION':
                        break
                    assert reply['solver_calls'] == 0 and reply['source']['result_sha256'] == body['result_sha256']
                    assert reply.get('comparison', {}).get('reference', {}).get('record_id') == (original['id'] if cross else None)
            finally:
                server.shutdown()
                thread.join()
        output['status'] = 'PASS' if len(output['turns']) == len(questions) and all(
            row['response']['mode'] == 'LIVE_AI' for row in output['turns']) else 'REVIEW'
    except (ValueError, WorkflowError, RuntimeError, OSError, AssertionError) as exc:
        output.update(status='FAIL', error_type=type(exc).__name__, error=str(exc))
    finally:
        if ledger:
            output['ledger_after'] = ledger.snapshot()
            output['requests'] = output['ledger_after']['attempts'] - output['ledger_before']['attempts']
            output['estimated_cny'] = str(Decimal(output['ledger_after']['settled_cny']) - Decimal(output['ledger_before']['settled_cny']))
        atomic_json(args.output / 'guided-live.json', output)
        print(json.dumps({key: output[key] for key in ('status', 'requests', 'estimated_cny', 'error') if key in output}, ensure_ascii=False), flush=True)
    return 0 if output['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
