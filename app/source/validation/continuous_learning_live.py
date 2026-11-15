"""Validate four continuous learning turns within the existing finals ledger."""

import argparse
from datetime import datetime
from decimal import Decimal
import getpass
import json
from pathlib import Path
import re
import subprocess
import threading
from zoneinfo import ZoneInfo

from photonguard.workflow import WorkflowError, atomic_json, make_server
from validation.continuous_learning_evidence import Lesson, RANGE, slit_plan
from validation.finals_evidence import (
    GOAL_TEXT, MAX_CNY, MAX_REQUESTS, PRICE_DATE, FinalsClient, Workbench, account_check,
    charge_ledger, forward_summary,
)


def validate(api, output, log):
    lesson = Lesson(api)
    live = {'mode': 'live', 'approve_data': True}
    first = lesson.ask('瑞利距离对应光束变成什么样？', **live)
    second = lesson.ask('没懂，能换个例子吗？', **live)
    log['theory_turns'] = lesson.turns
    log['checks']['continued_theory'] = (
        first['mode'] == second['mode'] == 'LIVE_AI'
        and first['conclusion']['id'] == second['conclusion']['id'] == 'gaussian_rayleigh'
        and first['intro'] != second['intro'] and second['session']['turn_count'] == 2
        and first['solver_calls'] == second['solver_calls'] == 0
    )

    intake = api.api('/learning/slit/inspect', {'text': GOAL_TEXT, **live})
    log['slit_intake'] = intake
    expected = {'wavelength_nm': 633, 'focal_length_mm': 200}
    log['checks']['source_bound_intake'] = intake['source'] == 'LIVE_AI_REVIEWED_QUOTES' and all(
        intake['values'].get(key) == value and bool(intake['quotes'].get(key)) and intake['quotes'][key] in GOAL_TEXT
        for key, value in expected.items()
    )
    if log['checks']['source_bound_intake']:
        body = {'text': GOAL_TEXT, 'teaching': True, 'values': intake['values'], 'excerpts': intake['quotes']}
        preview = api.api('/learning/slit/preview', body)
        draft = api.api('/learning/slit/plan', {**body, 'preview_sha256': preview['preview_sha256']})['case']
    else:
        draft = slit_plan(api, RANGE)
    case = api.solve(draft)
    log['solved'] = forward_summary(case)
    result_lesson = Lesson(api, case)
    explained = result_lesson.ask('保持当前缝片，532 nm 会怎样？先解释，不要计算。', **live)
    log['result_turn'] = explained
    log['checks']['bound_result_explanation'] = (
        explained['mode'] == 'LIVE_AI' and explained['source']['record_id'] == case['id']
        and explained['source']['result_sha256'] == case['experiment_record']['result_sha256']
        and explained['prediction']['direction'] == 'DECREASE' and explained['solver_calls'] == 0
    )
    (output / 'live-source-family.zip').write_bytes(api.api('/cases/' + case['id'] + '/family-export'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    ledger = None
    log = {'status': 'STARTED', 'checks': {}, 'request_window': 4, 'cumulative_limit_cny': MAX_CNY,
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    try:
        if datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat() != PRICE_DATE:
            raise ValueError('已核对的价格日期已变化。')
        ledger = charge_ledger()
        log['ledger_before'] = initial = ledger.snapshot()
        if initial['pending'] or initial['halted'] or MAX_REQUESTS - initial['attempts'] != 4:
            raise ValueError('连续助教验收需要原有账本中预留的四次请求窗口。')
        print('READY_FOR_KEY', flush=True)
        key = getpass.getpass('DeepSeek API Key: ')
        if not re.fullmatch(r'sk-[A-Za-z0-9]{20,100}', key):
            raise ValueError('未收到有效格式的内存凭据。')
        account_check(key)
        client = FinalsClient(key, output / 'provider-requests', ledger)
        del key
        with make_server(output / 'workspace', port=0, allow_workers=True) as server:
            server.store.product_client = client
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                validate(Workbench(server), output, log)
                log['status'] = 'PASS' if all(log['checks'].values()) else 'REVIEW'
            finally:
                server.shutdown()
                thread.join()
    except (WorkflowError, ValueError, RuntimeError, TypeError, KeyError, OSError) as exc:
        log.update(status='FAIL', failure_type=type(exc).__name__, failure=str(exc))
    finally:
        if ledger and 'ledger_before' in log:
            log['ledger_after'] = final = ledger.snapshot()
            log['provider_requests'] = final['live_provider_attempts'] - initial['live_provider_attempts']
            log['delta_settled_cny'] = str(Decimal(final['settled_cny']) - Decimal(initial['settled_cny']))
        atomic_json(output / 'live-learning.json', log)
        print(json.dumps({key: log[key] for key in ('status', 'checks', 'failure', 'provider_requests',
                                                   'delta_settled_cny', 'ledger_after') if key in log},
                         ensure_ascii=False), flush=True)
    return 0 if log['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
