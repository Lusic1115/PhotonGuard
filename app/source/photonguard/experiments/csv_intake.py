"""Preview and record explicit column interpretations for inverse CSV files."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from decimal import Decimal, InvalidOperation
from statistics import median

from ..core import digest
from ..workflow import WorkflowError
from ..workflow_model import wire
from .inverse_models import FILM, GAUSSIAN, MODELS, SLIT
from .observations import MAX_BYTES


NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z', re.ASCII)
LENGTH = {'m': Decimal('1000'), 'mm': Decimal('1'), 'um': Decimal('.001'), 'nm': Decimal('.000001')}
ROW_LIMIT = 5000
COLUMN_LIMIT = 64


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _value(cell):
    text = cell.strip()
    if len(text) > 128 or not NUMBER.fullmatch(text):
        return None
    number = float(text)
    if not math.isfinite(number) or abs(number) > 1e12:
        return None
    try:
        precise = Decimal(text)
    except InvalidOperation:
        return None
    return precise if number or precise == 0 else None


def _table(text):
    if not isinstance(text, str) or not text or len(text.encode('utf-8')) > MAX_BYTES or wire.credential_risk(text):
        raise WorkflowError('请使用不含密钥的 UTF-8 CSV，最大 2 MiB。')
    source = text.removeprefix('\ufeff')
    try:
        delimiter = next((sep for sep in (',', ';', '\t') if len(next(csv.reader(io.StringIO(source, newline=''), delimiter=sep), [])) >= 2), ',')
        reader = csv.reader(io.StringIO(source, newline=''), delimiter=delimiter, strict=True)
        header = [name.strip() for name in next(reader, [])]
        if not 2 <= len(header) <= COLUMN_LIMIT:
            raise WorkflowError('CSV 表头须有 2–64 列。请检查分隔符。')
        rows = []
        while len(rows) <= ROW_LIMIT:
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            rows.append((line, row))
        if not 1 <= len(rows) <= ROW_LIMIT:
            raise WorkflowError('CSV 需要 1–5000 条数据记录。')
    except csv.Error:
        raise WorkflowError('CSV 引号或字段结构无效，请核对原文件。') from None
    return header, rows, delimiter


def _unit(name):
    raw = name.strip().lower().replace('μ', 'u').replace('µ', 'u')
    if re.search(r'像素|(?:^|[^a-z])(?:px|pixels?)(?:$|[^a-z])', raw):
        return 'pixel'
    if '%' in raw or re.search(r'百分比|percent|(?:^|_)pct(?:$|_)', raw):
        return 'percent'
    match = re.search(r'(?:^|[^a-z])(nanometers?|micrometers?|millimeters?|nm|um|mm|m)(?:$|[^a-z])', raw)
    if match:
        token = match[1]
        return next((unit for prefix, unit in (('nano','nm'),('micro','um'),('milli','mm')) if token.startswith(prefix)), token)
    return next((unit for text, unit in (('纳米','nm'),('微米','um'),('毫米','mm')) if text in raw), None)


def _suggest(header, model):
    def unique(pattern, skip=None):
        indices = [i for i, name in enumerate(header) if i != skip and re.search(pattern, name, re.I)]
        return indices[0] if len(indices) == 1 else None

    if model == FILM:
        x = unique(r'波长|wavelength|^lambda\b|^λ')
        y = unique(r'反射率|reflectance|^R(?:$|[\s_%([])')
        result = {'x': x, 'y': y, 'y_unit': 'percent' if y is not None and _unit(header[y]) == 'percent' else None}
        if y is not None and header[y].strip() == 'reflectance_calibrated':
            result['y_unit'] = 'fraction'
    elif model == GAUSSIAN:
        x = unique(r'位置|坐标|position|^z(?:$|[\s_([])')
        sd = unique(r'标准差|不确定度|(?:^|[\s_])(?:sd|sigma)(?:$|[\s_([])')
        y = unique(r'半径|直径|radius|diameter|束宽', sd)
        width = None
        if y is not None and re.search(r'1\s*/\s*e|1e2|1/e²', header[y], re.I):
            width = 'diameter' if re.search(r'diameter|直径',header[y],re.I) else 'radius'
        result = {'x': x, 'y': y, 'sigma': sd, 'width_kind': width,
                  'y_unit': _unit(header[y]) if y is not None else None,
                  'sigma_unit': _unit(header[sd]) if sd is not None else None}
    else:
        x = unique(r'位置|坐标|position|^x(?:$|[\s_([])')
        y = unique(r'强度|光强|intensity')
        result = {'x': x, 'y': y, 'y_unit': 'relative' if y is not None else None}
    result['x_unit'] = _unit(header[x]) if x is not None else None
    return result


def _overview(header, rows, delimiter, model):
    columns = []
    for index, name in enumerate(header):
        cells = [row[index] if index < len(row) else '' for _, row in rows]
        values = [value for cell in cells if (value := _value(cell)) is not None]
        missing = sum(not cell.strip() for cell in cells)
        columns.append({'index': index, 'name': name[:180] or f'未命名列 {index+1}', 'unit_hint': _unit(name),
                        'numeric': len(values), 'missing': missing, 'nonnumeric': len(rows)-len(values)-missing,
                        'range': [float(min(values)), float(max(values))] if values else None})
    malformed = [line for line, row in rows if len(row) != len(header)]
    return {'columns': columns, 'rows': len(rows), 'delimiter': delimiter, 'shape_errors': malformed[:20],
            'shape_error_count': len(malformed),
            'samples': [{'line': line, 'cells': [cell[:100] for cell in row[:COLUMN_LIMIT]]} for line, row in rows[:5]],
            'suggestion': _suggest(header, model)}


def _checked_mapping(mapping, header, rows, model):
    base = {'x', 'y', 'x_unit', 'y_unit', 'sort', 'exclude_rows', 'exclude_reason'}
    allowed = base | ({'width_kind','sigma','sigma_unit','meaning_confirmed'} if model == GAUSSIAN else
                      {'meaning_confirmed'} if model == FILM else set())
    if not isinstance(mapping, dict) or set(mapping) - allowed or not {'x','y','x_unit','y_unit'} <= set(mapping):
        raise WorkflowError('请核对选中的列和单位。')
    indices = [mapping['x'], mapping['y']]
    if model == GAUSSIAN and mapping.get('sigma') is not None:
        indices.append(mapping['sigma'])
    if any(type(i) is not int or not 0 <= i < len(header) for i in indices) or len(set(indices)) != len(indices):
        raise WorkflowError('每个物理量须对应不同的现有列。')
    if mapping['x_unit'] not in LENGTH or _unit(header[indices[0]]) == 'pixel':
        raise WorkflowError('位置或波长须采用物理长度。像素需要经过校准的坐标列。')
    if model == GAUSSIAN:
        if (mapping.get('width_kind') not in ('radius', 'diameter') or mapping.get('meaning_confirmed') is not True
                or re.search(r'FWHM|半高全宽', header[indices[1]], re.I)):
            raise WorkflowError('请确认这是 1/e² 强度半径或直径。半高全宽需要另行提供所需口径的数据。')
        if mapping['y_unit'] not in LENGTH or len(indices) == 3 and mapping.get('sigma_unit') not in LENGTH:
            raise WorkflowError('请分别确认宽度与所用标准差的长度单位。')
    elif model == FILM:
        if mapping['y_unit'] not in ('fraction', 'percent') or mapping.get('meaning_confirmed') is not True:
            raise WorkflowError('请确认所选列为已校准反射率，并选择 0–1 或百分比。')
    elif mapping['y_unit'] != 'relative':
        raise WorkflowError('双缝输入需要相对光强列，拟合前请明确各次采集的尺度。')
    removed = mapping.get('exclude_rows', [])
    if (not isinstance(removed, list) or any(type(line) is not int for line in removed)
            or len(set(removed)) != len(removed) or not set(removed) <= {line for line, _ in rows}):
        raise WorkflowError('排除项须为原始文件中存在的行号。')
    reason = mapping.get('exclude_reason', '')
    if (not isinstance(reason, str) or len(reason) > 300 or wire.credential_risk(reason)
            or removed and not reason.strip() or type(mapping.get('sort', False)) is not bool):
        raise WorkflowError('请写明明确排除原始行的理由（最多 300 字）。')
    return indices, set(removed)


def _outliers(values):
    if len(values) < 7:
        return []
    local = []
    for left, current, right in zip(values, values[1:], values[2:]):
        a,b,c = (tuple(map(float, row[1])) for row in (left,current,right))
        if a[0] < b[0] < c[0]:
            expected = a[1] + (c[1]-a[1])*(b[0]-a[0])/(c[0]-a[0])
            local.append((current[0], abs(b[1]-expected)))
    if not local:
        return []
    scale = median(v for _, v in local)
    ys = [float(row[1][1]) for row in values]
    cutoff = max(10*scale, .18*(max(ys)-min(ys)), 1e-12)
    return [line for line, value in local if value > cutoff][:20]


def _analysis(text, model, mapping):
    if model not in MODELS:
        raise WorkflowError('请选择已有的反向分析模型。')
    header, rows, delimiter = _table(text)
    report = _overview(header, rows, delimiter, model)
    report['original_sha256'] = sha(text.encode('utf-8'))
    if mapping is None:
        return report, None
    indices, removed = _checked_mapping(mapping, header, rows, model)
    x_factor = LENGTH[mapping['x_unit']] / (LENGTH['nm'] if model == FILM else LENGTH['mm'])
    y_factor = Decimal('.01') if mapping['y_unit'] == 'percent' else Decimal(1)
    factors = [x_factor, y_factor]
    if model == GAUSSIAN:
        radius = Decimal('.5') if mapping['width_kind'] == 'diameter' else Decimal(1)
        factors[1] = LENGTH[mapping['y_unit']] / LENGTH['um'] * radius
        if len(indices) == 3:
            factors.append(LENGTH[mapping['sigma_unit']] / LENGTH['um'] * radius)
    canonical_header = (['wavelength_nm','reflectance_calibrated'] if model == FILM else
                        ['z_mm','radius_1e2_um'] + (['radius_sd_um'] if len(indices) == 3 else []) if model == GAUSSIAN else
                        ['position_mm','intensity_relative'])
    values, invalid, invalid_range = [], [], []
    for line, row in rows:
        if line in removed:
            continue
        cells = [_value(row[index]) if index < len(row) else None for index in indices]
        if len(row) != len(header) or None in cells:
            invalid.append(line)
            continue
        pair = [cell*factor for cell, factor in zip(cells, factors)]
        if (any(abs(v) > 10**8 for v in pair) or model == FILM and not (500<=pair[0]<=2000 and 0<=pair[1]<=1)
                or model == GAUSSIAN and any(v <= 0 for v in pair[1:])):
            invalid_range.append(line)
        else:
            values.append((line,pair))
    if mapping.get('sort'):
        values.sort(key=lambda row: row[1][0])
    duplicates, unordered, seen = [], [], set()
    for index, (line, row) in enumerate(values):
        # Downstream estimators represent observations as float64.
        x = float(row[0])
        if x in seen:
            duplicates.append(line)
        seen.add(x)
        if index and x < float(values[index-1][1][0]):
            unordered.append(line)
    issues = []
    for affected, text_label in ((invalid,'有缺项、非数值或列数不符'),(invalid_range,'超出当前模型的数值范围'),
                                (duplicates,'坐标重复，须核对独立采集或明确排除重复行'),(unordered,'坐标顺序颠倒，可预览按坐标排序')):
        if affected:
            issues.append({'text':text_label, 'rows':affected[:20], 'count':len(affected)})
    minimum, maximum = (1,61) if model == FILM else (20,5000) if model == SLIT else (1,5000)
    if not minimum <= len(values) <= maximum:
        issues.append({'text':f'当前模型需要 {minimum}–{maximum} 条可用记录。','rows':[],'count':len(values)})
    warnings = []
    for index, key in zip(indices, ('x_unit','y_unit','sigma_unit')):
        hint = _unit(header[index])
        if hint and hint != mapping.get(key):
            warnings.append(f'第 {index+1} 列表头提示 {hint}，当前选择 {mapping.get(key)}。请核对设备或数据说明。')
    if model == GAUSSIAN:
        label = header[mapping['y']]
        convention = ('diameter' if re.search(r'直径|diameter',label,re.I) else
                      'radius' if re.search(r'半径|radius',label,re.I) else None)
        if convention and convention != mapping['width_kind']:
            warnings.append('表头的半径／直径字样与当前解释不同。请根据采集说明确认口径。')
    outliers = _outliers(values)
    if outliers:
        warnings.append('相邻记录间疑似突变，核对原始数据或采集记录。默认保留这些点。')
    transformations = [{'source_column': i, 'target_column': key, 'factor': str(factor)}
                       for i,key,factor in zip(indices,canonical_header,factors)]
    mapped = {'mapping':mapping,'transformations':transformations,'original_sha256':report['original_sha256'],
              'original_columns':report['columns'],'original_rows':len(rows),'mapped_rows':len(values),
              'excluded_rows':sorted(removed),'sorted':bool(mapping.get('sort')),'issues':issues,'warnings':warnings,
              'outlier_rows':outliers,'columns':canonical_header,'samples':[{'line': line,'values':[float(v) for v in row]} for line,row in values[:6]],
              'can_save':not issues}
    if issues:
        return {**report,'mapped':mapped}, None
    stream = io.StringIO(newline='')
    writer = csv.writer(stream,lineterminator='\n');writer.writerow(canonical_header)
    writer.writerows([str(value.normalize()) for value in row] for _,row in values)
    canonical = stream.getvalue()
    if len(canonical.encode('utf-8')) > MAX_BYTES:
        raise WorkflowError('转换后的分析 CSV 超过大小限制。')
    mapped['analysis_sha256'] = sha(canonical.encode('utf-8'))
    mapped['sha256'] = digest({'model':model,'mapped':mapped})
    return {**report,'mapped':mapped},canonical


def preview(data):
    if not {'csv_text','model'} <= set(data) <= {'csv_text','model','mapping'}:
        raise WorkflowError('请指定要预览的 CSV 与分析模型。')
    return _analysis(data['csv_text'], data['model'], data.get('mapping'))[0]


def materialize(text, model, mapping, confirmed):
    report, canonical = _analysis(text, model, mapping)
    mapped = report.get('mapped', {})
    if canonical is None or not confirmed or confirmed != mapped.get('sha256'):
        raise WorkflowError('列映射或数据已变化，请重新预览并确认。', 409)
    audit = {key:value for key,value in mapped.items() if key not in ('samples','issues','can_save','sha256')}
    audit['model'] = model
    audit['preview_sha256'] = confirmed
    return canonical,audit


def verify(audit, original, canonical):
    if (audit['sha256'] != digest({key:value for key,value in audit.items() if key!='sha256'})
            or audit['original_sha256'] != sha(original) or audit['analysis_sha256'] != sha(canonical)):
        raise WorkflowError('原始 CSV 或列解释记录发生变化。', 409)
