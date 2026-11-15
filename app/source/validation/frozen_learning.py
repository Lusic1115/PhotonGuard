"""Evaluate fixed new phrasings separately from the development journeys."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import threading

from photonguard.workflow import atomic_json, make_server
from validation.continuous_learning_evidence import Lesson, RANGE, question, slit_plan
from validation.workbench_client import Workbench


def evaluate(api, case, item):
    kind = item['kind']
    if kind == 'goal':
        answer = api.api('/learning/slit/preview', {'text': item['text'], 'teaching': True})
        actual = {'status': answer['status']}
        if answer['status'] == 'PREVIEW':
            design = answer['design']['restriction']
            actual['restriction'] = design['kind']
            if design['kind'] == 'catalog':
                actual['values_um'] = design['values_um']
        else:
            actual['question_count'] = len(answer['questions'])
        return actual
    if kind == 'edit':
        answer = question(api, item['text'], case)
        p = answer['prediction']
        return {'can_prepare': p['can_prepare'] if p else False,
                'changes': {row['field']: row['after'] for row in p['changes'] if row['state'] == 'CHANGE'} if p else {},
                'solver_calls': answer['solver_calls']}
    lesson = Lesson(api)
    if item.get('prior'):
        lesson.ask(item['prior'])
    answer = lesson.ask(item['text'])
    return {'conclusion': (answer.get('conclusion') or {}).get('id'), 'depth': answer['depth'],
            'turns': answer['session']['turn_count'], 'solver_calls': answer['solver_calls']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw = args.inputs.read_bytes()
    data = json.loads(raw)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    log = {'input_sha256': hashlib.sha256(raw).hexdigest(),
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
           'provider_requests': 0, 'scope': 'NEW_EXPRESSIONS_LOCAL_HTTP', 'cases': []}
    with make_server(out / 'workspace', port=0, allow_workers=True) as server:
        loop = threading.Thread(target=server.serve_forever, daemon=True)
        loop.start()
        try:
            api = Workbench(server)
            case = api.solve(slit_plan(api, RANGE))
            for item in data['cases']:
                actual = evaluate(api, case, item)
                log['cases'].append({'id': item['id'], 'input': item['text'], 'actual': actual,
                                     'expected': item['expected'],
                                     'passed': all(actual.get(key) == value for key, value in item['expected'].items())})
        finally:
            server.shutdown()
            loop.join()
    log['passed'] = sum(item['passed'] for item in log['cases'])
    log['total'] = len(log['cases'])
    atomic_json(out / 'frozen-results.json', log)
    print(json.dumps({key: log[key] for key in ('passed', 'total', 'input_sha256', 'source_commit')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
