"""Shared local HTTP client for product checks and examples."""
import json
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from photonguard.core import digest
from photonguard.experiments.inverse_models import FILM

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
