"""Receipts for single local workbench submissions and connection recovery."""

import json
import re

from ..core import digest
from ..workflow import WorkflowError, atomic_json


HEADER = 'X-PhotonGuard-Operation'
_ID = re.compile(r'[a-f0-9]{32}')
_ACTION = re.compile(r'/api/experiments/cases/([a-f0-9]{32})/action')


def _receipt_path(store, ident):
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise WorkflowError('提交编号无效。')
    return store.owner.safe('workbench-operations', ident + '.json')


def _write(path, receipt):
    path.parent.mkdir(exist_ok=True)
    atomic_json(path, receipt)


def submit(store, ident, route, data, execute):
    """Register before dispatch. Reusing a registered ID never dispatches again."""
    path = _receipt_path(store, ident)
    source = _ACTION.fullmatch(route)
    receipt = {'status': 'PROCESSING', 'request_sha256': digest([route, data]),
               'source_id': source[1] if source else None}
    with store.operation_lock:
        if path.is_file():
            previous = json.loads(path.read_bytes())
            if previous['status'] == 'NOT_STARTED':
                raise WorkflowError('这次提交已关闭。请重新核对后再操作。', 409)
            if previous['request_sha256'] != receipt['request_sha256']:
                raise WorkflowError('提交编号已绑定另一项操作。', 409)
            raise WorkflowError('这次提交已登记，请读取回执。', 409)
        _write(path, receipt)
        store.active_operations.add(ident)
    try:
        result, status = execute()
        record = result.get('case', result)
        record_id = record.get('id') if isinstance(record, dict) else None
        receipt.update(status='SAVED' if record_id else 'NO_RECORD', record_id=record_id)
        _write(path, receipt)
        return result, status
    except WorkflowError as exc:
        receipt.update(status='REJECTED', error=str(exc))
        _write(path, receipt)
        raise
    finally:
        # A failure to serialize or persist a result leaves PROCESSING on disk.
        # Without an active call that status requires inspection.
        with store.operation_lock:
            store.active_operations.discard(ident)


def recover(store, ident):
    """Read a receipt, or close the ID before a delayed request can start."""
    path = _receipt_path(store, ident)
    with store.operation_lock:
        if not path.is_file():
            _write(path, {'status': 'NOT_STARTED'})
            return {'status': 'NOT_STARTED'}
        receipt = json.loads(path.read_bytes())
        if receipt['status'] == 'PROCESSING' and ident not in store.active_operations:
            receipt['status'] = 'REVIEW'
        return {key: receipt[key] for key in ('status', 'source_id', 'record_id', 'error') if key in receipt}
